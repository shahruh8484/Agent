import json

from fbadsagent.facebook.ad_library import AdLibraryError
from fbadsagent.landing.reference_fetcher import ReferenceFetchError
from fbadsagent.llm.provider import LLMError
from fbadsagent.models import AgentProduct, CampaignPlan
from fbadsagent.web.agent_runner import run_agent_for_product
from fbadsagent.web.creative_store import CreativeStore
from fbadsagent.web.landing_store import LandingPageStore


class ScriptedLLM:
    """Returns a different canned JSON response per call, in order."""

    def __init__(self, responses: list[str], image_response: str = ""):
        self._responses = list(responses)
        self._image_response = image_response
        self.calls: list[tuple[str, str]] = []
        self.image_calls: list[tuple[str, str, list[str]]] = []

    def generate(self, system: str, prompt: str, max_tokens: int = 1024) -> str:
        self.calls.append((system, prompt))
        return self._responses.pop(0)

    def generate_with_images(
        self, system: str, prompt: str, image_paths: list[str], max_tokens: int = 1024
    ) -> str:
        self.image_calls.append((system, prompt, image_paths))
        return self._image_response


def make_agent_product(**overrides) -> AgentProduct:
    defaults = dict(
        id="p1",
        name="Wireless Earbuds Pro",
        description="Noise-cancelling wireless earbuds with 40h battery life",
        price=49.99,
        daily_budget=25.0,
        keywords=["wireless earbuds"],
        fb_ad_account_id="act_123",
        cpa_network="traff-hub",
        campaign_hash="camp-hash-1",
    )
    defaults.update(overrides)
    return AgentProduct(**defaults)


def make_stores(tmp_path):
    landing_store = LandingPageStore(tmp_path / "landing_pages.json")
    creative_store = CreativeStore(tmp_path / "creative_sets.json")
    return landing_store, creative_store


def test_run_agent_success(settings, tmp_path, mocker):
    mock_ad_library_cls = mocker.patch("fbadsagent.web.agent_runner.AdLibraryClient")
    mock_ad_library_cls.return_value.search_competitor_ads.side_effect = AdLibraryError("no token")

    scripted_llm = ScriptedLLM(
        [
            json.dumps(
                [
                    {
                        "primary_text": "40 hours of pure sound.",
                        "headline": "All-Day Battery",
                        "description": "Shop now",
                        "call_to_action": "SHOP_NOW",
                        "image_prompt": "earbuds on a charging case",
                    }
                ]
            ),
            json.dumps(
                {
                    "headline": "Never Charge Twice a Day",
                    "subheadline": "40 hours of premium sound.",
                    "benefits": ["40h battery", "ANC"],
                    "cta_text": "Get Yours",
                }
            ),
        ]
    )
    mocker.patch("fbadsagent.web.agent_runner.get_llm_provider", return_value=scripted_llm)

    mock_ads_client_cls = mocker.patch("fbadsagent.web.agent_runner.FacebookAdsClient")
    mock_ads_client_cls.return_value.create_campaign.return_value = CampaignPlan(
        campaign_name="Wireless Earbuds Pro - AI Agent Campaign",
        objective="OUTCOME_SALES",
        daily_budget=25.0,
        status="PAUSED",
        dry_run=False,
        campaign_id="fb-campaign-123",
    )

    landing_store, creative_store = make_stores(tmp_path)
    product = make_agent_product()

    result = run_agent_for_product(product, settings, landing_store, creative_store, "manual")

    assert result.status == "success"
    assert result.fb_campaign_id == "fb-campaign-123"
    assert "PAUSED" in result.message
    assert result.triggered_by == "manual"

    # creative set was actually saved
    assert result.creative_set_id is not None
    saved_sets = creative_store.list_sets()
    assert len(saved_sets) == 1
    assert saved_sets[0].creatives[0].headline == "All-Day Battery"

    # landing page was actually published, wired to the product's CPA network
    assert result.landing_page_slug is not None
    page = landing_store.get_page(result.landing_page_slug)
    assert page is not None
    assert page.headline == "Never Charge Twice a Day"
    assert page.cpa_network == "traff-hub"
    assert page.campaign_hash == "camp-hash-1"

    # Facebook campaign was created against the product's ad account, not
    # settings.fb_ad_account_id
    create_kwargs = mock_ads_client_cls.call_args
    used_settings = create_kwargs.args[0]
    assert used_settings.fb_ad_account_id == "act_123"


