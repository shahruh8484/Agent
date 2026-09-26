from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi.testclient import TestClient

from amzagent.store import ACTIVE
from amzagent.web.app import create_app
from amzagent.web.period import parse_period
from tests.test_campaign_stats import _live

NOW = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)  # 08:00 in Tashkent


def test_presets_are_tashkent_days():
    p = parse_period("today", None, None, "Asia/Tashkent", NOW)
    assert p.label() == "27.09.2026"
    assert p.start == datetime(2026, 9, 26, 19, 0, tzinfo=timezone.utc)  # 00:00 UTC+5
    assert p.end - p.start == timedelta(days=1)
    y = parse_period("yesterday", None, None, "Asia/Tashkent", NOW)
    assert y.label() == "26.09.2026" and y.end == p.start
    assert parse_period("7d", None, None, "Asia/Tashkent", NOW).label() == "21.09.2026 — 27.09.2026"
    assert parse_period(None, None, None, "Asia/Tashkent", NOW).is_all


def test_custom_range_is_ordered_and_inclusive():
    p = parse_period(None, "2026-09-26", "2026-09-20", "Asia/Tashkent", NOW)
    assert p.key == "custom" and p.label() == "20.09.2026 — 26.09.2026"
    assert p.end - p.start == timedelta(days=7)
    assert parse_period(None, "bad", None, "Asia/Tashkent", NOW).is_all


def test_dashboard_period_uses_network_window_and_site_events(settings, store, monkeypatch):
    niche, push = _live(settings, store)
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    store.update_campaign(c["id"], impressions=9999, ad_clicks=99, spend=9.99)  # all-time
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(timespec="seconds")
    store.log_event("visit", niche.id, c["asin"], c["id"], "777")
    store._exec("UPDATE events SET ts = ? WHERE id = (SELECT MAX(id) FROM events)", (old,))
    store.log_event("visit", niche.id, c["asin"], c["id"], "777")  # today

    calls = []

    class FakeClient:
        def __init__(self, token):
            pass

        def stats_between(self, ids, start, end, by_zone=False):
            calls.append((start, end, by_zone))
            if by_zone:
                return [{"campaign_id": c["external_id"], "zone_id": "777", "impressions": 50,
                         "clicks": 2, "spent": 0.06}]
            return [{"campaign_id": c["external_id"], "zone_id": "", "impressions": 123,
                     "clicks": 4, "spent": 0.12}]

    import amzagent.web.app as web
    monkeypatch.setattr(web, "PropellerClient", FakeClient)
    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    client = TestClient(create_app(settings, store, start_loop=False))
    client.post("/login", data={"username": "admin", "password": "pw"})

    page = client.get("/admin?period=today").text
    assert "Итого за" in page and "$0.12" in page and ">123<" in page
    assert "$9.99" not in page  # all-time numbers not mixed in
    assert len(calls) == 2
    all_time = client.get("/admin").text
    assert "$9.99" in all_time and "Итого за всё время" in all_time
