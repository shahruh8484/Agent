import pytest

from fbadsagent.config import Settings
from fbadsagent.web.insights_client import FacebookInsightsClient, InsightsError


def test_get_account_insights_requires_access_token():
    client = FacebookInsightsClient(Settings(fb_access_token=""))
    with pytest.raises(InsightsError):
        client.get_account_insights("act_123")


def test_get_account_insights_parses_daily_rows_and_totals(mocker, settings):
    fake_response = mocker.Mock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "data": [
            {
                "date_start": "2026-09-01",
                "account_name": "My Ad Account",
                "spend": "10.50",
                "clicks": "20",
                "impressions": "1000",
                "ctr": "2.0",
                "cpc": "0.53",
                "actions": [
                    {"action_type": "lead", "value": "3"},
                    {"action_type": "link_click", "value": "20"},
                ],
            },
            {
                "date_start": "2026-09-02",
                "account_name": "My Ad Account",
                "spend": "5.00",
                "clicks": "10",
                "impressions": "500",
                "ctr": "2.0",
                "cpc": "0.5",
                "actions": [],
            },
        ]
    }
    mocker.patch(
        "fbadsagent.web.insights_client.requests.get", return_value=fake_response
    )

    client = FacebookInsightsClient(settings)
    summary = client.get_account_insights("123", date_preset="last_7d")

    assert summary.account_id == "act_123"
    assert summary.account_name == "My Ad Account"
    assert len(summary.daily) == 2
    assert summary.daily[0].leads == 3
    assert summary.daily[0].cpl == pytest.approx(3.5, rel=1e-3)
    assert summary.daily[1].leads == 0
    assert summary.daily[1].cpl is None

    assert summary.total_spend == pytest.approx(15.5)
    assert summary.total_clicks == 30
    assert summary.total_leads == 3
    assert summary.avg_cpl == pytest.approx(15.5 / 3, rel=1e-3)


def test_get_account_insights_raises_on_error_status(mocker, settings):
    fake_response = mocker.Mock()
    fake_response.status_code = 403
    fake_response.text = "Forbidden"
    mocker.patch(
        "fbadsagent.web.insights_client.requests.get", return_value=fake_response
    )

    client = FacebookInsightsClient(settings)
    with pytest.raises(InsightsError):
        client.get_account_insights("act_123")
