from amzagent.agent.runner import KILLED, PAUSE_FLAG, Deps, run_cycle
from amzagent.store import ACTIVE, DRY_RUN, STOPPED
from tests.conftest import FakeCatalog, FakeLLM, FakePush, make_product


def _deps(settings, store, push=None, products=None):
    products = products or [make_product(f"A{i}", reviews=1000 * (i + 1)) for i in range(4)]
    return Deps(settings=settings, store=store, catalog=FakeCatalog(products), llm=FakeLLM(),
                push=push)


def test_dry_run_builds_site_and_campaigns_without_sending(settings, store):
    niche = store.add_niche("wireless earbuds")
    assert run_cycle(_deps(settings, store))

    products = store.list_products(niche.id)
    assert [p.asin for p, _ in products] == ["A3", "A2", "A1", "A0"]  # best first
    assert all(c is not None for _, c in products)
    assert store.get_site_copy(niche.id).site_title == "Sound Picks"

    campaigns = store.list_campaigns()
    assert len(campaigns) == settings.campaigns_per_site
    assert {c["status"] for c in campaigns} == {DRY_RUN}
    assert "c=" in campaigns[0]["payload"] and "example.com/s/wireless-earbuds/p/" in campaigns[0]["payload"]

    # A second cycle must not stack more dry-run campaigns.
    run_cycle(_deps(settings, store))
    assert len(store.list_campaigns()) == settings.campaigns_per_site


def test_live_mode_launches_within_budget_cap(settings, store):
    settings.push_live = True
    settings.max_daily_spend = 25  # room for two $10 campaigns, not three
    settings.campaigns_per_site = 3
    store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))

    active = store.list_campaigns(statuses=(ACTIVE,))
    assert len(active) == 2 and len(push.created) == 2
    assert push.started == []  # created into moderation; starts on approval
    assert store.running_daily_budget() == 20


def test_switching_to_live_retires_dry_runs(settings, store):
    store.add_niche("earbuds")
    run_cycle(_deps(settings, store))
    settings.push_live = True
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    assert len(store.list_campaigns(statuses=(DRY_RUN,))) == 0
    assert len(store.list_campaigns(statuses=(ACTIVE,))) == settings.campaigns_per_site


def test_kill_rule_stops_expensive_campaign_and_never_relaunches(settings, store):
    settings.push_live = True
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    bad, good = store.list_campaigns(statuses=(ACTIVE,))
    # bad: $6 spent, 1 click -> $6/click. good: $6 spent, 30 clicks -> $0.20/click.
    push.spend_rows = [{"campaign_id": bad["external_id"], "zone_id": "", "spent": 6.0},
                       {"campaign_id": good["external_id"], "zone_id": "", "spent": 6.0}]
    store.log_event("click", niche.id, bad["asin"], bad["id"])
    for _ in range(30):
        store.log_event("click", niche.id, good["asin"], good["id"])

    run_cycle(_deps(settings, store, push))
    assert store.get_campaign(bad["id"])["status"] == KILLED
    assert store.get_campaign(good["id"])["status"] == ACTIVE
    assert bad["external_id"] in push.stopped
    # The freed slot goes to a new product, not back to the killed one.
    active_asins = {c["asin"] for c in store.list_campaigns(statuses=(ACTIVE,))}
    assert bad["asin"] not in active_asins and len(active_asins) == 2


def test_zone_blacklist(settings, store):
    settings.push_live = True
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    push.zone_rows = [
        {"campaign_id": c["external_id"], "zone_id": "111", "spent": 2.0},  # no clicks -> bad
        {"campaign_id": c["external_id"], "zone_id": "222", "spent": 2.0},  # has a click
        {"campaign_id": c["external_id"], "zone_id": "333", "spent": 0.2},  # too little data
    ]
    run_cycle(_deps(settings, store, push))
    assert push.excluded == []  # no zone ids seen in visits yet: never blacklist blindly
    store.log_event("visit", niche.id, c["asin"], c["id"], "222")
    store.log_event("click", niche.id, c["asin"], c["id"], "222")
    run_cycle(_deps(settings, store, push))
    assert push.excluded == [(c["external_id"], ["111"])]
    run_cycle(_deps(settings, store, push))  # not re-sent
    assert len(push.excluded) == 1


def test_kill_switch_stops_everything(settings, store):
    settings.push_live = True
    store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    store.set_flag(PAUSE_FLAG, "1")
    run_cycle(_deps(settings, store, push))
    assert not store.list_campaigns(statuses=(ACTIVE,))
    assert {c["status"] for c in store.list_campaigns()} == {STOPPED}
    assert len(push.created) == 2  # nothing new launched


