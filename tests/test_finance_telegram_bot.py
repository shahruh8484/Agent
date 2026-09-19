import json

from fbadsagent.finance.store import FinanceStore
from fbadsagent.finance.telegram_bot import handle_text_message, is_authorized
from tests.conftest import FakeLLM


def test_is_authorized_allows_anyone_when_no_allowlist(settings):
    assert is_authorized(settings, 12345) is True
    assert is_authorized(settings, None) is True


def test_is_authorized_restricts_to_allowlist(settings):
    settings.finance_telegram_allowed_user_ids = "111, 222"

    assert is_authorized(settings, 111) is True
    assert is_authorized(settings, 222) is True
    assert is_authorized(settings, 333) is False
    assert is_authorized(settings, None) is False


def test_handle_text_message_adds_transaction(settings, tmp_path):
    store = FinanceStore(tmp_path / "finance.json")
    llm = FakeLLM(
        response=json.dumps(
            {
                "intent": "add_transaction",
                "type": "expense",
                "amount": 20000,
                "currency": None,
                "category": "Транспорт",
                "note": "",
                "period": None,
            }
        )
    )

    reply = handle_text_message(store, llm, settings, "потратил 20000 на такси", telegram_user_id=7)

    assert len(store.list_transactions()) == 1
    assert store.list_transactions()[0].telegram_user_id == 7
    assert "Расход записан" in reply


def test_handle_text_message_falls_back_to_conversation(settings, tmp_path):
    store = FinanceStore(tmp_path / "finance.json")

    class ChattyLLM(FakeLLM):
        def generate(self, system, prompt, max_tokens=1024):
            self.calls.append((system, prompt))
            if "finance-tracking bot" in system:  # nlp classifier system prompt
                return json.dumps(
                    {"intent": "other", "type": None, "amount": None, "currency": None, "category": None, "note": None, "period": None}
                )
            return "Привет! Чем могу помочь?"

    reply = handle_text_message(store, ChattyLLM(), settings, "привет", telegram_user_id=7)

    assert reply == "Привет! Чем могу помочь?"
    assert store.list_transactions() == []
