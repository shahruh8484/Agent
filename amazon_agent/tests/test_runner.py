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


def test_kill_rule_by_amazon_rate_and_never_relaunches(settings, store):
    settings.push_live = True
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    bad, good = store.list_campaigns(statuses=(ACTIVE,))
    # Both: $6 spent, 200 visits. bad: 1 to Amazon (0.5%), good: 4 (2%).
    push.spend_rows = [{"campaign_id": bad["external_id"], "zone_id": "", "spent": 6.0},
                       {"campaign_id": good["external_id"], "zone_id": "", "spent": 6.0}]
    for c, amazon in ((bad, 1), (good, 4)):
        for _ in range(200):
            store.log_event("visit", niche.id, c["asin"], c["id"])
        for _ in range(amazon):
            store.log_event("click", niche.id, c["asin"], c["id"])

    run_cycle(_deps(settings, store, push))
    assert store.get_campaign(bad["id"])["status"] == KILLED
    assert "1 of 200 visitors went to Amazon (0.50% < 1%)" in store.get_campaign(bad["id"])["note"]
    assert store.get_campaign(good["id"])["status"] == ACTIVE
    assert bad["external_id"] in push.stopped
    # The freed slot goes to a new product, not back to the killed one.
    active_asins = {c["asin"] for c in store.list_campaigns(statuses=(ACTIVE,))}
    assert bad["asin"] not in active_asins and len(active_asins) == 2


def test_optional_cost_rule(settings, store):
    settings.push_live = True
    settings.min_amazon_rate = 0  # rate rule off
    settings.max_cost_per_amazon_click = 2.0
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    push.spend_rows = [{"campaign_id": c["external_id"], "spent": 6.0}]
    for _ in range(2):  # $3 per Amazon click
        store.log_event("click", niche.id, c["asin"], c["id"])
    run_cycle(_deps(settings, store, push))
    assert store.get_campaign(c["id"])["status"] == KILLED
    assert "cost per Amazon click $3.00 > $2.00" in store.get_campaign(c["id"])["note"]


def test_zone_pruning_only_on_passing_campaigns(settings, store):
    settings.push_live = True
    settings.zone_min_visits = 15
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    push.spend_rows = [{"campaign_id": c["external_id"], "spent": 3.0}]
    push.zone_rows = [
        {"campaign_id": c["external_id"], "zone_id": "111", "spent": 0.5},  # 20 visits, 0 Amazon
        {"campaign_id": c["external_id"], "zone_id": "222", "spent": 0.5},  # gave a click
        {"campaign_id": c["external_id"], "zone_id": "333", "spent": 0.2},  # 5 visits: too few
    ]

    def visits(zone, n, amazon=0):
        for _ in range(n):
            store.log_event("visit", niche.id, c["asin"], c["id"], zone)
        for _ in range(amazon):
            store.log_event("click", niche.id, c["asin"], c["id"], zone)

    visits("111", 20)
    visits("222", 20, amazon=2)  # campaign: 2 of 45 visitors -> 4.4% >= 1%: passed
    visits("333", 5)
    run_cycle(_deps(settings, store, push))
    assert store.get_campaign(c["id"])["status"] == ACTIVE
    assert push.excluded == [(c["external_id"], ["111"])]
    run_cycle(_deps(settings, store, push))  # not re-sent
    assert len(push.excluded) == 1


def test_no_zone_pruning_before_the_test_is_passed(settings, store):
    settings.push_live = True
    settings.kill_min_spend = 5.0  # still in its test at $3
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    push.spend_rows = [{"campaign_id": c["external_id"], "spent": 3.0}]
    push.zone_rows = [{"campaign_id": c["external_id"], "zone_id": "111", "spent": 2.0}]
    for _ in range(20):
        store.log_event("visit", niche.id, c["asin"], c["id"], "111")
    run_cycle(_deps(settings, store, push))
    assert push.excluded == []

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