def test_run_agent_no_llm_configured(settings, tmp_path, mocker):
    mocker.patch(
        "fbadsagent.web.agent_runner.get_llm_provider", side_effect=LLMError("no api key")
    )
    landing_store, creative_store = make_stores(tmp_path)
    product = make_agent_product()

    result = run_agent_for_product(product, settings, landing_store, creative_store)

    assert result.status == "error"
    assert "No LLM configured" in result.message
    assert creative_store.list_sets() == []


def test_run_agent_missing_fb_ad_account(settings, tmp_path, mocker):
    mocker.patch("fbadsagent.web.agent_runner.AdLibraryClient").return_value.search_competitor_ads.side_effect = AdLibraryError(
        "no token"
    )
    scripted_llm = ScriptedLLM(
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
    mocker.patch("fbadsagent.web.agent_runner.get_llm_provider", return_value=scripted_llm)

    landing_store, creative_store = make_stores(tmp_path)
    product = make_agent_product(fb_ad_account_id="")

    result = run_agent_for_product(product, settings, landing_store, creative_store)

    assert result.status == "error"
    assert "No Facebook ad account" in result.message
    # creative + landing page were still produced before the FB step
    assert result.creative_set_id is not None
    assert result.landing_page_slug is not None


def test_run_agent_uses_reference_landing_pages(settings, tmp_path, mocker):
    mocker.patch("fbadsagent.web.agent_runner.AdLibraryClient").return_value.search_competitor_ads.side_effect = AdLibraryError(
        "no token"
    )
    scripted_llm = ScriptedLLM(
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
    mocker.patch("fbadsagent.web.agent_runner.get_llm_provider", return_value=scripted_llm)

    mock_fetch = mocker.patch(
        "fbadsagent.web.agent_runner.fetch_reference_text",
        return_value="Competitor headline: Never Charge Twice",
    )

    mock_ads_client_cls = mocker.patch("fbadsagent.web.agent_runner.FacebookAdsClient")
    mock_ads_client_cls.return_value.create_campaign.return_value = CampaignPlan(
        campaign_name="x",
        objective="OUTCOME_SALES",
        daily_budget=25.0,
        status="PAUSED",
        dry_run=False,
        campaign_id="fb-campaign-123",
    )

    landing_store, creative_store = make_stores(tmp_path)
    product = make_agent_product(
        reference_landing_urls=["https://competitor.com/offer"]
    )

    result = run_agent_for_product(product, settings, landing_store, creative_store)

    assert result.status == "success"
    mock_fetch.assert_called_once_with("https://competitor.com/offer")
    landing_prompt = scripted_llm.calls[1][1]
    assert "Competitor headline: Never Charge Twice" in landing_prompt


def test_run_agent_uses_reference_screenshots(settings, tmp_path, mocker):
    mocker.patch("fbadsagent.web.agent_runner.AdLibraryClient").return_value.search_competitor_ads.side_effect = AdLibraryError(
        "no token"
    )
    scripted_llm = ScriptedLLM(
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
        ],
        image_response="Headline: Never Charge Twice a Day. CTA: Shop Now.",
    )
    mocker.patch("fbadsagent.web.agent_runner.get_llm_provider", return_value=scripted_llm)

    mock_ads_client_cls = mocker.patch("fbadsagent.web.agent_runner.FacebookAdsClient")
    mock_ads_client_cls.return_value.create_campaign.return_value = CampaignPlan(
        campaign_name="x",
        objective="OUTCOME_SALES",
        daily_budget=25.0,
        status="PAUSED",
        dry_run=False,
        campaign_id="fb-campaign-123",
    )

    landing_store, creative_store = make_stores(tmp_path)
    screenshot_path = tmp_path / "screenshot.png"
    screenshot_path.write_bytes(b"fake-png-bytes")
    product = make_agent_product(reference_screenshot_paths=[str(screenshot_path)])

    result = run_agent_for_product(product, settings, landing_store, creative_store)

    assert result.status == "success"
    assert len(scripted_llm.image_calls) == 1
    _, _, image_paths = scripted_llm.image_calls[0]
    assert image_paths == [str(screenshot_path)]

    landing_prompt = scripted_llm.calls[1][1]
    assert "Headline: Never Charge Twice a Day. CTA: Shop Now." in landing_prompt


def test_run_agent_tolerates_reference_fetch_failure(settings, tmp_path, mocker):
    mocker.patch("fbadsagent.web.agent_runner.AdLibraryClient").return_value.search_competitor_ads.side_effect = AdLibraryError(
        "no token"
    )
    scripted_llm = ScriptedLLM(
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
    mocker.patch("fbadsagent.web.agent_runner.get_llm_provider", return_value=scripted_llm)
    mocker.patch(
        "fbadsagent.web.agent_runner.fetch_reference_text",
        side_effect=ReferenceFetchError("timed out"),
    )

    mock_ads_client_cls = mocker.patch("fbadsagent.web.agent_runner.FacebookAdsClient")
    mock_ads_client_cls.return_value.create_campaign.return_value = CampaignPlan(
        campaign_name="x",
        objective="OUTCOME_SALES",
        daily_budget=25.0,
        status="PAUSED",
        dry_run=False,
        campaign_id="fb-campaign-123",
    )

    landing_store, creative_store = make_stores(tmp_path)
    product = make_agent_product(reference_landing_urls=["https://unreachable.example"])

    result = run_agent_for_product(product, settings, landing_store, creative_store)

    assert result.status == "success"


def test_run_agent_tolerates_screenshot_description_failure(settings, tmp_path, mocker):
    mocker.patch("fbadsagent.web.agent_runner.AdLibraryClient").return_value.search_competitor_ads.side_effect = AdLibraryError(
        "no token"
    )

    class FailingImageLLM(ScriptedLLM):
        def generate_with_images(self, system, prompt, image_paths, max_tokens=1024):
            raise LLMError("vision not supported")

    scripted_llm = FailingImageLLM(
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
    mocker.patch("fbadsagent.web.agent_runner.get_llm_provider", return_value=scripted_llm)

    mock_ads_client_cls = mocker.patch("fbadsagent.web.agent_runner.FacebookAdsClient")
    mock_ads_client_cls.return_value.create_campaign.return_value = CampaignPlan(
        campaign_name="x",
        objective="OUTCOME_SALES",
        daily_budget=25.0,
        status="PAUSED",
        dry_run=False,
        campaign_id="fb-campaign-123",
    )

    landing_store, creative_store = make_stores(tmp_path)
    screenshot_path = tmp_path / "screenshot.png"
    screenshot_path.write_bytes(b"fake-png-bytes")
    product = make_agent_product(reference_screenshot_paths=[str(screenshot_path)])

    result = run_agent_for_product(product, settings, landing_store, creative_store)

    assert result.status == "success"


def test_run_agent_fb_campaign_creation_fails(settings, tmp_path, mocker):
    mocker.patch("fbadsagent.web.agent_runner.AdLibraryClient").return_value.search_competitor_ads.side_effect = AdLibraryError(
        "no token"
    )
    scripted_llm = ScriptedLLM(
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
    mocker.patch("fbadsagent.web.agent_runner.get_llm_provider", return_value=scripted_llm)

    mock_ads_client_cls = mocker.patch("fbadsagent.web.agent_runner.FacebookAdsClient")
    mock_ads_client_cls.return_value.create_campaign.side_effect = RuntimeError("token expired")

    landing_store, creative_store = make_stores(tmp_path)
    product = make_agent_product()

    result = run_agent_for_product(product, settings, landing_store, creative_store)

    assert result.status == "error"
    assert "Facebook campaign creation failed" in result.message
    assert "token expired" in result.message
