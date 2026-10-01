import bcrypt
from fastapi.testclient import TestClient

from amzagent.ig.lander import compliance_issues
from amzagent.web.app import create_app
from amzagent.web.ig_routes import KEY_FLAG, norm_domain
from tests.conftest import FakeLLM

BROWSER = {"user-agent": "Mozilla/5.0 (Linux; Android 14) Mobile Safari/537.36"}


def _client(settings, store):
    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    client = TestClient(create_app(settings, store, start_loop=False))
    client.post("/login", data={"username": "admin", "password": "pw"})
    return client


def _project(client, **extra):
    data = {"name": "BR bets", "brand": "Marca", "license_url": "https://marca.bet.br/",
            "offer_url": "https://partner.example/go?aff=7&sub1={click_id}&z={zone}",
            "payout": "40", "domain": "https://www.Apostas-Exemplo.com/", "country": "BR",
            "language": "pt", "offer": "Bônus de 100% até R$200, rollover 5x, depósito mínimo "
                                       "R$20 via PIX.", **extra}
    return client.post("/admin/ig/projects", data=data)


def test_tab_needs_login_and_is_linked(settings, store):
    anon = TestClient(create_app(settings, store, start_loop=False))
    assert anon.get("/admin/ig", follow_redirects=False).status_code == 303
    client = _client(settings, store)
    assert 'href="/admin/ig"' in client.get("/admin").text
    page = client.get("/admin/ig").text
    assert "Постбэк" in page and store.get_flag(KEY_FLAG) in page


def test_project_validation_and_domain(settings, store):
    client = _client(settings, store)
    bad = _project(client, offer_url="http://x")
    assert "Проект не сохранён" in bad.text
    settings.domain = "pushpannel.com"
    assert "отдельный домен" in _project(client, domain="pushpannel.com").text
    _project(client)
    p = store.list_ig_projects()[0]
    assert p["domain"] == "apostas-exemplo.com" and p["payout"] == 40
    assert "уже занят" in _project(client, name="dup").text
    assert norm_domain("HTTP://www.a.com:443/x") == "a.com"


def test_lander_on_own_domain_click_and_postback(settings, store, monkeypatch):
    import amzagent.web.ig_routes as ig_routes
    monkeypatch.setattr(ig_routes, "get_llm", lambda s: FakeLLM())
    client = _client(settings, store)
    _project(client)
    pid = store.list_ig_projects()[0]["id"]
    client.post(f"/admin/ig/projects/{pid}/write")
    anon = TestClient(create_app(settings, store, start_loop=False))
    host = {**BROWSER, "host": "apostas-exemplo.com"}

    # not reachable on the main domain for visitors, only on its own domain
    assert anon.get(f"/l/{pid}/", headers=BROWSER).status_code == 404
    page = anon.get("/?c=12&z=555", headers=host)
    assert page.status_code == 200 and "Apostas esportivas 18+" in page.text
    assert "Jogue com responsabilidade" in page.text and 'href="/go?c=12&amp;z=555&amp;v=' in page.text
    assert anon.get("/admin", headers=host).status_code == 404  # nothing else on that domain
    assert "Disallow: /" in anon.get("/robots.txt", headers=host).text

    go = anon.get("/go?c=12&z=555&js=1", headers=host, follow_redirects=False)
    assert go.status_code == 302
    click_id = go.headers["location"].split("sub1=")[1].split("&")[0]
    assert len(click_id) == 16 and go.headers["location"].endswith("&z=555")
    bot = anon.get("/go", headers={"host": "apostas-exemplo.com", "user-agent": "curl/8"},
                   follow_redirects=False)
    assert bot.status_code == 403

    key = store.get_flag(KEY_FLAG)
    assert anon.get(f"/pb/ig?key=wrong&click_id={click_id}&event=ftd").status_code == 403
    assert anon.get(f"/pb/ig?key={key}&subid={click_id}&status=registration").text == "ok"
    assert anon.get(f"/pb/ig?key={key}&click_id={click_id}&goal=ftd").text == "ok"
    assert anon.get(f"/pb/ig?key={key}&click_id={click_id}&event=ftd").text == "duplicate"
    assert anon.get(f"/pb/ig?key={key}&click_id={click_id}&event=dep&sum=12,5").text == "ok"
    assert anon.get(f"/pb/ig?key={key}&click_id=nope&event=ftd").text.startswith("unknown")

    st = store.ig_stats(pid)
    assert (st["visit"], st["click"], st["bot"], st["reg"], st["ftd"], st["dep"]) == \
        (1, 1, 1, 1, 1, 1)
    assert st["revenue"] == 52.5  # CPA from the project + the redeposit sum
    assert store.ig_zone_stats(pid)[0]["zone"] == "555"
    panel = client.get("/admin/ig").text
    assert "$52.50" in panel and "555" in panel

    assert anon.get("/caddy/ask?domain=www.apostas-exemplo.com").status_code == 200
    assert anon.get("/caddy/ask?domain=other.com").status_code == 404


