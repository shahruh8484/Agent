from amzagent.agent.advice import ADVICE_PREFIX, post_advice
from amzagent.agent.runner import MANUAL_FLAG, Deps
from amzagent.store import ACTIVE
from tests.conftest import FakeCatalog, FakeLLM
from tests.test_campaign_stats import _client, _live


def _deps(settings, store, push):
    return Deps(settings=settings, store=store, catalog=FakeCatalog([]), llm=FakeLLM(),
                push=push)


def test_agent_posts_advice_once_and_the_chat_shows_it(settings, store):
    niche, push = _live(settings, store)
    settings.agent_advice = True
    a, b = store.list_campaigns(statuses=(ACTIVE,))[:2]
    store.set_flag(MANUAL_FLAG, "1")
    settings.manual_prune_zones = False  # the agent won't prune: advice instead
    for _ in range(20):                  # a dead zone in campaign a
        store.log_event("visit", niche.id, a["asin"], a["id"], "111")
    for i in range(30):                  # a winning zone in campaign b
        store.log_event("visit", niche.id, b["asin"], b["id"], "222")
        if i < 6:
            store.log_event("click", niche.id, b["asin"], b["id"], "222")
    product = store.get_product(niche.id, b["asin"])[0]
    product.epc, product.cc_budget = 1.5, "low"
    store.replace_products(niche.id, [(p if p.asin != b["asin"] else product, 1.0)
                                      for p, _ in store.list_products(niche.id)])

    deps = _deps(settings, store, push)
    assert post_advice(deps) >= 3
    text = store.list_chat()[-1]["content"]
    assert text.startswith(ADVICE_PREFIX)
    assert "зона 111" in text and "вайт-лист" in text and "222 (6 из 30, 20%)" in text
    assert "«low»" in text
    assert push.excluded == [] and push.stopped == []  # advice only, nothing changed

    assert post_advice(deps) == 0  # at most once an hour
    assert post_advice(deps, force=True) == 0  # and the same advice isn't repeated
    client = _client(settings, store)
    assert "💡 1" in client.get("/admin").text  # unread in the chat button
    client.get("/chat/history")
    assert "💡 1" not in client.get("/admin").text  # read


def test_advice_can_be_switched_off(settings, store):
    niche, push = _live(settings, store)
    settings.agent_advice = False
    assert post_advice(_deps(settings, store, push), force=True) == 0
    assert store.list_chat() == []