def test_stuck_creating_row_holds_slot_then_expires(settings, store):
    from datetime import datetime, timedelta, timezone

    settings.push_live = True
    settings.campaigns_per_site = 1
    niche = store.add_niche("earbuds")
    run_cycle(_deps(settings, store, FakePush()))  # builds the site, launches 1
    active = store.list_campaigns(statuses=(ACTIVE,))[0]
    store.update_campaign(active["id"], status="stopped")
    # A launch that died half-way:
    stuck = store.add_campaign(niche.id, "A2", "creating", 10)
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    assert push.created == []  # the stuck row still holds the only slot
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    store._exec("UPDATE campaigns SET created_at = ? WHERE id = ?", (old, stuck))
    run_cycle(_deps(settings, store, push))
    assert store.get_campaign(stuck)["status"] == "error"
    assert len(push.created) == 1  # slot reused


def test_stats_outage_blocks_launches_then_stops_campaigns(settings, store):
    from datetime import datetime, timedelta, timezone

    from amzagent.push.propeller import PropellerError

    settings.push_live = True
    store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    assert len(store.list_campaigns(statuses=(ACTIVE,))) == 2

    def broken(*a, **k):
        raise PropellerError("Time zone is available only in the weekly period.")

    push.spend = broken
    store.update_campaign(store.list_campaigns(statuses=(ACTIVE,))[0]["id"], status="killed")
    deps = _deps(settings, store, push)
    run_cycle(deps)
    assert len(push.created) == 2  # no replacement launched while blind
    assert len(store.list_campaigns(statuses=(ACTIVE,))) == 1  # not stopped yet (< 30 min)
    assert store.get_flag("stats_error")

    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    store.set_flag("stats_ok_at", old)
    run_cycle(_deps(settings, store, push))
    assert store.list_campaigns(statuses=(ACTIVE,)) == []  # safety stop
    assert any("no spend data" in (c["note"] or "") for c in store.list_campaigns())


def test_kill_uses_realtime_visits_when_network_stats_lag(settings, store):
    settings.push_live = True
    settings.kill_min_spend = 1.0
    settings.push_bid_cpc = 0.03
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    # PropellerAds still reports $0, but 40 paid visits already landed (~$1.41).
    for _ in range(40):
        store.log_event("visit", niche.id, c["asin"], c["id"], None)
    run_cycle(_deps(settings, store, push))
    killed = store.get_campaign(c["id"])
    assert killed["status"] == KILLED and "spent $1.41" in killed["note"]


def test_campaign_paused_by_network_counts_as_stopped(settings, store):
    from amzagent.push.propeller import PropellerError

    settings.push_live = True
    store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]

    def refuse(ids):
        raise PropellerError("campaign is paused")

    push.stop = refuse
    push.statuses = {c["external_id"]: 7}  # paused by PropellerAds
    from amzagent.agent.runner import stop_campaign
    stop_campaign(_deps(settings, store, push), c, KILLED, "test")
    assert store.get_campaign(c["id"])["status"] == KILLED

    other = store.list_campaigns(statuses=(ACTIVE,))[0]
    push.statuses = {other["external_id"]: 6}  # really running: must retry later
    stop_campaign(_deps(settings, store, push), other, KILLED, "test")
    assert store.get_campaign(other["id"])["status"] == ACTIVE


def test_daily_cap_counts_money_already_spent_today(settings, store):
    settings.push_live = True
    settings.max_daily_spend = 20
    settings.campaigns_per_site = 2
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    first, second = store.list_campaigns(statuses=(ACTIVE,))
    # first burned $8 today with no Amazon clicks -> killed; a replacement
    # would make $8 + $10 + $10 = $28 > $20, so it must not launch.
    push.spend_rows = [{"campaign_id": first["external_id"], "spent": 8.0}]
    run_cycle(_deps(settings, store, push))
    assert store.get_campaign(first["id"])["status"] == KILLED
    assert len(push.created) == 2  # no third campaign today
    assert niche


def test_unknown_24h_spend_blocks_launches(settings, store):
    from amzagent.push.propeller import PropellerError

    settings.push_live = True
    store.add_niche("earbuds")
    push = FakePush()

    def broken(*a, **k):
        raise PropellerError("stats down")

    push.spend_last_hours = broken
    deps = _deps(settings, store, push)
    run_cycle(deps)
    assert push.created == []
    assert any("24h spend unknown" in line for line in deps.log)