def test_empty_search_keeps_existing_site(settings, store):
    niche = store.add_niche("earbuds")
    run_cycle(_deps(settings, store))
    deps = _deps(settings, store)
    deps.catalog = FakeCatalog([])
    run_cycle(deps)
    assert len(store.list_products(niche.id)) == 4


def test_run_log_is_live_and_finished(settings, store):
    store.add_niche("earbuds")
    run_cycle(_deps(settings, store))
    run = store.list_runs()[0]
    assert run["ok"] == 1 and "4 selected" in run["log"]
    assert not store.run_in_progress()


def test_interrupted_run_is_closed_on_startup(settings, store):
    store.start_run(None)
    assert store.run_in_progress()
    store.close_interrupted_runs()
    assert not store.run_in_progress()
    assert store.list_runs()[0]["ok"] == 0


def test_busy_cycle_without_wait_is_skipped(settings, store):
    from amzagent.agent import runner

    runner._run_lock.acquire()
    try:
        assert run_cycle(_deps(settings, store)) is False
    finally:
        runner._run_lock.release()


def test_old_copy_is_upgraded_once_with_buying_tips(settings, store):
    from amzagent.models import ProductCopy

    niche = store.add_niche("earbuds")
    run_cycle(_deps(settings, store))
    # Simulate copy written by an older version (no tips, version 0).
    for p, _ in store.list_products(niche.id):
        store.set_product_copy(niche.id, p.asin,
                               ProductCopy(summary="old", push_title="t", push_text="b"))
    deps = _deps(settings, store)
    run_cycle(deps)
    copies = [c for _, c in store.list_products(niche.id)]
    assert all(c.buying_tips == ["Check fit", "Check battery life"] for c in copies)
    assert all(c.version == 1 for c in copies)

    llm_calls_before = len(deps.llm.prompts)
    run_cycle(deps)  # already current: nothing rewritten
    assert len(deps.llm.prompts) == llm_calls_before


def test_moderation_rejection_frees_slot_and_is_not_retried(settings, store):
    settings.push_live = True
    store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    rejected, approved = store.list_campaigns(statuses=(ACTIVE,))
    push.statuses = {rejected["external_id"]: 3, approved["external_id"]: 6}
    run_cycle(_deps(settings, store, push))
    assert store.get_campaign(rejected["id"])["status"] == KILLED
    assert "rejected" in store.get_campaign(rejected["id"])["note"]
    assert store.get_campaign(approved["id"])["note"] == "PropellerAds: working"
    active_asins = {c["asin"] for c in store.list_campaigns(statuses=(ACTIVE,))}
    assert rejected["asin"] not in active_asins and len(active_asins) == 2


def test_quick_check_kills_after_test_budget_and_refills(settings, store):
    from amzagent.agent.runner import quick_check

    settings.push_live = True
    settings.kill_min_spend = 1.0
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    first = store.list_campaigns(statuses=(ACTIVE,))[-1]
    push.spend_rows = [{"campaign_id": first["external_id"], "spent": 1.1}]  # $1 spent, no clicks
    deps = _deps(settings, store, push)
    assert quick_check(deps)
    assert store.get_campaign(first["id"])["status"] == KILLED
    assert len(store.list_campaigns(statuses=(ACTIVE,))) == 2  # slot refilled right away
    assert store.list_runs()[0]["log"]  # something happened -> journal entry
    before = len(store.list_runs())
    quick_check(_deps(settings, store, push))  # nothing new happens
    assert len(store.list_runs()) == before
    assert niche


def test_old_zone_macro_is_replaced_on_live_campaigns(settings, store):
    import json

    settings.push_live = True
    store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    assert "z={zoneid}" in json.loads(c["payload"])["target_url"]  # new campaigns: new macro
    payload = json.loads(c["payload"])
    payload["target_url"] = payload["target_url"].replace("{zoneid}", "${ZONEID}")
    store.update_campaign(c["id"], payload=payload)  # simulate an old campaign
    run_cycle(_deps(settings, store, push))
    assert push.url_updates and push.url_updates[0][1].count("{zoneid}") == 1
    assert "${ZONEID}" not in json.loads(store.get_campaign(c["id"])["payload"])["target_url"]
    n = len(push.url_updates)
    run_cycle(_deps(settings, store, push))
    assert len(push.url_updates) == n  # done once
