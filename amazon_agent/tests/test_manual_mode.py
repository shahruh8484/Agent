import bcrypt
from fastapi.testclient import TestClient

from amzagent.agent.runner import KILLED, MANUAL_FLAG, Deps, launch_product, run_cycle
from amzagent.store import ACTIVE
from amzagent.web.app import create_app
from tests.conftest import FakeCatalog, FakeLLM, FakePush, make_product


def _deps(settings, store, push=None):
    products = [make_product(f"A{i}", reviews=1000 * (i + 1)) for i in range(4)]
    return Deps(settings=settings, store=store, catalog=FakeCatalog(products), llm=FakeLLM(),
                push=push)


def test_manual_mode_builds_the_site_but_launches_nothing(settings, store):
    settings.push_live = True
    niche = store.add_niche("earbuds")
    store.set_flag(MANUAL_FLAG, "1")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    assert len(store.list_products(niche.id)) == 4  # site still kept up to date
    assert push.created == [] and store.list_campaigns() == []


def test_manual_mode_does_not_kill_or_prune_but_still_caps_spend(settings, store):
    settings.push_live = True
    settings.manual_prune_zones = False
    settings.zone_min_visits = 5
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    store.set_flag(MANUAL_FLAG, "1")
    for _ in range(40):  # 40 visits, no Amazon clicks: would fail the 1% test
        store.log_event("visit", niche.id, c["asin"], c["id"], "111")
    push.spend_rows = [{"campaign_id": c["external_id"], "spent": 3.0}]
    push.zone_rows = [{"campaign_id": c["external_id"], "zone_id": "111", "spent": 3.0}]
    run_cycle(_deps(settings, store, push))
    assert store.get_campaign(c["id"])["status"] == ACTIVE
    assert push.excluded == []
    # the 24h limit still applies
    push.spend_rows = [{"campaign_id": c["external_id"], "spent": settings.max_daily_spend}]
    run_cycle(_deps(settings, store, push))
    assert store.get_campaign(c["id"])["status"] == "capped"


def test_manual_mode_prunes_dead_zones_when_enabled(settings, store):
    settings.push_live = True
    settings.zone_min_visits = 15
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    store.set_flag(MANUAL_FLAG, "1")
    for zone, visits, amazon in (("111", 15, 0), ("222", 15, 1), ("333", 5, 0)):
        for _ in range(visits):
            store.log_event("visit", niche.id, c["asin"], c["id"], zone)
        for _ in range(amazon):
            store.log_event("click", niche.id, c["asin"], c["id"], zone)
    push.spend_rows = [{"campaign_id": c["external_id"], "spent": 0.5}]  # still in its test
    run_cycle(_deps(settings, store, push))
    assert store.get_campaign(c["id"])["status"] == ACTIVE  # not killed in manual mode
    # 15 visits, no Amazon click -> out; the clicking zone and the young one stay
    assert push.excluded == [(c["external_id"], ["111"])]


def test_launch_product_by_hand(settings, store):
    settings.push_live = True
    settings.max_daily_spend = 20
    niche = store.add_niche("earbuds")
    store.set_flag(MANUAL_FLAG, "1")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    deps = _deps(settings, store, push)
    assert launch_product(deps, niche.id, "A1") is None
    assert launch_product(deps, niche.id, "A1") == "на этот товар уже есть работающая кампания"
    assert launch_product(deps, niche.id, "NOPE") == "товар не найден на сайте"
    assert launch_product(deps, niche.id, "A2") is None
    assert "дневного лимита" in launch_product(deps, niche.id, "A3")  # $20 = two campaigns
    # a product whose campaign was stopped can be launched again by hand
    first = store.list_campaigns(statuses=(ACTIVE,))[-1]
    store.update_campaign(first["id"], status=KILLED)
    assert launch_product(deps, niche.id, first["asin"]) is None


def test_mode_switch_and_product_list_in_panel(settings, store):
    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    niche = store.add_niche("earbuds")
    run_cycle(_deps(settings, store))
    client = TestClient(create_app(settings, store, start_loop=False))
    client.post("/login", data={"username": "admin", "password": "pw"})
    page = client.get("/admin").text
    assert 'value="auto" class="on"' in page and "Ручной режим:" not in page
    assert f"/niches/{niche.id}/products/A0/launch" in page  # the 4th product has no campaign
    client.post("/mode", data={"mode": "manual"})
    assert store.get_flag(MANUAL_FLAG) == "1"
    assert 'value="manual" class="on"' in client.get("/admin").text
    client.post(f"/niches/{niche.id}/products/A0/launch")
    assert any(c["asin"] == "A0" for c in store.list_campaigns())
    client.post("/mode", data={"mode": "auto"})
    assert store.get_flag(MANUAL_FLAG) == "0"


def test_whitelist_campaign_from_zone_table(settings, store, monkeypatch):
    settings.push_live = True
    settings.max_daily_spend = 100
    settings.zone_min_visits = 5
    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    for zone, visits, amazon in (("111", 10, 2), ("222", 10, 1), ("333", 10, 0)):
        for _ in range(visits):
            store.log_event("visit", niche.id, c["asin"], c["id"], zone)
        for _ in range(amazon):
            store.log_event("click", niche.id, c["asin"], c["id"], zone)
    import amzagent.web.app as web_app
    monkeypatch.setattr(web_app, "build_deps", lambda s, st: _deps(s, st, push))
    client = TestClient(create_app(settings, store, start_loop=False))
    client.post("/login", data={"username": "admin", "password": "pw"})
    page = client.get("/admin").text
    assert 'value="111" form="wl' in page and "только с отмеченными зонами" in page
    before = len(push.created)
    client.post(f"/campaigns/{c['id']}/whitelist", data={"zones": ["111", "222", "x;1"]})
    assert len(push.created) == before + 1
    assert push.created[-1]["targeting"]["zone"] == {"list": [111, 222], "is_excluded": False}
    wl = max(store.list_campaigns(statuses=(ACTIVE,)), key=lambda r: r["id"])
    assert wl["zones_only"] == "111,222" and wl["manual_keep"] == 1 and wl["asin"] == c["asin"]
    assert store.get_campaign(c["id"])["status"] == ACTIVE  # the original keeps running
    assert "вайт-лист 2 зон" in client.get("/admin").text

    # the whitelist copy's zones are never pruned by the agent
    for _ in range(10):
        store.log_event("visit", niche.id, wl["asin"], wl["id"], "111")
    push.spend_rows = [{"campaign_id": wl["external_id"], "spent": 5.0}]
    run_cycle(_deps(settings, store, push))
    assert all(ext != wl["external_id"] for ext, _ in push.excluded)
    assert store.get_campaign(wl["id"])["status"] == ACTIVE


def test_whitelist_needs_zones_and_room(settings, store):
    from amzagent.agent.runner import launch_whitelist

    settings.push_live = True
    settings.max_daily_spend = 20
    store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    deps = _deps(settings, store, push)
    assert launch_whitelist(deps, c["id"], []) == "не отмечено ни одной зоны"
    assert "дневного лимита" in launch_whitelist(deps, c["id"], ["111"])  # 2 x $10 already
