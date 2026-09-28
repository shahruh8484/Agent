import re

from amzagent.agent.runner import Deps, exclude_bot_zones
from amzagent.store import ACTIVE
from tests.conftest import FakeCatalog, FakeLLM
from tests.test_campaign_stats import IPHONE, _client, _live


def _go(client, niche, c, extra="", ip="1.2.3.4", ua=IPHONE):
    return client.get(f"/go/{niche.slug}/{c['asin']}?c={c['id']}&z=777{extra}",
                      headers={"User-Agent": ua, "X-Forwarded-For": f"9.9.9.9, {ip}"},
                      follow_redirects=False)


def test_real_browser_click_goes_to_amazon(settings, store):
    niche, _ = _live(settings, store)
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    r = _go(_client(settings, store), niche, c, "&js=1")
    assert r.status_code == 302 and "tag=" in r.headers["location"]
    assert store.count_events(c["id"], "click") == 1 and store.count_events(c["id"], "bot") == 0


def test_bot_user_agent_is_held_back_without_our_tag(settings, store):
    niche, _ = _live(settings, store)
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    r = _go(_client(settings, store), niche, c, "&js=1", ua="python-requests/2.31")
    assert r.status_code == 200 and "Continue to Amazon" in r.text
    assert "tag=" not in r.text and 'var r=""' in r.text  # no way back to the tagged link
    assert store.count_events(c["id"], "click") == 0
    assert store.bot_reasons(c["id"]) == {"bot-ua": 1}


def test_click_without_page_script_gets_a_continue_button(settings, store):
    niche, _ = _live(settings, store)
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    client = _client(settings, store)
    r = _go(client, niche, c)  # no js=1: fetched directly
    assert r.status_code == 200 and store.bot_reasons(c["id"]) == {"no-js": 1}
    retry = re.search(r'var r="([^"]+)"', r.text).group(1).replace("&amp;", "&")
    passed = client.get(retry + "&js=1", headers={"User-Agent": IPHONE},
                        follow_redirects=False)  # a person pressed "Continue"
    assert passed.status_code == 302 and store.count_events(c["id"], "click") == 1
    forged = client.get(f"/go/{niche.slug}/{c['asin']}?ok=0000&js=1",
                        headers={"User-Agent": "curl/8"}, follow_redirects=False)
    assert forged.status_code == 200  # a forged or bot "continue" never passes


def test_click_right_after_the_page_opened_is_held_back(settings, store):
    niche, _ = _live(settings, store)
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    client = _client(settings, store)
    page = client.get(f"/s/{niche.slug}/p/{c['asin']}?c={c['id']}&z=777",
                      headers={"User-Agent": IPHONE}).text
    visit = re.search(r"/go/[^\"?]+\?v=(\d+)", page).group(1)
    r = _go(client, niche, c, f"&v={visit}&js=1")
    assert r.status_code == 200 and store.bot_reasons(c["id"]) == {"fast": 1}


def test_many_clicks_from_one_address_are_held_back(settings, store):
    niche, _ = _live(settings, store)
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    client = _client(settings, store)
    codes = [_go(client, niche, c, "&js=1", ip="5.5.5.5").status_code for _ in range(7)]
    assert codes == [302] * 5 + [200, 200]
    assert store.bot_reasons(c["id"]) == {"repeat-ip": 2}
    assert _go(client, niche, c, "&js=1", ip="6.6.6.6").status_code == 302  # others still pass
    assert "🤖 2 задержано" in client.get("/admin").text


def test_zones_sending_mostly_bots_are_excluded(settings, store):
    niche, push = _live(settings, store)
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    for zone, bots, real in (("777", 3, 1), ("888", 3, 5), ("999", 2, 0)):
        for _ in range(bots):
            store.log_event("bot", niche.id, c["asin"], c["id"], zone, reason="no-js")
        for _ in range(real):
            store.log_event("click", niche.id, c["asin"], c["id"], zone)
    deps = Deps(settings=settings, store=store, catalog=FakeCatalog([]), llm=FakeLLM(),
                push=push)
    exclude_bot_zones(deps)
    assert push.excluded == [(c["external_id"], ["777"])]  # 888: mostly real; 999: too few
    settings.bot_zone_min = 0
    push.excluded.clear()
    store.log_event("bot", niche.id, c["asin"], c["id"], "999", reason="no-js")
    exclude_bot_zones(deps)
    assert push.excluded == []  # rule off
