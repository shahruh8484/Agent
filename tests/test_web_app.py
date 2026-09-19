import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fbadsagent.models import AccountInsightsSummary, DailyInsight
from fbadsagent.web.app import create_app
from tests.conftest import FakeLLM


class FakeInsightsClient:
    def __init__(self, summary: AccountInsightsSummary | None = None, error: Exception | None = None):
        self._summary = summary
        self._error = error
        self.calls: list[tuple[str, str, str | None]] = []

    def get_account_insights(
        self, account_id: str, date_preset: str = "last_30d", access_token: str | None = None
    ):
        self.calls.append((account_id, date_preset, access_token))
        if self._error:
            raise self._error
        return self._summary


def make_summary() -> AccountInsightsSummary:
    return AccountInsightsSummary(
        account_id="act_111",
        account_name="Test Account",
        date_preset="last_7d",
        daily=[DailyInsight(date="2026-09-01", spend=10.0, clicks=5, impressions=200, leads=1, ctr=2.5, cpc=2.0, cpl=10.0)],
        total_spend=10.0,
        total_clicks=5,
        total_impressions=200,
        total_leads=1,
        avg_ctr=2.5,
        avg_cpc=2.0,
        avg_cpl=10.0,
    )


def build_client(web_settings, insights_client=None):
    app = create_app(settings=web_settings, insights_client=insights_client or FakeInsightsClient(make_summary()))
    return TestClient(app)


def test_root_redirects_to_login_when_unauthenticated(web_settings):
    client = build_client(web_settings)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_login_with_wrong_password_rejected(web_settings):
    client = build_client(web_settings)
    response = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert response.status_code == 401
    assert "Неверное имя пользователя или пароль" in response.text


def test_login_then_access_dashboard_and_api(web_settings):
    fake_insights = FakeInsightsClient(make_summary())
    client = build_client(web_settings, insights_client=fake_insights)

    login = client.post(
        "/login", data={"username": "admin", "password": "correct-horse"}, follow_redirects=False
    )
    assert login.status_code == 302
    assert login.headers["location"] == "/"

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "act_111" in dashboard.text

    accounts = client.get("/api/accounts")
    assert accounts.status_code == 200
    assert accounts.json() == {"accounts": ["act_111", "act_222"]}

    insights = client.get("/api/insights", params={"account_id": "act_111", "date_preset": "last_7d"})
    assert insights.status_code == 200
    body = insights.json()
    assert body["total_spend"] == 10.0
    assert body["total_leads"] == 1
    assert fake_insights.calls == [("act_111", "last_7d", "test-token")]

    logout = client.get("/logout", follow_redirects=False)
    assert logout.status_code == 302
    after_logout = client.get("/", follow_redirects=False)
    assert after_logout.status_code == 302
    assert after_logout.headers["location"] == "/login"


def test_api_insights_requires_login(web_settings):
    client = build_client(web_settings)
    response = client.get("/api/insights", params={"account_id": "act_111"})
    assert response.status_code == 401


def test_api_insights_surfaces_provider_errors(web_settings):
    from fbadsagent.web.insights_client import InsightsError

    fake_insights = FakeInsightsClient(error=InsightsError("token expired"))
    client = build_client(web_settings, insights_client=fake_insights)
    client.post("/login", data={"username": "admin", "password": "correct-horse"})

    response = client.get("/api/insights", params={"account_id": "act_111"})
    assert response.status_code == 400
    assert "token expired" in response.json()["error"]


def test_create_app_requires_secret_key(web_settings):
    web_settings.secret_key = ""
    with pytest.raises(RuntimeError):
        create_app(settings=web_settings)


def login(client):
    client.post("/login", data={"username": "admin", "password": "correct-horse"})


