import json
from types import SimpleNamespace

from finance.assistant import OpenAIBackend, user_content
from finance.config import Settings, assistant_provider


class FakeCompletions:
    def __init__(self, message):
        self.message = message
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=self.message)])


def fake_client(message):
    completions = FakeCompletions(message)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def tool_call(name, args):
    return SimpleNamespace(type="function", function=SimpleNamespace(name=name, arguments=args))


def test_openai_backend_parses_text_and_tool_calls():
    message = SimpleNamespace(
        content="Вот что внесу:",
        tool_calls=[
            tool_call("add_web", json.dumps({"name": "Vasya"})),
            tool_call("add_web", "{broken"),          # invalid JSON is skipped
            tool_call("delete_everything", "{}"),     # unknown tools are ignored
        ],
    )
    client, completions = fake_client(message)
    backend = OpenAIBackend("", "gpt-4o", client=client)

    msgs = [{"role": "user", "content": user_content("скрин", (b"img", "image/png"))}]
    text, actions = backend.respond("SYSTEM", msgs)

    assert text == "Вот что внесу:"
    assert actions == [{"name": "add_web", "input": {"name": "Vasya"}}]
    sent = completions.kwargs
    assert sent["model"] == "gpt-4o"
    assert sent["messages"][0] == {"role": "system", "content": "SYSTEM"}
    parts = sent["messages"][1]["content"]
    assert parts[0]["type"] == "image_url" and parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[1] == {"type": "text", "text": "скрин"}
    assert {t["function"]["name"] for t in sent["tools"]} >= {"add_payment", "add_order"}


def test_openai_backend_text_only():
    client, _ = fake_client(SimpleNamespace(content="Вы в плюсе.", tool_calls=None))
    assert OpenAIBackend("", "gpt-4o", client=client).respond("s", [{"role": "user", "content": "?"}]) == ("Вы в плюсе.", [])


def test_assistant_provider_choice():
    assert assistant_provider(Settings(openai_api_key="", anthropic_api_key="")) == ""
    assert assistant_provider(Settings(openai_api_key="sk-x", anthropic_api_key="")) == "openai"
    assert assistant_provider(Settings(openai_api_key="", anthropic_api_key="sk-ant")) == "anthropic"
    assert assistant_provider(Settings(openai_api_key="sk-x", anthropic_api_key="sk-ant")) == "openai"
    assert assistant_provider(Settings(openai_api_key="sk-x", anthropic_api_key="sk-ant", llm_provider="anthropic")) == "anthropic"


class ScriptedCompletions:
    """Returns the queued messages in order and records each request."""

    def __init__(self, *messages):
        self.messages = list(messages)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=self.messages.pop(0))])


def test_openai_backend_retries_when_it_describes_but_does_not_call():
    prose = SimpleNamespace(content="Связка Макс → Санж с 2026-07-01. Предлагаю создать такую связку — проверь и подтверди.", tool_calls=None)
    forced = SimpleNamespace(content=None, tool_calls=[tool_call("add_link", json.dumps({"web": "Макс"}))])
    completions = ScriptedCompletions(prose, forced)
    backend = OpenAIBackend("", "gpt-4o", client=SimpleNamespace(chat=SimpleNamespace(completions=completions)))

    text, actions = backend.respond("s", [{"role": "user", "content": "Связка Макс → Санж"}])
    assert text.startswith("Связка Макс")
    assert actions == [{"name": "add_link", "input": {"web": "Макс"}}]
    assert [c["tool_choice"] for c in completions.calls] == ["auto", "required"]


def test_openai_backend_does_not_force_tools_on_questions():
    question = SimpleNamespace(content="Сколько платим Максу — за лид или за апрув? Потом подтвердишь.", tool_calls=None)
    completions = ScriptedCompletions(question)
    backend = OpenAIBackend("", "gpt-4o", client=SimpleNamespace(chat=SimpleNamespace(completions=completions)))
    assert backend.respond("s", [{"role": "user", "content": "x"}])[1] == []
    assert len(completions.calls) == 1