def test_owner_preview_is_not_counted(settings, store):
    client = _client(settings, store)
    _project(client, domain="")
    pid = store.list_ig_projects()[0]["id"]
    assert client.get(f"/l/{pid}/", headers=BROWSER).status_code == 200
    go = client.get(f"/l/{pid}/go", headers=BROWSER, follow_redirects=False)
    assert "sub1=preview" in go.headers["location"]
    st = store.ig_stats(pid)
    assert st["visit"] == 0 and st["click"] == 0
    assert "Не указан домен" in client.get("/admin/ig").text


def test_lander_edit_and_forbidden_words(settings, store):
    client = _client(settings, store)
    _project(client)
    pid = store.list_ig_projects()[0]["id"]
    client.post(f"/admin/ig/projects/{pid}/lander", data={
        "headline": "Ganhe dinheiro garantido", "intro": "x", "steps": "Um\n\nDois",
        "faq": "Como sacar? | Via PIX\nsem resposta"})
    page = client.get("/admin/ig").text
    assert "Исправьте перед запуском" in page and "ganhe dinheiro" in page
    assert compliance_issues("Aposte com calma", "pt") == []
    assert compliance_issues("Oferecemos bônus no nosso site", "pt") == ["nosso", "nosso site",
                                                                         "oferecemos"]
    import json
    lander = json.loads(store.get_ig_project(pid)["lander"])
    assert lander["steps"] == ["Um", "Dois"] and len(lander["faq"]) == 1


# --- stage 2: push campaigns ---------------------------------------------------

import json as _json  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from amzagent.agent.igaming import apply_ig_rules, launch_ig_campaign  # noqa: E402
from amzagent.agent.runner import TODAY_SPEND_FLAG, Deps, apply_kill_rules  # noqa: E402
from tests.conftest import FakeCatalog, FakePush  # noqa: E402


def _ig_deps(settings, store, push=None):
    return Deps(settings=settings, store=store, catalog=FakeCatalog([]), llm=FakeLLM(), push=push)


