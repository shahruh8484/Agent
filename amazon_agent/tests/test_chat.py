import json

import bcrypt
from fastapi.testclient import TestClient

from amzagent.agent.runner import Deps, run_cycle
from amzagent.panel_settings import effective
from amzagent.store import ACTIVE
from amzagent.web.app import create_app
from amzagent.web.chat import TOOLS, ChatAgent
from tests.conftest import FakeCatalog, FakeLLM, FakePush, make_product


class ScriptedBackend:
    """Calls the given tools in order, then answers with what they returned."""

    def __init__(self, calls):
        self.calls, self.seen = calls, None

    def chat(self, system, messages, tools, call):
        self.seen = messages
        results = [json.loads(call(name, args)) for name, args in self.calls]
        return "готово: " + json.dumps(results, ensure_ascii=False)


def _live(settings, store, monkeypatch):
    settings.push_live = True
    store.add_niche("earbuds")
    push = FakePush()
    import amzagent.web.chat as chat
    real = chat.build_deps

    def deps_with_fake_push(s, st):
        d = real(s, st)
        d.push = push
        return d

    monkeypatch.setattr(chat, "build_deps", deps_with_fake_push)
    run_cycle(Deps(settings=settings, store=store, llm=FakeLLM(), push=push,
                   catalog=FakeCatalog([make_product(f"A{i}", reviews=1000 * (i + 1))
                                        for i in range(3)])))
    return push


def test_tools_have_valid_schemas():
    names = [t["name"] for t in TOOLS]
    assert len(names) == len(set(names))
    assert all(t["parameters"]["type"] == "object" for t in TOOLS)


def test_chat_reads_data_and_stops_a_campaign(settings, store, monkeypatch):
    push = _live(settings, store, monkeypatch)
    c = store.list_campaigns(statuses=(ACTIVE,))[0]
    backend = ScriptedBackend([("overview", {}), ("list_campaigns", {}),
                               ("stop_campaign", {"campaign_id": c["id"]})])
    out = ChatAgent(settings, store, backend=backend).reply("останови первую")
    assert store.get_campaign(c["id"])["status"] == "killed"
    assert c["external_id"] in push.stopped
    assert len(out["actions"]) == 1  # reads aren't actions
    assert '"running_campaigns": 2' in out["reply"]
    assert [m["role"] for m in store.list_chat()] == ["user", "assistant"]
    assert "чат: stop_campaign" in store.list_runs(1)[0]["log"]
    # the next turn sees the history
    backend2 = ScriptedBackend([])
    ChatAgent(settings, store, backend=backend2).reply("а теперь?")
    assert backend2.seen[0] == {"role": "user", "content": "останови первую"}


def test_chat_changes_settings_through_validation(settings, store):
    agent = ChatAgent(settings, store, backend=ScriptedBackend([
        ("update_settings", {"changes": {"max_daily_spend": 45}}),
        ("update_settings", {"changes": {"campaign_daily_budget": 1}}),  # below $10 minimum
        ("update_settings", {"changes": {"admin_password_hash": "x"}}),
    ]))
    out = agent.reply("подними лимит до 45")
    assert effective(settings, store).max_daily_spend == 45
    assert effective(settings, store).campaign_daily_budget == settings.campaign_daily_budget
    assert "минимум" in out["reply"] and "нельзя менять" in out["reply"]


def test_chat_survives_backend_errors(settings, store):
    class Broken:
        def chat(self, *a):
            raise RuntimeError("401 bad key")

    out = ChatAgent(settings, store, backend=Broken()).reply("привет")
    assert "401 bad key" in out["reply"]


def test_chat_routes_need_login(settings, store, monkeypatch):
    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    client = TestClient(create_app(settings, store, start_loop=False))
    assert client.post("/chat", json={"message": "hi"}).status_code == 401
    assert client.get("/chat/history").status_code == 401
    client.post("/login", data={"username": "admin", "password": "pw"})
    monkeypatch.setattr(ChatAgent, "reply", lambda self, m: {"reply": "ok " + m, "actions": []})
    assert client.post("/chat", json={"message": "hi"}).json()["reply"] == "ok hi"
    assert "Чат с агентом" in client.get("/admin").text
    store.add_chat("user", "x")
    assert client.get("/chat/history").json()["messages"][0]["content"] == "x"
    client.post("/chat/clear")
    assert store.list_chat() == []


def test_chat_switches_mode_and_launches_a_product(settings, store, monkeypatch):
    from amzagent.agent.runner import MANUAL_FLAG

    push = _live(settings, store, monkeypatch)
    niche = store.list_niches()[0]
    idle = [p.asin for p, _ in store.list_products(niche.id)
            if p.asin not in {c["asin"] for c in store.list_campaigns()}]
    settings.max_daily_spend = 100
    out = ChatAgent(settings, store, backend=ScriptedBackend([
        ("set_mode", {"mode": "manual"}),
        ("site_products", {"site_id": niche.id}),
        ("launch_product", {"site_id": niche.id, "asin": idle[0]}),
    ])).reply("ручной режим и запусти следующий товар")
    assert store.get_flag(MANUAL_FLAG) == "1"
    assert any(c["asin"] == idle[0] for c in store.list_campaigns(statuses=(ACTIVE,)))
    assert len(out["actions"]) == 2 and len(push.created) == 3


def test_diagnosis_and_fresh_state_reach_the_model(settings, store, monkeypatch):
    from datetime import datetime, timezone

    from amzagent.agent.runner import TODAY_SPEND_FLAG

    push = _live(settings, store, monkeypatch)
    a, b = store.list_campaigns(statuses=(ACTIVE,))
    store.update_campaign(a["id"], status="capped")
    store.update_campaign(b["id"], note="PropellerAds: paused")
    store.set_flag(TODAY_SPEND_FLAG, json.dumps({
        "at": datetime.now(timezone.utc).isoformat(), "by_campaign": {str(a["id"]): 51.0}}))
    agent = ChatAgent(settings, store, backend=None)
    reasons = " ".join(agent.diagnose())
    assert "на паузе по суточному лимиту" in reasons and "$51.00" in reasons
    assert f"#{b['id']}" in reasons and "ждёт поздних кликов" in reasons

    class Capture:
        def chat(self, system, messages, tools, call):
            self.system = system
            return "ok"

    cap = Capture()
    ChatAgent(settings, store, backend=cap).reply("почему реклама остановилась?")
    assert "ТЕКУЩЕЕ СОСТОЯНИЕ" in cap.system and "why_ads_not_running" in cap.system
    assert f'"limit_24h": {settings.max_daily_spend}' in cap.system
    assert push is not None
