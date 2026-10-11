from finance import fx
from tests.conftest import TODAY
from tests.test_app import login, settings  # noqa: F401


def test_update_rate_applies_adjustment(repo):
    repo.set_setting("rate_adjust_pct", -2)
    rate = fx.update_rate(repo, TODAY, lambda: (12000.0, "10.10.2026"))
    assert rate == 11760.0
    assert repo.usd_uzs_rate() == 11760.0
    assert repo.meta("rate_cb_date") == "10.10.2026"
    assert repo.meta("rate_checked") == TODAY.isoformat()


def test_auto_update_only_when_on_and_once_a_day(repo):
    calls = []

    def fetch():
        calls.append(1)
        return 12500.0, "10.10.2026"

    fx.auto_update(repo, TODAY, fetch)
    assert calls == [] and repo.usd_uzs_rate() == 11500  # off by default
    repo.set_setting("rate_auto", 1)
    fx.auto_update(repo, TODAY, fetch)
    fx.auto_update(repo, TODAY, fetch)
    assert calls == [1] and repo.usd_uzs_rate() == 12500


def test_auto_update_failure_keeps_old_rate(repo):
    repo.set_setting("rate_auto", 1)

    def broken():
        raise fx.RateError("нет сети")

    fx.auto_update(repo, TODAY, broken)
    assert repo.usd_uzs_rate() == 11500
    assert fx.auto_update_due(repo, TODAY)  # will retry later


def test_settings_turn_on_auto_rate(settings, repo):
    from fastapi.testclient import TestClient

    from finance.app import create_app

    app = create_app(settings, repo=repo, today_fn=lambda: TODAY, rate_fetcher=lambda: (12345.0, "10.10.2026"))
    client = TestClient(app)
    login(client)
    form = {"usd_uzs_rate": 11500, "operator_pct": 15, "tax_pct": 0, "restock_days": 10,
            "advertiser_low_days": 2, "rate_auto": "1", "rate_adjust_pct": 0}
    r = client.post("/settings", data=form, follow_redirects=False)
    assert r.status_code == 302 and "12" in r.headers["location"]
    assert repo.usd_uzs_rate() == 12345.0
    page = client.get("/settings").text
    assert "Последний курс ЦБ" in page and "checked" in page
    assert client.post("/settings/rate/update", follow_redirects=False).status_code == 302
