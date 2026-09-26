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
