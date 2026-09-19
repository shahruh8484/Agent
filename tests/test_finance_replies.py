from fbadsagent.finance.nlp import FinanceIntent, TransactionDraft
from fbadsagent.finance.replies import (
    format_balance_reply,
    format_report_reply,
    handle_finance_intent,
)
from fbadsagent.finance.store import FinanceStore


def test_add_transaction_persists_and_confirms(settings, tmp_path):
    store = FinanceStore(tmp_path / "finance.json")
    intent = FinanceIntent(
        "add_transaction",
        transaction=TransactionDraft("expense", 50000.0, None, "Еда", "обед"),
    )

    reply = handle_finance_intent(store, settings, intent, source="telegram", telegram_user_id=42)

    transactions = store.list_transactions()
    assert len(transactions) == 1
    tx = transactions[0]
    assert tx.type == "expense"
    assert tx.amount == 50000.0
    assert tx.category == "Еда"
    assert tx.currency == settings.finance_default_currency
    assert tx.source == "telegram"
    assert tx.telegram_user_id == 42
    assert "Расход записан" in reply
    assert "50 000" in reply or "50000" in reply.replace(" ", "")


def test_add_transaction_uses_explicit_currency(settings, tmp_path):
    store = FinanceStore(tmp_path / "finance.json")
    intent = FinanceIntent(
        "add_transaction",
        transaction=TransactionDraft("income", 500.0, "USD", "Зарплата", ""),
    )

    handle_finance_intent(store, settings, intent)

    assert store.list_transactions()[0].currency == "USD"


def test_balance_query_reflects_store_state(settings, tmp_path):
    store = FinanceStore(tmp_path / "finance.json")
    handle_finance_intent(
        store, settings, FinanceIntent("add_transaction", transaction=TransactionDraft("income", 1000.0, None, "X", ""))
    )
    handle_finance_intent(
        store, settings, FinanceIntent("add_transaction", transaction=TransactionDraft("expense", 400.0, None, "Y", ""))
    )

    reply = format_balance_reply(store, settings.finance_default_currency)

    assert "600" in reply.replace(" ", "")


def test_report_query_includes_category_breakdown(settings, tmp_path):
    from datetime import datetime, timezone

    store = FinanceStore(tmp_path / "finance.json")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    handle_finance_intent(
        store,
        settings,
        FinanceIntent("add_transaction", transaction=TransactionDraft("expense", 300.0, None, "Еда", "")),
    )

    reply = format_report_reply(store, "month", settings.finance_default_currency)

    assert "Еда" in reply
    assert "300" in reply.replace(" ", "")
    assert today  # sanity: fixture computed without error


def test_balance_query_does_not_mix_currencies(settings, tmp_path):
    store = FinanceStore(tmp_path / "finance.json")
    handle_finance_intent(
        store, settings, FinanceIntent("add_transaction", transaction=TransactionDraft("income", 1000.0, "UZS", "X", ""))
    )
    handle_finance_intent(
        store, settings, FinanceIntent("add_transaction", transaction=TransactionDraft("income", 500.0, "USD", "Y", ""))
    )

    uzs_reply = format_balance_reply(store, "UZS")
    usd_reply = format_balance_reply(store, "USD")

    assert "1 000" in uzs_reply.replace(",", " ")
    assert "500" in usd_reply
    assert "1500" not in usd_reply.replace(" ", "")


def test_balance_query_intent_respects_requested_currency(settings, tmp_path):
    store = FinanceStore(tmp_path / "finance.json")
    handle_finance_intent(
        store, settings, FinanceIntent("add_transaction", transaction=TransactionDraft("income", 1000.0, "UZS", "X", ""))
    )
    handle_finance_intent(
        store, settings, FinanceIntent("add_transaction", transaction=TransactionDraft("income", 500.0, "USD", "Y", ""))
    )

    reply = handle_finance_intent(store, settings, FinanceIntent("balance_query", currency="USD"))

    assert "500" in reply
    assert "1000" not in reply.replace(" ", "")


def test_handle_finance_intent_rejects_unknown_kind(settings, tmp_path):
    import pytest

    store = FinanceStore(tmp_path / "finance.json")
    with pytest.raises(ValueError):
        handle_finance_intent(store, settings, FinanceIntent("bogus"))