def test_24h_limit_pauses_running_and_resumes_later(settings, store):
    settings.push_live = True
    settings.max_daily_spend = 20
    settings.min_amazon_rate = 0  # keep kill rules out of this test
    store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    a, b = store.list_campaigns(statuses=(ACTIVE,))
    push.spend_rows = [{"campaign_id": a["external_id"], "spent": 12.0},
                       {"campaign_id": b["external_id"], "spent": 9.0}]  # $21 >= $20
    run_cycle(_deps(settings, store, push))
    assert {store.get_campaign(c["id"])["status"] for c in (a, b)} == {"capped"}
    assert set(push.stopped) >= {a["external_id"], b["external_id"]}
    assert len(push.created) == 2  # paused ones keep their slots: nothing new

    push.spend_rows = [{"campaign_id": a["external_id"], "spent": 5.0}]  # window rolled on
    run_cycle(_deps(settings, store, push))
    assert {store.get_campaign(c["id"])["status"] for c in (a, b)} == {ACTIVE}
    assert set(push.started) == {a["external_id"], b["external_id"]}


def test_manual_resume_is_not_killed_again(settings, store):
    from amzagent.agent.runner import resume_campaign

    settings.push_live = True
    settings.max_daily_spend = 100
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    push.spend_rows = [{"campaign_id": c["external_id"], "spent": 3.0}]
    for _ in range(100):
        store.log_event("visit", niche.id, c["asin"], c["id"])
    run_cycle(_deps(settings, store, push))
    assert store.get_campaign(c["id"])["status"] == KILLED  # 0% -> killed

    assert resume_campaign(_deps(settings, store, push), c["id"]) is None
    assert c["external_id"] in push.started
    run_cycle(_deps(settings, store, push))
    kept = store.get_campaign(c["id"])
    assert kept["status"] == ACTIVE and kept["manual_keep"] == 1  # rules leave it alone

    from amzagent.agent.runner import stop_campaign
    stop_campaign(_deps(settings, store, push), kept, KILLED, "stopped manually")
    assert store.get_campaign(c["id"])["manual_keep"] == 0


def test_resume_waits_for_room_and_refuses_dry_runs(settings, store):
    from amzagent.agent.runner import resume_campaign

    store.add_niche("earbuds")
    run_cycle(_deps(settings, store))  # dry run
    dry = store.list_campaigns()[0]
    store.update_campaign(dry["id"], status="stopped")
    assert "тестовый режим" in resume_campaign(_deps(settings, store, FakePush()), dry["id"])

    settings.push_live = True
    settings.max_daily_spend = 20
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    a, b = store.list_campaigns(statuses=(ACTIVE,))
    store.update_campaign(a["id"], status="killed")
    push.spend_rows = [{"campaign_id": a["external_id"], "spent": 15.0}]
    from amzagent.agent.runner import sync_today_spend
    deps = _deps(settings, store, push)
    sync_today_spend(deps)
    assert resume_campaign(deps, a["id"]) is None  # no room: queued, not refused
    queued = store.get_campaign(a["id"])
    assert queued["status"] == "capped" and queued["manual_keep"] == 1
    assert a["external_id"] not in push.started

    push.spend_rows = [{"campaign_id": a["external_id"], "spent": 2.0}]  # window rolled on
    run_cycle(_deps(settings, store, push))
    assert store.get_campaign(a["id"])["status"] == ACTIVE  # started by itself
    assert a["external_id"] in push.started


def test_manual_campaign_zones_are_pruned_even_below_the_rate(settings, store):
    settings.push_live = True
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    store.update_campaign(c["id"], manual_keep=1)
    push.spend_rows = [{"campaign_id": c["external_id"], "spent": 5.0}]
    for zone, visits, amazon in (("111", 100, 0), ("222", 100, 1)):  # 1 of 200 = 0.5% < 1%
        for _ in range(visits):
            store.log_event("visit", niche.id, c["asin"], c["id"], zone)
        for _ in range(amazon):
            store.log_event("click", niche.id, c["asin"], c["id"], zone)
    run_cycle(_deps(settings, store, push))
    assert store.get_campaign(c["id"])["status"] == ACTIVE  # kept (manual)
    assert push.excluded == [(c["external_id"], ["111"])]  # dead zone cut, clicking zone kept


