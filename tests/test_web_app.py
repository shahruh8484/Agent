import pytest
from fastapi.testclient import TestClient

from fbadsagent.models import AccountInsightsSummary, DailyInsight
from fbadsagent.web.app import create_app


class FakeInsightsClient:
    def __init__(self, summary: AccountInsightsSummary | None = None, error: Exception | None = None):
        self._summary = summary
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def get_account_insights(self, account_id: str, date_preset: str = "last_30d"):
        self.calls.append((account_id, date_preset))
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
    assert "Invalid username or password" in response.text


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
    assert fake_insights.calls == [("act_111", "last_7d")]

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