def _live(settings, store):
    settings.push_live = True
    store.set_flag(TODAY_SPEND_FLAG, _json.dumps({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by_campaign": {}}))


def _ready_project(client, store, monkeypatch):
    import amzagent.web.ig_routes as ig_routes
    monkeypatch.setattr(ig_routes, "get_llm", lambda s: FakeLLM())
    _project(client, bid_cpc="0.008", daily_budget="12", platform="all")
    pid = store.list_ig_projects()[0]["id"]
    client.post(f"/admin/ig/projects/{pid}/push/write")
    return pid


def test_launch_builds_a_push_campaign_to_the_lander(settings, store, monkeypatch):
    client = _client(settings, store)
    pid = _ready_project(client, store, monkeypatch)
    p = store.get_ig_project(pid)
    assert p["push_title"] == "Bônus de boas-vindas" and p["bid_cpc"] == 0.008

    assert launch_ig_campaign(_ig_deps(settings, store), pid) is None  # dry run
    c = store.list_campaigns()[0]
    payload = _json.loads(c["payload"])
    assert c["status"] == "dry_run" and c["niche_id"] == 0 and c["asin"] == f"ig{pid}"
    assert payload["target_url"] == f"https://apostas-exemplo.com/?c={c['id']}&z={{zoneid}}"
    assert payload["targeting"]["country"]["list"] == ["br"]
    assert payload["rates"][0]["amount"] == 0.008 and payload["daily_amount"] == 12
    assert payload["creatives"][0]["description"].endswith("18+")
    assert "os_type" not in payload["targeting"]  # all devices
    # shown on the iGaming tab, not among the Amazon campaigns
    assert f"ig{pid}" not in client.get("/admin").text
    assert "Кампаний пока нет" not in client.get("/admin/ig").text

    push = FakePush()
    _live(settings, store)
    assert launch_ig_campaign(_ig_deps(settings, store, push), pid) is None
    assert len(push.created) == 1
    store.update_ig_project(pid, push_title="Lucro garantido")
    assert "запрещённые" in launch_ig_campaign(_ig_deps(settings, store, push), pid)
    store.update_ig_project(pid, push_title="")
    assert "нет текста пуша" in launch_ig_campaign(_ig_deps(settings, store, push), pid)


def test_rules_judge_by_deposits(settings, store, monkeypatch):
    client = _client(settings, store)
    pid = _ready_project(client, store, monkeypatch)  # CPA $40
    push = FakePush()
    _live(settings, store)
    deps = _ig_deps(settings, store, push)
    launch_ig_campaign(deps, pid)
    c = store.list_campaigns()[0]
    cid = c["id"]

    def conv(zone, *types):
        click = f"k{zone}{len(types)}"
        store.log_ig_event(pid, "click", click_id=click, campaign=str(cid), zone=zone)
        for t in types:
            store.log_ig_event(pid, t, click_id=click, campaign=str(cid), zone=zone,
                               payout=40 if t == "ftd" else 0)

    store.upsert_zone_stats(cid, "66", 1000, 100, 25)
    apply_ig_rules(deps, manual=False)  # no registrations reported yet: only CPA spent counts
    assert "66" not in store.blacklisted_zones(cid)
    conv("77")                  # $25 spent, no registration -> out
    conv("88", "reg")           # $45 spent, registration but no deposit -> out
    conv("99", "reg", "ftd")    # $50 spent, deposit -> stays
    conv("55")                  # $10 spent: too early to judge
    for zone, spent in (("77", 25), ("88", 45), ("99", 50), ("55", 10)):
        store.upsert_zone_stats(cid, zone, 1000, 100, spent)
    store.update_campaign(cid, spend=130)

    apply_kill_rules(deps)  # the Amazon rules leave it alone
    assert store.get_campaign(cid)["status"] == "active"
    apply_ig_rules(deps, manual=False)
    assert store.blacklisted_zones(cid) == {"66", "77", "88"}  # registrations now reported
    assert store.get_campaign(cid)["status"] == "active"  # one deposit: not killed

    store.update_campaign(cid, spend=260)  # 2 x limit, earned $40 < half
    apply_ig_rules(deps, manual=False)
    c = store.get_campaign(cid)
    assert c["status"] == "killed" and "less than half" in c["note"]

    # a fresh campaign with no deposits at all after 3 x CPA
    launch_ig_campaign(deps, pid)
    c2 = store.list_campaigns()[0]
    store.update_campaign(c2["id"], spend=121)
    apply_ig_rules(deps, manual=True)  # manual mode: no kill
    assert store.get_campaign(c2["id"])["status"] == "active"
    apply_ig_rules(deps, manual=False)
    assert "no deposits" in store.get_campaign(c2["id"])["note"]

    page = client.get("/admin/ig").text
    assert f"#{c2['id']}" in page and "отключена" in page
    # the panel builds its own deps: no PropellerAds token in tests
    answer = client.post(f"/admin/ig/campaigns/{c2['id']}/resume").text
    assert "не возвращена" in answer and 'class="toast bad"' in answer


def test_actionpay_style_postback(settings, store):
    """Status names instead of events, amounts in reais, rejections."""
    client = _client(settings, store)
    _project(client, payout="9")
    pid = store.list_ig_projects()[0]["id"]
    for cid in ("a1", "a2"):
        store.log_ig_event(pid, "click", click_id=cid, campaign="5", zone="7")
    key = store.get_flag(KEY_FLAG)
    anon = TestClient(create_app(settings, store, start_loop=False))
    base = f"/pb/ig?key={key}&apid=x&aptime=1&appayment=49"
    assert anon.get(f"{base}&click_id=a1&event=created&payout=49&currency=BRL").text == "ok"
    assert anon.get(f"{base}&click_id=a1&event=accepted").text == "duplicate"
    assert anon.get(f"{base}&click_id=a2&event=created&payout={{payout}}").text == "ok"
    assert store.ig_stats(pid)["revenue"] == 18.0  # 2 x $9 CPA, reais not taken as dollars
    assert anon.get(f"{base}&click_id=a2&event=rejected").text == "ok"
    assert anon.get(f"{base}&click_id=a2&event=rejected").text == "duplicate"
    st = store.ig_stats(pid)
    assert (st["ftd"], st["rej"], st["revenue"]) == (2, 1, 9.0)
    assert "−1" in client.get("/admin/ig").text


def test_period_whitelist_and_advice(settings, store, monkeypatch):
    from amzagent.agent.advice import ADVICE_PREFIX, post_advice
    from amzagent.agent.igaming import collect_ig_advice, deposit_zones

    client = _client(settings, store)
    pid = _ready_project(client, store, monkeypatch)
    push = FakePush()
    _live(settings, store)
    deps = _ig_deps(settings, store, push)
    launch_ig_campaign(deps, pid)
    cid = store.list_campaigns()[0]["id"]
    for zone in ("11", "22"):
        store.log_ig_event(pid, "click", click_id=f"c{zone}", campaign=str(cid), zone=zone)
        store.log_ig_event(pid, "ftd", click_id=f"c{zone}", campaign=str(cid), zone=zone,
                           payout=40)
    store.log_ig_event(pid, "visit", campaign=str(cid), zone="33")
    assert deposit_zones(store, cid) == ["11", "22"]

    # period: today shows the events, yesterday doesn't
    today = client.get("/admin/ig?period=today").text
    assert "Сегодня" in today and "$80.00" in today
    yesterday = client.get("/admin/ig?period=yesterday").text
    assert yesterday.count("$80.00") < today.count("$80.00")  # only in the all-time row
    assert "Вайт-лист из площадок с депозитами (2)" in client.get("/admin/ig?period=all").text

    # whitelist launch (in code: the panel's own deps have no PropellerAds in tests)
    assert launch_ig_campaign(deps, pid, zones=deposit_zones(store, cid)) is None
    wl = store.list_campaigns()[0]
    assert wl["zones_only"] == "11,22" and wl["manual_keep"] == 1
    assert push.created[-1]["targeting"]["zone"]["list"] == [11, 22]

    advice = dict(collect_ig_advice(settings, store, manual=False))
    assert any(k.startswith(f"ig-win:{cid}:") for k in advice)
    settings.agent_advice = True
    assert post_advice(deps, force=True) >= 1
    assert store.list_chat()[-1]["content"].startswith(ADVICE_PREFIX)
    assert "дали депозиты" in store.list_chat()[-1]["content"]


def test_pacing_counts_lander_visits_at_the_project_bid(settings, store, monkeypatch):
    from datetime import datetime, timezone

    from amzagent.agent.runner import PACED, pace_campaigns, spent_since_budget_day

    client = _client(settings, store)
    pid = _ready_project(client, store, monkeypatch)  # bid $0.008, $12/day
    push = FakePush()
    _live(settings, store)
    settings.pace_daily_budget = True
    deps = _ig_deps(settings, store, push)
    launch_ig_campaign(deps, pid)
    c = store.list_campaigns()[0]
    for _ in range(850):
        store.log_ig_event(pid, "visit", campaign=str(c["id"]), zone="1")
    assert round(spent_since_budget_day(deps, c), 2) == 8.0  # 850 x $0.008 / 0.85
    pace_campaigns(deps)
    now = datetime.now(timezone.utc)
    if now.hour * 60 + now.minute + 60 < 1440 * 8 / 12:  # ahead of an even schedule
        assert store.get_campaign(c["id"])["status"] == PACED


def test_early_zone_rule_and_manual_toggle(settings, store, monkeypatch):
    from amzagent.agent.igaming import set_zone

    client = _client(settings, store)
    pid = _ready_project(client, store, monkeypatch)
    push = FakePush()
    _live(settings, store)
    deps = _ig_deps(settings, store, push)
    launch_ig_campaign(deps, pid)
    cid = store.list_campaigns()[0]["id"]
    for _ in range(50):  # nobody presses the button
        store.log_ig_event(pid, "visit", campaign=str(cid), zone="31")
    for i in range(50):  # people press it here
        store.log_ig_event(pid, "visit", campaign=str(cid), zone="32")
    store.log_ig_event(pid, "click", click_id="k", campaign=str(cid), zone="32")
    store.update_campaign(cid, status="paced")  # paused by budget pacing: still judged
    apply_ig_rules(deps, manual=True)  # manual mode, zone pruning on by default
    assert store.blacklisted_zones(cid) == {"31"}
    store.update_campaign(cid, status="active")

    assert set_zone(deps, pid, "32", off=True) is None
    assert store.blacklisted_zones(cid) == {"31", "32"}
    assert set_zone(deps, pid, "31", off=False) is None
    assert store.blacklisted_zones(cid) == {"32"}
    page = client.get("/admin/ig?period=all").text
    assert "отключено 1" in page and "Вернуть" in page and "Отключить" in page


def test_push_pictures_drawn_uploaded_and_used(settings, store, monkeypatch):
    import io as _io

    from PIL import Image

    from amzagent.agent.igaming import creatives_of, draw_ig_creatives
    from tests.test_ai_creatives import FakePainter

    client = _client(settings, store)
    pid = _ready_project(client, store, monkeypatch)
    push = FakePush()
    _live(settings, store)
    deps = _ig_deps(settings, store, push)
    painter = FakePainter(fail_first=True)
    deps.painter = painter
    assert draw_ig_creatives(deps, pid) == 2  # one of three failed
    assert "no text" in painter.prompts[0].lower() or "No text" in painter.prompts[0]

    buf = _io.BytesIO()
    Image.new("RGB", (1200, 800), (10, 120, 60)).save(buf, "JPEG")
    client.post(f"/admin/ig/projects/{pid}/pictures/upload",
                files={"image": ("mine.jpg", buf.getvalue(), "image/jpeg")})
    pics = creatives_of(store.get_ig_project(pid))
    assert len(pics) == 3
    page = client.get("/admin/ig").text
    assert f"/media/ig{pid}/{pics[0][1]}" in page
    assert client.get(f"/media/ig{pid}/{pics[0][1]}").status_code == 200

    client.post(f"/admin/ig/projects/{pid}/pictures/{pics[0][1]}/delete")
    assert len(creatives_of(store.get_ig_project(pid))) == 2
    assert "не изображение" in client.post(
        f"/admin/ig/projects/{pid}/pictures/upload",
        files={"image": ("x.jpg", b"not an image", "image/jpeg")}).text

    assert launch_ig_campaign(deps, pid) is None
    assert len(push.created[-1]["creatives"]) == 2  # both pictures rotate


def test_zones_are_judged_over_the_whole_project(settings, store, monkeypatch):
    client = _client(settings, store)
    pid = _ready_project(client, store, monkeypatch)
    push = FakePush()
    _live(settings, store)
    deps = _ig_deps(settings, store, push)
    launch_ig_campaign(deps, pid)
    launch_ig_campaign(deps, pid)
    a, b = [c["id"] for c in store.list_campaigns()[:2]]
    for cid in (a, b):  # 30 + 30 visits, no button press: 60 over the project
        for _ in range(30):
            store.log_ig_event(pid, "visit", campaign=str(cid), zone="41")
    apply_ig_rules(deps, manual=False)
    assert "41" in store.blacklisted_zones(a) and "41" in store.blacklisted_zones(b)
    assert "отключено 1" in client.get("/admin/ig?period=all").text


def test_filters_by_campaign_device_and_daily_table(settings, store, monkeypatch):
    client = _client(settings, store)
    pid = _ready_project(client, store, monkeypatch)
    push = FakePush()
    _live(settings, store)
    deps = _ig_deps(settings, store, push)
    launch_ig_campaign(deps, pid)
    launch_ig_campaign(deps, pid)
    a, b = sorted(c["id"] for c in store.list_campaigns()[:2])
    for _ in range(7):
        store.log_ig_event(pid, "visit", campaign=str(a), zone="1", device="mobile")
    for _ in range(3):
        store.log_ig_event(pid, "visit", campaign=str(b), zone="2", device="desktop")
    assert store.ig_stats(pid)["visit"] == 10
    assert store.ig_stats(pid, campaign=str(a))["visit"] == 7
    assert store.ig_stats(pid, device="desktop")["visit"] == 3
    days = store.ig_daily(pid, offset_minutes=300)
    assert len(days) == 1 and days[0]["visits"] == 10

    page = client.get(f"/admin/ig?period=all&campaign={b}&device=").text
    assert f"кампания #{b}" in page and "По дням (1)" in page
    assert f"#{a} <span" not in page  # the other campaign's row is hidden
    # the filter sticks across reloads
    assert f"кампания #{b}" in client.get("/admin/ig").text
    page = client.get("/admin/ig?period=all&campaign=&device=mobile").text
    assert "Телефоны и планшеты</option>" in page and "сбросить фильтры" in page


def test_budget_per_campaign(settings, store, monkeypatch):
    from amzagent.agent.runner import apply_daily_budget, set_campaign_budget

    client = _client(settings, store)
    pid = _ready_project(client, store, monkeypatch)  # $12/day
    push = FakePush()
    _live(settings, store)
    deps = _ig_deps(settings, store, push)
    launch_ig_campaign(deps, pid)
    c = store.list_campaigns()[0]
    assert set_campaign_budget(deps, c["id"], 20) is None
    assert store.get_campaign(c["id"])["daily_budget"] == 20
    assert push.updates[-1] == (c["external_id"], {"daily_amount": 20})
    assert "минимум" in set_campaign_budget(deps, c["id"], 5)
    assert "общего лимита" in set_campaign_budget(deps, c["id"], 100)  # limit is $30
    assert apply_daily_budget(deps, 15) == 0  # the Amazon setting leaves it alone
    assert store.get_campaign(c["id"])["daily_budget"] == 20

    settings.push_live = False  # dry run: the panel's own deps need no PropellerAds
    launch_ig_campaign(deps, pid)
    dry = store.list_campaigns()[0]
    answer = client.post(f"/admin/ig/campaigns/{dry['id']}/budget", data={"budget": "25"}).text
    assert "$25.00 в день" in answer and store.get_campaign(dry["id"])["daily_budget"] == 25


def test_stats_refresh_endpoint_and_live_blocks(settings, store, monkeypatch):
    client = _client(settings, store)
    pid = _ready_project(client, store, monkeypatch)
    res = client.post("/admin/ig/stats/refresh").json()
    assert res == {"ok": True, "error": ""}  # no PropellerAds in tests: nothing to pull
    page = client.get("/admin/ig").text
    for block in (f"live-stats-{pid}", f"live-camps-{pid}", f"live-sum-{pid}"):
        assert f'id="{block}"' in page
    assert 'id="refresh-stats"' in page
    anon = TestClient(create_app(settings, store, start_loop=False))
    assert anon.post("/admin/ig/stats/refresh").status_code == 401


def test_bot_filter_on_the_lander_button_and_privacy_page(settings, store):
    from datetime import datetime, timedelta, timezone

    client = _client(settings, store)
    _project(client, license_note="Sistema Lotérico de Pernambuco · Portaria SPA/MF nº 528/2025",
             contact="contato@exemplo.com")
    pid = store.list_ig_projects()[0]["id"]
    anon = TestClient(create_app(settings, store, start_loop=False))
    host = {**BROWSER, "host": "apostas-exemplo.com"}
    page = anon.get("/?c=1&z=9", headers=host).text
    assert "Portaria SPA/MF nº 528/2025" in page and 'href="/privacidade"' in page
    assert "contato@exemplo.com" in page
    privacy = anon.get("/privacidade", headers=host)
    assert privacy.status_code == 200 and "LGPD" in privacy.text

    v = [r for r in store._all("SELECT id FROM ig_events WHERE type = 'visit'")][-1]["id"]
    # clicked within 2 s of opening the page: a "continue" page, not the offer
    fast = anon.get(f"/go?c=1&z=9&v={v}&js=1", headers=host, follow_redirects=False)
    assert fast.status_code == 200 and "Continuar" in fast.text
    store._exec("UPDATE ig_events SET ts = ? WHERE id = ?",
                ((datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(), v))
    # no JS mark: continue page; its signed link lets a real person through
    nojs = anon.get(f"/go?c=1&z=9&v={v}", headers=host, follow_redirects=False)
    assert "Continuar" in nojs.text
    ok = nojs.text.split("+'")[1].split("&js=1'")[0]
    passed = anon.get(f"/go{ok}&js=1", headers=host, follow_redirects=False)
    assert passed.status_code == 302
    # automated browser: refused
    assert anon.get(f"/go?v={v}&wd=1", headers=host).status_code == 403
    reasons = store.ig_bot_reasons(pid)
    assert reasons["fast"] == 1 and reasons["webdriver"] == 1
    assert reasons["no-js"] == 2  # the plain click and the automated browser
    # many clicks from one address in an hour: refused
    for _ in range(5):
        anon.get(f"/go?v={v}&js=1", headers=host, follow_redirects=False)
    assert anon.get(f"/go?v={v}&js=1", headers=host).status_code == 403


def test_campaign_update_falls_back_to_put():
    from amzagent.push.propeller import PropellerClient, PropellerError

    calls = []

    def fake(self, method, path, **kw):
        calls.append(method)
        if method == "PATCH":
            raise PropellerError("PATCH -> HTTP 405", status=405)
        return {}

    client = PropellerClient("token")
    PropellerClient._request, orig = fake, PropellerClient._request
    try:
        client.update_campaign("11949973", {"daily_amount": 20})
    finally:
        PropellerClient._request = orig
    assert calls == ["PATCH", "PUT"]


def test_budget_panel_only_after_a_refusal(settings, store, monkeypatch):
    from amzagent.agent.runner import set_campaign_budget
    from amzagent.push.propeller import PropellerError

    client = _client(settings, store)
    pid = _ready_project(client, store, monkeypatch)
    push = FakePush()
    _live(settings, store)
    deps = _ig_deps(settings, store, push)
    launch_ig_campaign(deps, pid)
    c = store.list_campaigns()[0]

    def refuse(cid, fields):
        raise PropellerError("PATCH -> HTTP 400: Empty data", status=400)

    push.update_campaign = refuse
    assert "PropellerAds не принял" in set_campaign_budget(deps, c["id"], 20)
    assert store.get_campaign(c["id"])["daily_budget"] == 12
    assert set_campaign_budget(deps, c["id"], 20, panel_only=True) is None
    assert store.get_campaign(c["id"])["daily_budget"] == 20


def test_casino_theme_reaches_every_prompt(settings, store, monkeypatch):
    import amzagent.web.ig_routes as ig_routes
    from amzagent.agent.igaming import describe_ig_scenes, write_push_text
    from amzagent.ig.lander import write_lander

    client = _client(settings, store)
    _project(client)  # sport, apostas-exemplo.com
    _project(client, name="Cassino", theme="casino", domain="cassino.apostas-exemplo.com")
    casino = [p for p in store.list_ig_projects() if p["name"] == "Cassino"][0]
    assert casino["theme"] == "casino" and casino["domain"] == "cassino.apostas-exemplo.com"
    llm = FakeLLM()
    write_lander(llm, casino)
    write_push_text(llm, casino)
    describe_ig_scenes(llm, {**casino, "push_title": "x"}, 3)
    assert "online casino" in llm.prompts[0] and "online casino" in llm.prompts[1]
    assert "roulette" in llm.prompts[2] and "football" not in llm.prompts[2]
    assert "Казино" in client.get("/admin/ig").text
    anon = TestClient(create_app(settings, store, start_loop=False))
    assert anon.get("/", headers={**BROWSER, "host": "cassino.apostas-exemplo.com"}).status_code == 200


def test_push_flags_operator_voice():
    from amzagent.agent.igaming import forbidden_in_push
    project = {"language": "pt", "push_title": "Diversão no PlayBet",
               "push_text": "Depósito mínimo de R$49 em nosso cassino"}
    assert "nosso" in forbidden_in_push(project)
    assert forbidden_in_push({**project, "push_text": "Cassino da PlayBet"}) == []


def test_push_text_fits_propeller_limit():
    from amzagent.agent.igaming import push_text
    from amzagent.push.propeller import DESCRIPTION_MAX
    long = "Depósito mínimo de R$49 em cassino online da PlayBet"
    assert len(push_text({"push_text": long})) <= DESCRIPTION_MAX
    assert push_text({"push_text": long}).endswith("18+")
    assert push_text({"push_text": "Cassino da PlayBet"}) == "Cassino da PlayBet 18+"