def test_resume_eta_follows_when_old_spend_leaves_the_window(settings, store):
    import json
    from datetime import datetime, timedelta, timezone

    from amzagent.agent.runner import CAPPED, TODAY_SPEND_FLAG, resume_eta

    niche = store.add_niche("earbuds")
    cid = store.add_campaign(niche.id, "A1", CAPPED, 10)
    now = datetime.now(timezone.utc)
    assert resume_eta(store, 20) is None  # spend unknown
    store.set_flag(TODAY_SPEND_FLAG, json.dumps({"at": now.isoformat(), "by_campaign": {
        str(cid): 22.0}}))
    # 22 visits: 11 made 20h ago, 11 made 2h ago -> $1 per visit
    for hours_ago in [20] * 11 + [2] * 11:
        store.log_event("visit", niche.id, "A1", cid, None)
        ts = (now - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
        store._exec("UPDATE events SET ts = ? WHERE id = (SELECT MAX(id) FROM events)", (ts,))
    # $22 spent, resumes at <= $19: once 3 of the 20h-old visits roll off, in ~4h
    eta = resume_eta(store, 20)
    assert timedelta(hours=3, minutes=58) < eta - now < timedelta(hours=4, minutes=2)
    assert resume_eta(store, 30) <= datetime.now(timezone.utc)  # already under: right away
    store.update_campaign(cid, status=ACTIVE)
    assert resume_eta(store, 20) is None  # nothing paused


def test_when_formats_panel_time():
    from datetime import datetime, timedelta, timezone

    from amzagent.web.app import when

    assert when(None, "Asia/Tashkent") is None
    assert when(datetime.now(timezone.utc), "Asia/Tashkent") == "в ближайшие минуты"
    text = when(datetime.now(timezone.utc) + timedelta(hours=3, minutes=5, seconds=30),
                "Asia/Tashkent")
    assert "через 3 ч 5 мин" in text


def test_network_paused_campaign_is_marked_not_forced(settings, store):
    from amzagent.agent.runner import sync_moderation

    settings.push_live = True
    store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    a = store.list_campaigns(statuses=(ACTIVE,))[0]
    push.statuses = {a["external_id"]: 7}  # "Paused · Daily impressions"
    sync_moderation(_deps(settings, store, push))
    c = store.get_campaign(a["id"])
    assert c["status"] == ACTIVE and c["note"] == "PropellerAds: paused"
    assert push.started == []  # PropellerAds restarts it by itself


def test_committed_counts_a_running_campaigns_real_24h_spend(settings, store):
    import json
    from datetime import datetime, timezone

    from amzagent.agent.runner import TODAY_SPEND_FLAG, committed_24h

    niche = store.add_niche("earbuds")
    running = store.add_campaign(niche.id, "A1", ACTIVE, 10)
    fresh = store.add_campaign(niche.id, "A2", ACTIVE, 10)
    stopped = store.add_campaign(niche.id, "A3", KILLED, 10)
    assert committed_24h(store) is None  # unknown spend is never zero
    store.set_flag(TODAY_SPEND_FLAG, json.dumps({
        "at": datetime.now(timezone.utc).isoformat(),
        "by_campaign": {str(running): 18.42, str(fresh): 2.0, str(stopped): 31.5}}))
    # 31.50 stopped + 18.42 (spent more than its $10 budget) + 10 (budget > $2 spent)
    assert committed_24h(store) == 31.5 + 18.42 + 10


def test_stopped_campaigns_keep_getting_late_spend(settings, store):
    settings.push_live = True
    store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    store.update_campaign(c["id"], status=KILLED, spend=6.18)
    push.spend_rows = [{"campaign_id": c["external_id"], "spent": 6.9}]  # late clicks
    run_cycle(_deps(settings, store, push))
    assert store.get_campaign(c["id"])["spend"] == 6.9
    assert store.get_campaign(c["id"])["status"] == KILLED


def test_pacing_spreads_the_daily_budget(settings, store, monkeypatch):
    import json
    from datetime import datetime, timezone

    import amzagent.agent.runner as runner

    settings.push_live = True
    niche = store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    for _ in range(100):  # 100 visits x $0.03 / 0.85 = $3.53 spent today
        store.log_event("visit", niche.id, c["asin"], c["id"], "1")
    settings.pace_daily_budget = True
    store.set_flag(runner.TODAY_SPEND_FLAG, json.dumps({
        "at": datetime.now(timezone.utc).isoformat(), "by_campaign": {str(c["id"]): 3.5}}))

    monkeypatch.setattr(runner, "pace_allowance", lambda budget, now, start=None: 2.0)  # early in the day
    runner.pace_campaigns(_deps(settings, store, push))
    assert store.get_campaign(c["id"])["status"] == runner.PACED
    assert c["external_id"] in push.stopped

    monkeypatch.setattr(runner, "pace_allowance", lambda budget, now, start=None: 3.6)  # barely caught up
    runner.pace_campaigns(_deps(settings, store, push))
    assert store.get_campaign(c["id"])["status"] == runner.PACED  # no flapping

    monkeypatch.setattr(runner, "pace_allowance", lambda budget, now, start=None: 5.0)  # later
    runner.pace_campaigns(_deps(settings, store, push))
    assert store.get_campaign(c["id"])["status"] == ACTIVE
    assert push.started == [c["external_id"]]


def test_pace_allowance_is_an_even_share_with_a_head_start():
    from datetime import datetime, timezone

    from amzagent.agent.runner import pace_allowance

    noon = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    assert round(pace_allowance(10, noon), 2) == round(10 * 780 / 1440, 2)  # 13h of 24
    late = datetime(2026, 9, 27, 23, 30, tzinfo=timezone.utc)
    assert pace_allowance(10, late) == 10  # capped at the budget
    # launched at 17:00 today: from the launch, not from midnight
    launched = datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)
    at_1837 = datetime(2026, 9, 27, 18, 37, tzinfo=timezone.utc)
    assert round(pace_allowance(10, at_1837, launched), 2) == round(10 * 157 / 1440, 2)
    yesterday = datetime(2026, 9, 26, 17, 0, tzinfo=timezone.utc)
    assert pace_allowance(10, noon, yesterday) == pace_allowance(10, noon)


