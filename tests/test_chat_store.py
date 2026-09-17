from fbadsagent.models import ChatMessage
from fbadsagent.web.chat_store import ChatStore


def make_message(role="user", content="hi", kind="message") -> ChatMessage:
    return ChatMessage(role=role, content=content, created_at="2026-09-17 10:00 UTC", kind=kind)


def test_empty_store(tmp_path):
    store = ChatStore(tmp_path / "chat.json")
    assert store.list_messages() == []


def test_append_and_list(tmp_path):
    store = ChatStore(tmp_path / "chat.json")
    store.append(make_message("user", "Hello"))
    store.append(make_message("assistant", "Hi there", kind="message"))

    messages = store.list_messages()
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].content == "Hi there"


def test_append_trims_to_max_messages(tmp_path):
    store = ChatStore(tmp_path / "chat.json", max_messages=3)
    for i in range(5):
        store.append(make_message("user", f"msg {i}"))

    messages = store.list_messages()
    assert len(messages) == 3
    assert [m.content for m in messages] == ["msg 2", "msg 3", "msg 4"]


def test_clear(tmp_path):
    store = ChatStore(tmp_path / "chat.json")
    store.append(make_message())
    store.clear()
    assert store.list_messages() == []
