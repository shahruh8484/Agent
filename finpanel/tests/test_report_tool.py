import json
from types import SimpleNamespace

from finance.assistant import OpenAIBackend, period_report, run_read_tool
from tests.conftest import TODAY


def make_link(repo):
    web = repo.add_web("Sanya")
    adv = repo.add_advertiser("Ahmed")
    # rekl $10 per approve with 30% guarantee, web $2 per lead
    return repo.add_link(web, adv, "Glyco", "2026-10-01", "approve", 10.0, 30.0, "lead", 2.0)


def test_period_report_closed_days(repo):
    link = make_link(repo)
    repo.upsert_traffic_stat(link, "2026-10-08", 100, 100, 20)  # guarantee -> 30 approves
    text = period_report(repo, "2026-10-01", "2026-10-08", TODAY)
    assert "окончательные" in text
    assert "Sanya → Ahmed" in text
    assert "ИТОГО ПРИБЫЛЬ: $100.00" in text


def test_period_report_splits_preliminary_days(repo):
    link = make_link(repo)
    repo.upsert_traffic_stat(link, "2026-10-08", 100, 100, 20)  # final: +100
    repo.upsert_traffic_stat(link, "2026-10-09", 100, 100, 20)  # preliminary: 200 - 200 = 0
    text = period_report(repo, "2026-10-01", "2026-10-10", TODAY)
    assert "предварительные" in text
    closed = text.split("Только закрытые дни")[1]
    assert "2026-10-01 — 2026-10-08" in closed
    assert "ИТОГО ПРИБЫЛЬ: $100.00" in closed


def test_period_report_bad_dates(repo):
    assert "Ошибка" in run_read_tool(repo, TODAY, "get_report", {"start": "1 октября", "end": "x"})


class ScriptedCompletions:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=self.messages.pop(0))])


def call(id_, name, args):
    return SimpleNamespace(id=id_, type="function", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def test_openai_runs_read_tool_and_answers_from_it():
    completions = ScriptedCompletions([
        SimpleNamespace(content=None, tool_calls=[call("c1", "get_report", {"start": "2026-10-01", "end": "2026-10-08"})]),
        SimpleNamespace(content="С 1 по 8 октября вы в плюсе: $100.", tool_calls=None),
    ])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    asked = []

    def read_tool(name, args):
        asked.append((name, args))
        return "ИТОГО ПРИБЫЛЬ: $100.00"

    text, actions = OpenAIBackend("", "gpt-4o", client=client).respond(
        "s", [{"role": "user", "content": "я в плюсе с 1 по 8?"}], read_tool=read_tool)

    assert text == "С 1 по 8 октября вы в плюсе: $100."
    assert actions == []
    assert asked == [("get_report", {"start": "2026-10-01", "end": "2026-10-08"})]
    second = completions.sent[1]["messages"]
    assert second[-2]["tool_calls"][0]["id"] == "c1"
    assert second[-1] == {"role": "tool", "tool_call_id": "c1", "content": "ИТОГО ПРИБЫЛЬ: $100.00"}
    assert "get_report" in {t["function"]["name"] for t in completions.sent[0]["tools"]}


def test_openai_without_read_tool_hides_it():
    completions = ScriptedCompletions([SimpleNamespace(content="ok", tool_calls=None)])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    OpenAIBackend("", "gpt-4o", client=client).respond("s", [{"role": "user", "content": "?"}])
    assert "get_report" not in {t["function"]["name"] for t in completions.sent[0]["tools"]}