def test_accounts_page_requires_login(web_settings):
    client = build_client(web_settings)
    response = client.get("/accounts", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_accounts_page_lists_seeded_accounts(web_settings):
    client = build_client(web_settings)
    login(client)

    response = client.get("/accounts")
    assert response.status_code == 200
    assert "act_111" in response.text
    assert "act_222" in response.text


def test_add_and_remove_account(web_settings):
    client = build_client(web_settings)
    login(client)

    add = client.post(
        "/accounts/add",
        data={"account_id": "999888777", "account_name": "New Store"},
        follow_redirects=False,
    )
    assert add.status_code == 302
    assert add.headers["location"] == "/accounts"

    page = client.get("/accounts")
    assert "act_999888777" in page.text
    assert "New Store" in page.text

    remove = client.post(
        "/accounts/delete", data={"account_id": "act_999888777"}, follow_redirects=False
    )
    assert remove.status_code == 302
    page_after = client.get("/accounts")
    assert "act_999888777" not in page_after.text


def test_sync_accounts_adds_fetched_accounts(web_settings, mocker):
    mocker.patch(
        "fbadsagent.web.app.list_ad_accounts",
        return_value=[
            {"id": "act_555", "name": "Synced Store"},
            {"id": "act_111", "name": "Already Tracked"},
        ],
    )
    client = build_client(web_settings)
    login(client)

    sync = client.post("/accounts/sync", follow_redirects=False)
    assert sync.status_code == 302
    assert sync.headers["location"] == "/accounts?synced=2"

    page = client.get("/accounts")
    assert "act_555" in page.text
    assert "Synced Store" in page.text


def test_sync_accounts_surfaces_provider_errors(web_settings, mocker):
    from fbadsagent.web.insights_client import InsightsError

    mocker.patch(
        "fbadsagent.web.app.list_ad_accounts", side_effect=InsightsError("no token")
    )
    client = build_client(web_settings)
    login(client)

    sync = client.post("/accounts/sync", follow_redirects=False)
    assert sync.status_code == 302
    assert sync.headers["location"] == "/accounts?error=no%20token"


def test_add_agent_product_parses_reference_urls(web_settings, tmp_path):
    from fbadsagent.web.product_store import ProductStore

    product_store = ProductStore(tmp_path / "agent_products.json")
    app = create_app(
        settings=web_settings,
        insights_client=FakeInsightsClient(make_summary()),
        product_store=product_store,
    )
    client = TestClient(app)
    login(client)

    client.post(
        "/agent/add",
        data={
            "name": "Wireless Earbuds Pro",
            "description": "Noise-cancelling wireless earbuds",
            "reference_landing_urls": "https://a.com/x\nhttps://b.com/y\n\n",
        },
        follow_redirects=False,
    )

    products = product_store.list_products()
    assert len(products) == 1
    assert products[0].reference_landing_urls == ["https://a.com/x", "https://b.com/y"]


def test_add_agent_product_stores_landing_style(web_settings, tmp_path):
    from fbadsagent.web.product_store import ProductStore

    product_store = ProductStore(tmp_path / "agent_products.json")
    app = create_app(
        settings=web_settings,
        insights_client=FakeInsightsClient(make_summary()),
        product_store=product_store,
    )
    client = TestClient(app)
    login(client)

    client.post(
        "/agent/add",
        data={
            "name": "Glycofort",
            "description": "Blood sugar support supplement",
            "landing_style": "quiz",
        },
        follow_redirects=False,
    )

    products = product_store.list_products()
    assert len(products) == 1
    assert products[0].landing_style == "quiz"


def test_add_agent_product_defaults_language_to_uzbek(web_settings, tmp_path):
    from fbadsagent.web.product_store import ProductStore

    product_store = ProductStore(tmp_path / "agent_products.json")
    app = create_app(
        settings=web_settings,
        insights_client=FakeInsightsClient(make_summary()),
        product_store=product_store,
    )
    client = TestClient(app)
    login(client)

    client.post(
        "/agent/add",
        data={"name": "Glycofort", "description": "Blood sugar support supplement"},
        follow_redirects=False,
    )

    products = product_store.list_products()
    assert len(products) == 1
    assert products[0].target_language == "Uzbek"


def test_add_agent_product_stores_custom_language(web_settings, tmp_path):
    from fbadsagent.web.product_store import ProductStore

    product_store = ProductStore(tmp_path / "agent_products.json")
    app = create_app(
        settings=web_settings,
        insights_client=FakeInsightsClient(make_summary()),
        product_store=product_store,
    )
    client = TestClient(app)
    login(client)

    client.post(
        "/agent/add",
        data={
            "name": "Wireless Earbuds Pro",
            "description": "desc",
            "target_language": "English",
        },
        follow_redirects=False,
    )

    products = product_store.list_products()
    assert len(products) == 1
    assert products[0].target_language == "English"


def test_add_agent_product_saves_uploaded_screenshots(web_settings, tmp_path):
    from fbadsagent.web.product_store import ProductStore

    product_store = ProductStore(tmp_path / "agent_products.json")
    app = create_app(
        settings=web_settings,
        insights_client=FakeInsightsClient(make_summary()),
        product_store=product_store,
    )
    client = TestClient(app)
    login(client)

    client.post(
        "/agent/add",
        data={"name": "Wireless Earbuds Pro", "description": "desc"},
        files=[
            ("reference_screenshots", ("shot1.png", b"fake-png-bytes-1", "image/png")),
            ("reference_screenshots", ("shot2.png", b"fake-png-bytes-2", "image/png")),
        ],
        follow_redirects=False,
    )

    products = product_store.list_products()
    assert len(products) == 1
    saved_paths = products[0].reference_screenshot_paths
    assert len(saved_paths) == 2
    for path in saved_paths:
        assert Path(path).exists()


def test_cpa_networks_page_requires_login(web_settings):
    client = build_client(web_settings)
    response = client.get("/cpa-networks", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_add_and_remove_cpa_network(web_settings):
    client = build_client(web_settings)
    login(client)

    add = client.post(
        "/cpa-networks/add",
        data={"name": "traff-hub", "base_url": "https://traff-hub.com/api", "api_key": "secret123"},
        follow_redirects=False,
    )
    assert add.status_code == 302
    assert add.headers["location"] == "/cpa-networks"

    page = client.get("/cpa-networks")
    assert "traff-hub" in page.text
    assert "traff-hub.com/api" in page.text
    assert "secret123" not in page.text  # key itself never rendered, only masked

    remove = client.post("/cpa-networks/delete", data={"name": "traff-hub"}, follow_redirects=False)
    assert remove.status_code == 302
    page_after = client.get("/cpa-networks")
    assert "Сетей пока нет" in page_after.text


def test_test_cpa_network_unsupported_network(web_settings):
    client = build_client(web_settings)
    login(client)
    client.post("/cpa-networks/add", data={"name": "some-other-network", "api_key": "k"})

    response = client.post("/cpa-networks/test", data={"name": "some-other-network"})
    assert response.status_code == 200
    assert "не реализован API-клиент" in response.text


def test_test_cpa_network_traffhub_success(web_settings, mocker):
    fake_response = mocker.Mock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"data": []}
    mocker.patch("fbadsagent.integrations.traffhub.requests.post", return_value=fake_response)

    client = build_client(web_settings)
    login(client)
    client.post("/cpa-networks/add", data={"name": "traff-hub", "api_key": "real-key"})

    response = client.post("/cpa-networks/test", data={"name": "traff-hub"})
    assert response.status_code == 200
    assert "соединение установлено" in response.text


def test_test_cpa_network_traffhub_failure(web_settings, mocker):
    fake_response = mocker.Mock()
    fake_response.status_code = 403
    fake_response.text = "Invalid api_key"
    mocker.patch("fbadsagent.integrations.traffhub.requests.post", return_value=fake_response)

    client = build_client(web_settings)
    login(client)
    client.post("/cpa-networks/add", data={"name": "traff-hub", "api_key": "bad-key"})

    response = client.post("/cpa-networks/test", data={"name": "traff-hub"})
    assert response.status_code == 200
    assert "Invalid api_key" in response.text


def test_landing_pages_page_requires_login(web_settings):
    client = build_client(web_settings)
    response = client.get("/landing-pages", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def make_landing_page(**overrides):
    from fbadsagent.models import LandingPageConfig

    defaults = dict(
        slug="wireless-earbuds-pro",
        title="Wireless Earbuds Pro",
        headline="Hear Every Detail",
        subheadline="Premium audio, all day long.",
        benefits=["40h battery", "ANC", "Waterproof"],
        cta_text="Get Yours",
        cpa_network="traff-hub",
        campaign_hash="6c9c0e1f",
    )
    defaults.update(overrides)
    return LandingPageConfig(**defaults)


def test_remove_landing_page(web_settings, tmp_path):
    from fbadsagent.web.landing_store import LandingPageStore

    landing_store = LandingPageStore(tmp_path / "landing_pages.json")
    landing_store.add_page(make_landing_page())
    app = create_app(
        settings=web_settings,
        insights_client=FakeInsightsClient(make_summary()),
        landing_store=landing_store,
    )
    client = TestClient(app)
    login(client)

    page = client.get("/landing-pages")
    assert "/lp/wireless-earbuds-pro" in page.text
    assert "traff-hub" in page.text
    assert "6c9c0e1f" in page.text

    remove = client.post(
        "/landing-pages/delete", data={"slug": "wireless-earbuds-pro"}, follow_redirects=False
    )
    assert remove.status_code == 302
    page_after = client.get("/landing-pages")
    assert "Лендингов пока нет" in page_after.text


def test_public_landing_page_renders_without_login(web_settings, tmp_path):
    from fbadsagent.web.landing_store import LandingPageStore

    landing_store = LandingPageStore(tmp_path / "landing_pages.json")
    landing_store.add_page(make_landing_page())
    app = create_app(
        settings=web_settings,
        insights_client=FakeInsightsClient(make_summary()),
        landing_store=landing_store,
    )
    anon_client = TestClient(app)  # unauthenticated — public page needs no login
    response = anon_client.get("/lp/wireless-earbuds-pro")
    assert response.status_code == 200
    assert "Hear Every Detail" in response.text
    assert "40h battery" in response.text


def test_public_quiz_landing_page_renders_quiz_template(web_settings, tmp_path):
    from fbadsagent.models import LandingPageConfig, QuizQuestion
    from fbadsagent.web.landing_store import LandingPageStore

    landing_store = LandingPageStore(tmp_path / "landing_pages.json")
    landing_store.add_page(
        LandingPageConfig(
            slug="glycofort-quiz",
            title="Glycofort",
            headline="A Few Quick Questions",
            subheadline="Find out what fits your routine.",
            cta_text="See My Recommendation",
            cpa_network="traff-hub",
            campaign_hash="6c9c0e1f",
            style="quiz",
            quiz_questions=[QuizQuestion(text="Do you exercise often?", options=["Yes", "No"])],
            quiz_result_message="Glycofort supports normal blood sugar levels.",
        )
    )
    app = create_app(
        settings=web_settings,
        insights_client=FakeInsightsClient(make_summary()),
        landing_store=landing_store,
    )
    client = TestClient(app)

    response = client.get("/lp/glycofort-quiz")

    assert response.status_code == 200
    assert "Do you exercise often?" in response.text
    assert "Glycofort supports normal blood sugar levels." in response.text
    assert "Analyzing your answers" in response.text


def test_public_landing_page_404_when_missing(web_settings):
    client = build_client(web_settings)
    response = client.get("/lp/does-not-exist")
    assert response.status_code == 404


def test_submit_lead_success(web_settings, tmp_path, mocker):
    from fbadsagent.web.landing_store import LandingPageStore

    fake_response = mocker.Mock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"success": True, "transaction_id": "abc"}
    mocker.patch("fbadsagent.integrations.traffhub.requests.post", return_value=fake_response)

    landing_store = LandingPageStore(tmp_path / "landing_pages.json")
    landing_store.add_page(make_landing_page())
    app = create_app(
        settings=web_settings,
        insights_client=FakeInsightsClient(make_summary()),
        landing_store=landing_store,
    )
    client = TestClient(app)
    login(client)
    client.post("/cpa-networks/add", data={"name": "traff-hub", "api_key": "real-key"})

    anon_client = TestClient(app)
    response = anon_client.post(
        "/lp/wireless-earbuds-pro/lead", json={"fio": "Test Test", "phone": "+10000000000"}
    )
    assert response.status_code == 200
    assert response.json() == {"success": True}


def test_submit_lead_missing_network_config(web_settings, tmp_path):
    from fbadsagent.web.landing_store import LandingPageStore

    landing_store = LandingPageStore(tmp_path / "landing_pages.json")
    landing_store.add_page(make_landing_page())
    app = create_app(
        settings=web_settings,
        insights_client=FakeInsightsClient(make_summary()),
        landing_store=landing_store,
    )
    client = TestClient(app)

    response = client.post(
        "/lp/wireless-earbuds-pro/lead", json={"fio": "Test Test", "phone": "+10000000000"}
    )
    assert response.status_code == 400
    assert response.json()["success"] is False


def test_submit_lead_traffhub_error_returns_502(web_settings, tmp_path, mocker):
    from fbadsagent.web.landing_store import LandingPageStore

    fake_response = mocker.Mock()
    fake_response.status_code = 403
    fake_response.text = "Invalid api_key"
    mocker.patch("fbadsagent.integrations.traffhub.requests.post", return_value=fake_response)

    landing_store = LandingPageStore(tmp_path / "landing_pages.json")
    landing_store.add_page(make_landing_page())
    app = create_app(
        settings=web_settings,
        insights_client=FakeInsightsClient(make_summary()),
        landing_store=landing_store,
    )
    client = TestClient(app)
    login(client)
    client.post("/cpa-networks/add", data={"name": "traff-hub", "api_key": "bad-key"})

    response = client.post(
        "/lp/wireless-earbuds-pro/lead", json={"fio": "Test Test", "phone": "+10000000000"}
    )
    assert response.status_code == 502
    assert response.json()["success"] is False


def test_creatives_page_requires_login(web_settings):
    client = build_client(web_settings)
    response = client.get("/creatives", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_creatives_page_shows_seeded_set_and_serves_its_image(web_settings, tmp_path):
    from fbadsagent.models import AdCreativeCopy, GeneratedImage, SavedCreativeSet
    from fbadsagent.web.creative_store import CreativeStore

    data_dir = tmp_path / "data"
    creatives_dir = data_dir / "creatives"
    creatives_dir.mkdir(parents=True)
    image_path = creatives_dir / "variant-1.png"
    image_path.write_bytes(b"fake-png-bytes")

    web_settings.data_dir = str(data_dir)
    creative_store = CreativeStore(data_dir / "creative_sets.json")
    creative_store.add_set(
        SavedCreativeSet(
            id="set1",
            product_name="Wireless Earbuds Pro",
            created_at="2026-09-18 12:00 UTC",
            creatives=[
                AdCreativeCopy(
                    variant_id="v1",
                    primary_text="40 hours of pure sound.",
                    headline="All-Day Battery",
                    description="Shop now",
                    call_to_action="SHOP_NOW",
                )
            ],
            images=[GeneratedImage(variant_id="v1", path=str(image_path), prompt="x", provider="stub")],
        )
    )
    app = create_app(
        settings=web_settings,
        insights_client=FakeInsightsClient(make_summary()),
        creative_store=creative_store,
    )
    client = TestClient(app)
    login(client)

    page = client.get("/creatives")
    assert "Wireless Earbuds Pro" in page.text
    assert "All-Day Battery" in page.text

    image_response = client.get("/creative-assets/variant-1.png")
    assert image_response.status_code == 200

    remove = client.post("/creatives/delete", data={"set_id": "set1"}, follow_redirects=False)
    assert remove.status_code == 302
    page_after = client.get("/creatives")
    assert "Креативов пока нет" in page_after.text


def test_chat_page_requires_login(web_settings):
    client = build_client(web_settings)
    response = client.get("/chat", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_chat_send_requires_login(web_settings):
    client = build_client(web_settings)
    response = client.post("/chat/send", json={"message": "hi"})
    assert response.status_code == 401


def test_chat_send_success(web_settings, mocker):
    fake_llm = FakeLLM(response="Sure, here is a suggestion.")
    mocker.patch("fbadsagent.web.app.get_llm_provider", return_value=fake_llm)

    client = build_client(web_settings)
    login(client)

    response = client.post("/chat/send", json={"message": "What should I do next?"})
    assert response.status_code == 200
    assert response.json() == {"reply": "Sure, here is a suggestion."}

    page = client.get("/chat")
    assert "What should I do next?" in page.text
    assert "Sure, here is a suggestion." in page.text

    # the context summary reaches the LLM (last call — the first is the
    # launch-command classifier, which "Sure, here is a suggestion." fails
    # to parse as JSON and so falls through to the normal reply path)
    system_prompt, prompt = fake_llm.calls[-1]
    assert "Current dashboard state" in system_prompt
    assert "What should I do next?" in prompt


def test_chat_send_empty_message_rejected(web_settings):
    client = build_client(web_settings)
    login(client)
    response = client.post("/chat/send", json={"message": "   "})
    assert response.status_code == 400


def test_chat_send_llm_error(web_settings, mocker):
    from fbadsagent.llm.provider import LLMError

    mocker.patch("fbadsagent.web.app.get_llm_provider", side_effect=LLMError("no key"))
    client = build_client(web_settings)
    login(client)

    response = client.post("/chat/send", json={"message": "hi"})
    assert response.status_code == 400
    assert "no key" in response.json()["error"]


def test_chat_idea_appends_idea_message(web_settings, mocker):
    fake_llm = FakeLLM(response="Try a video creative for this offer.")
    mocker.patch("fbadsagent.web.app.get_llm_provider", return_value=fake_llm)

    client = build_client(web_settings)
    login(client)

    response = client.post("/chat/idea")
    assert response.status_code == 200
    assert response.json() == {"reply": "Try a video creative for this offer."}

    page = client.get("/chat")
    assert "Идея агента" in page.text
    assert "Try a video creative for this offer." in page.text


def test_chat_send_launch_command_for_unknown_product_asks_to_add_it(web_settings, mocker):
    fake_llm = FakeLLM(
        response=json.dumps(
            {
                "is_launch_command": True,
                "product_name": "Glycofort",
                "count": 1,
                "daily_budget": None,
            }
        )
    )
    mocker.patch("fbadsagent.web.app.get_llm_provider", return_value=fake_llm)

    client = build_client(web_settings)
    login(client)

    response = client.post("/chat/send", json={"message": "launch Glycofort"})
    assert response.status_code == 200
    assert "У меня нет продукта с названием" in response.json()["reply"]


def test_chat_send_launch_command_runs_agent_for_matching_product(web_settings, tmp_path, mocker):
    from fbadsagent.facebook.ad_library import AdLibraryError
    from fbadsagent.models import AgentProduct, CampaignPlan
    from fbadsagent.web.product_store import AgentRunLogStore, ProductStore

    class ScriptedLLM:
        def __init__(self, responses):
            self._responses = list(responses)
            self.calls = []

        def generate(self, system, prompt, max_tokens=1024):
            self.calls.append((system, prompt))
            return self._responses.pop(0)

    classifier_llm = FakeLLM(
        response=json.dumps(
            {
                "is_launch_command": True,
                "product_name": "glycofort",
                "count": 1,
                "daily_budget": None,
            }
        )
    )
    mocker.patch("fbadsagent.web.app.get_llm_provider", return_value=classifier_llm)

    pipeline_llm = ScriptedLLM(
        [
            json.dumps(
                [
                    {
                        "primary_text": "x",
                        "headline": "x",
                        "description": "x",
                        "call_to_action": "SHOP_NOW",
                        "image_prompt": "x",
                    }
                ]
            ),
            json.dumps({"headline": "x", "subheadline": "x", "benefits": ["x"], "cta_text": "x"}),
        ]
    )
    mocker.patch("fbadsagent.web.agent_runner.get_llm_provider", return_value=pipeline_llm)
    mocker.patch(
        "fbadsagent.web.agent_runner.AdLibraryClient"
    ).return_value.search_competitor_ads.side_effect = AdLibraryError("no token")
    mock_ads_client_cls = mocker.patch("fbadsagent.web.agent_runner.FacebookAdsClient")
    mock_ads_client_cls.return_value.create_campaign.return_value = CampaignPlan(
        campaign_name="Glycofort - AI Agent Campaign",
        objective="OUTCOME_SALES",
        daily_budget=5.0,
        status="PAUSED",
        dry_run=False,
        campaign_id="fb-campaign-123",
    )

    product_store = ProductStore(tmp_path / "agent_products.json")
    product_store.add_product(
        AgentProduct(
            id="p1",
            name="Glycofort",
            description="Blood sugar support supplement",
            daily_budget=20.0,
            fb_ad_account_id="act_123",
            cpa_network="traff-hub",
            campaign_hash="6c9c0e1f",
        )
    )
    agent_run_log = AgentRunLogStore(tmp_path / "agent_runs.json")
    app = create_app(
        settings=web_settings,
        insights_client=FakeInsightsClient(make_summary()),
        product_store=product_store,
        agent_run_log=agent_run_log,
    )
    client = TestClient(app)
    login(client)

    response = client.post(
        "/chat/send", json={"message": "запусти 1 рк на glycofort с 5 долларами бюджета"}
    )

    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "Успех" in reply
    assert "/lp/" in reply

    runs = agent_run_log.list_runs()
    assert len(runs) == 1
    assert runs[0].triggered_by == "chat"
    assert runs[0].status == "success"


def test_update_access_token_reflected_in_status(web_settings):
    web_settings.fb_access_token = ""  # nothing seeded
    client = build_client(web_settings)
    login(client)

    before = client.get("/accounts")
    assert "не задан" in before.text

    client.post("/accounts/token", data={"access_token": "new-token-123"}, follow_redirects=False)

    after = client.get("/accounts")
    assert "не задан" not in after.text


def test_finance_page_requires_login(web_settings):
    client = build_client(web_settings)
    response = client.get("/finance", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_add_list_and_delete_finance_transaction(web_settings):
    client = build_client(web_settings)
    login(client)

    add = client.post(
        "/finance/add",
        data={
            "type": "expense",
            "amount": "50000",
            "currency": "UZS",
            "category": "Еда",
            "note": "обед",
            "date": "2026-09-10",
        },
        follow_redirects=False,
    )
    assert add.status_code == 302
    assert add.headers["location"] == "/finance"

    page = client.get("/finance")
    assert "Еда" in page.text
    assert "обед" in page.text

    summary = client.get("/api/finance/summary", params={"period": "all"})
    assert summary.status_code == 200
    body = summary.json()
    assert body["expense"] == 50000.0
    assert body["balance"] == -50000.0
    assert body["category_totals"] == {"Еда": 50000.0}


def test_delete_finance_transaction(web_settings):
    from fbadsagent.finance.store import FinanceStore

    finance_store = FinanceStore(Path(web_settings.data_dir) / "finance.json")
    app = create_app(settings=web_settings, insights_client=FakeInsightsClient(make_summary()), finance_store=finance_store)
    client = TestClient(app)
    login(client)

    client.post(
        "/finance/add",
        data={"type": "income", "amount": "1000", "currency": "USD", "category": "Продажи", "note": "", "date": "2026-09-01"},
        follow_redirects=False,
    )
    transaction_id = finance_store.list_transactions()[0].id

    delete = client.post("/finance/delete", data={"transaction_id": transaction_id}, follow_redirects=False)
    assert delete.status_code == 302
    assert finance_store.list_transactions() == []


def test_api_finance_summary_requires_login(web_settings):
    client = build_client(web_settings)
    response = client.get("/api/finance/summary")
    assert response.status_code == 401


def test_finance_summary_filters_by_currency(web_settings):
    client = build_client(web_settings)
    login(client)

    client.post(
        "/finance/add",
        data={"type": "income", "amount": "1000", "currency": "UZS", "category": "X", "note": "", "date": "2026-09-10"},
    )
    client.post(
        "/finance/add",
        data={"type": "income", "amount": "500", "currency": "USD", "category": "Y", "note": "", "date": "2026-09-10"},
    )

    uzs = client.get("/api/finance/summary", params={"period": "all", "currency": "UZS"}).json()
    usd = client.get("/api/finance/summary", params={"period": "all", "currency": "USD"}).json()

    assert uzs["income"] == 1000.0
    assert usd["income"] == 500.0


def test_finance_page_lists_supported_currencies(web_settings):
    client = build_client(web_settings)
    login(client)

    response = client.get("/finance")
    assert "USD" in response.text
    assert "UZS" in response.text