def test_busy_reports_a_running_cycle(settings, store, monkeypatch):
    import amzagent.busy as busy

    monkeypatch.setattr(busy, "get_settings", lambda: settings)
    assert busy.main() == 1
    store.start_run(None)
    assert busy.main() == 0


def test_quick_checks_go_to_their_own_journal_and_repeats_are_muted(settings, store):
    from amzagent.agent.runner import quick_check

    settings.push_live = True
    settings.max_daily_spend = 10  # room for exactly one campaign
    store.add_niche("earbuds")
    push = FakePush()
    run_cycle(_deps(settings, store, push))
    cycle_log = store.list_runs(1, kinds=("cycle",))[0]["log"]
    assert cycle_log.count("no room under the daily cap") == 1
    assert quick_check(_deps(settings, store, push))
    assert quick_check(_deps(settings, store, push))
    assert store.list_runs(10, kinds=("check",)) == []  # the same news isn't repeated

    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    store.update_campaign(c["id"], status=KILLED)  # something new happens
    push.statuses = {c["external_id"]: 3}
    store.update_campaign(c["id"], status=ACTIVE)
    quick_check(_deps(settings, store, push))  # rejected by moderation -> logged
    checks = store.list_runs(10, kinds=("check",))
    assert len(checks) == 1 and "rejected by moderation" in checks[0]["log"]


class PlatformPush(FakePush):
    def __init__(self, reference=None):
        super().__init__()
        self.reference, self.lookups = reference, 0

    def os_type_values(self, platform):
        from amzagent.push.propeller import PropellerError, match_os_types
        self.lookups += 1
        if self.reference is None:
            raise PropellerError("HTTP 404")
        return match_os_types(self.reference, platform)


def test_platform_choice_targets_new_campaigns(settings, store):
    settings.push_live = True
    store.add_niche("earbuds")
    push = PlatformPush([{"id": 7, "name": "Mobile"}, {"id": 8, "name": "Desktop"}])
    run_cycle(_deps(settings, store, push))
    assert all("os_type" not in p["targeting"] for p in push.created)  # "all" by default

    settings.push_platform = "mobile"
    settings.campaigns_per_site = 4
    run_cycle(_deps(settings, store, push))
    new = push.created[2:]
    assert new and all(p["targeting"]["os_type"]["list"] == [7] for p in new)
    assert push.lookups == 1  # looked up once, then remembered


def test_platform_falls_back_when_reference_is_missing(settings, store):
    settings.push_live, settings.push_platform = True, "desktop"
    store.add_niche("earbuds")
    push = PlatformPush(None)
    run_cycle(_deps(settings, store, push))
    assert push.created and all(p["targeting"]["os_type"]["list"] == ["desktop"]
                                for p in push.created)
