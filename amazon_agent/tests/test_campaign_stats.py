import bcrypt
from fastapi.testclient import TestClient

from amzagent.agent.runner import Deps, run_cycle
from amzagent.store import ACTIVE
from amzagent.web.app import create_app
from tests.conftest import FakeCatalog, FakeLLM, FakePush, make_product


def _live(settings, store):
    settings.push_live = True
    niche = store.add_niche("earbuds")
    push = FakePush()
    deps = Deps(settings=settings, store=store, llm=FakeLLM(), push=push,
                catalog=FakeCatalog([make_product(f"A{i}", reviews=1000 * (i + 1))
                                     for i in range(3)]))
    run_cycle(deps)
    return niche, push


def test_stats_sync_fills_campaign_and_zone_numbers(settings, store):
    niche, push = _live(settings, store)
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    push.spend_rows = [{"campaign_id": c["external_id"], "impressions": 5000, "clicks": 40,
                        "spent": 1.2}]
    push.zone_rows = [{"campaign_id": c["external_id"], "zone_id": "777", "impressions": 3000,
                       "clicks": 30, "spent": 0.9}]
    run_cycle(Deps(settings=settings, store=store, llm=FakeLLM(), push=push,
                   catalog=FakeCatalog([make_product(f"A{i}", reviews=1000 * (i + 1))
                                        for i in range(3)])))
    c = store.get_campaign(c["id"])
    assert (c["impressions"], c["ad_clicks"], c["spend"]) == (5000, 40, 1.2)
    assert store.zone_stats(c["id"])[0]["zone"] == "777"


def _client(settings, store):
    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    client = TestClient(create_app(settings, store, start_loop=False))
    client.post("/login", data={"username": "admin", "password": "pw"})
    return client


def test_dashboard_lists_stats_and_zones_and_toggles_a_zone(settings, store, monkeypatch):
    niche, push = _live(settings, store)
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    store.update_campaign(c["id"], impressions=5000, ad_clicks=40, spend=2.0)
    store.upsert_zone_stats(c["id"], "777", 3000, 30, 1.5)
    store.log_event("visit", niche.id, c["asin"], c["id"], "777")
    store.log_event("click", niche.id, c["asin"], c["id"], "777")

    # The dashboard's own deps must talk to our fake PropellerAds.
    import amzagent.web.app as web
    real_build = web.build_deps
    monkeypatch.setattr(web, "build_deps", lambda s, st: _with_push(real_build(s, st), push))

    client = _client(settings, store)
    page = client.get("/admin").text
    assert "5,000" in page and "0.80%" in page  # impressions and CTR
    assert "Зоны 1 ▾" in page and ">777<" in page
    assert f"/campaigns/{c['id']}/zones/777/exclude" in page

    page = client.post(f"/campaigns/{c['id']}/zones/777/exclude").text
    assert "зона 777 отключена" in page
    assert push.excluded == [(c["external_id"], ["777"])]
    assert f"/campaigns/{c['id']}/zones/777/include" in page

    page = client.post(f"/campaigns/{c['id']}/zones/777/include").text
    assert "снова включена" in page and push.replaced == [(c["external_id"], [])]
    assert client.post(f"/campaigns/{c['id']}/zones/abc/exclude").status_code == 400
    # the button uses fetch: JSON back, no redirect
    r = client.post(f"/campaigns/{c['id']}/zones/777/exclude",
                    headers={"X-Requested-With": "fetch"})
    assert r.json() == {"ok": True, "error": None}
    assert push.excluded[-1] == (c["external_id"], ["777"])


def _with_push(deps, push):
    deps.push = push
    return deps


def test_dashboard_shows_realtime_spend_estimate(settings, store):
    niche, push = _live(settings, store)
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    store.update_campaign(c["id"], spend=0.63)  # lagging network figure
    for _ in range(45):  # 45 visits x $0.03 / 0.85 = $1.59
        store.log_event("visit", niche.id, c["asin"], c["id"])
    page = _client(settings, store).get("/admin").text
    assert "$0.63" in page and "≈ $1.59 сейчас" in page


def test_zone_stats_are_pulled_every_15_minutes_not_every_check(settings, store):
    from amzagent.agent.runner import ZONE_STATS_FLAG, sync_stats

    niche, push = _live(settings, store)
    calls = []
    spend = push.spend

    def counting(ids, days=30, by_zone=False):
        calls.append(by_zone)
        return spend(ids, days, by_zone)

    push.spend = counting
    deps = Deps(settings=settings, store=store, push=push)
    store.set_flag(ZONE_STATS_FLAG, "")
    assert sync_stats(deps) and sync_stats(deps)
    assert calls == [False, True, False]  # zones only on the first check


def test_dashboard_reuses_period_stats_for_a_few_minutes(settings, store, monkeypatch):
    niche, push = _live(settings, store)
    calls = []

    class FakeClient:
        def __init__(self, token):
            pass

        def stats_between(self, ids, start, end, by_zone=False):
            calls.append(by_zone)
            return []

    import amzagent.web.app as web
    monkeypatch.setattr(web, "PropellerClient", FakeClient)
    client = _client(settings, store)
    client.get("/admin?period=today")
    client.get("/admin?period=today")
    assert len(calls) == 2  # one load's worth: the reload was served from cache


def test_revenue_and_profit_use_the_epc(settings, store):
    from amzagent.web.app import _campaign_rows, _campaign_totals

    niche = store.add_niche("Top Deals", asins={"A0": 1.5})
    cid = store.add_campaign(niche.id, "A0", ACTIVE, 10)
    store.update_campaign(cid, spend=4.0, external_id="1")
    for _ in range(3):
        store.log_event("click", niche.id, "A0", cid, "777")
    store.log_event("visit", niche.id, "A0", cid, "777")
    store.upsert_zone_stats(cid, "777", 100, 5, 1.0)
    row = _campaign_rows(store)[0]
    assert row["revenue"] == 4.5 and row["profit"] == 0.5  # 3 × $1.50 − $4
    assert row["zones"][0]["profit"] == 3.5  # 3 × $1.50 − $1
    t = _campaign_totals([row])
    assert t["revenue"] == 4.5 and t["profit"] == 0.5

    cid2 = store.add_campaign(niche.id, "B9", ACTIVE, 10)  # no EPC known
    store.update_campaign(cid2, spend=2.0)
    rows = _campaign_rows(store)
    assert next(r for r in rows if r["id"] == cid2)["profit"] is None
    assert _campaign_totals(rows)["profit"] == 4.5 - 6.0  # all spend counts
