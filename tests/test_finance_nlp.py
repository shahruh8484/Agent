import json

from fbadsagent.finance.nlp import parse_finance_message
from tests.conftest import FakeLLM


def test_parse_add_expense():
    llm = FakeLLM(
        response=json.dumps(
            {
                "intent": "add_transaction",
                "type": "expense",
                "amount": 50000,
                "currency": None,
                "category": "Еда",
                "note": "обед",
                "period": None,
            }
        )
    )

    intent = parse_finance_message(llm, "потратил 50000 на еду, обед")

    assert intent is not None
    assert intent.kind == "add_transaction"
    assert intent.transaction.type == "expense"
    assert intent.transaction.amount == 50000.0
    assert intent.transaction.category == "Еда"
    assert intent.transaction.note == "обед"
    assert intent.transaction.currency is None


def test_parse_add_income_with_currency():
    llm = FakeLLM(
        response=json.dumps(
            {
                "intent": "add_transaction",
                "type": "income",
                "amount": 500,
                "currency": "USD",
                "category": "Зарплата",
                "note": "",
                "period": None,
            }
        )
    )

    intent = parse_finance_message(llm, "income 500 USD salary")

    assert intent.transaction.type == "income"
    assert intent.transaction.currency == "USD"


def test_parse_add_transaction_rejects_missing_amount():
    llm = FakeLLM(
        response=json.dumps(
            {"intent": "add_transaction", "type": "expense", "amount": None, "currency": None, "category": None, "note": None, "period": None}
        )
    )

    assert parse_finance_message(llm, "потратил на еду") is None


def test_parse_add_transaction_rejects_non_positive_amount():
    llm = FakeLLM(
        response=json.dumps(
            {"intent": "add_transaction", "type": "expense", "amount": -10, "currency": None, "category": "X", "note": None, "period": None}
        )
    )

    assert parse_finance_message(llm, "минус десять") is None


def test_parse_balance_query():
    llm = FakeLLM(
        response=json.dumps(
            {"intent": "balance_query", "type": None, "amount": None, "currency": None, "category": None, "note": None, "period": None}
        )
    )

    intent = parse_finance_message(llm, "какой у меня баланс?")

    assert intent.kind == "balance_query"
    assert intent.currency is None


def test_parse_balance_query_with_explicit_currency():
    llm = FakeLLM(
        response=json.dumps(
            {"intent": "balance_query", "type": None, "amount": None, "currency": "USD", "category": None, "note": None, "period": None}
        )
    )

    intent = parse_finance_message(llm, "баланс в долларах?")

    assert intent.kind == "balance_query"
    assert intent.currency == "USD"


def test_parse_report_query_defaults_period_to_month():
    llm = FakeLLM(
        response=json.dumps(
            {"intent": "report_query", "type": None, "amount": None, "currency": None, "category": None, "note": None, "period": None}
        )
    )

    intent = parse_finance_message(llm, "дай отчёт")

    assert intent.kind == "report_query"
    assert intent.period == "month"


def test_parse_report_query_respects_explicit_period():
    llm = FakeLLM(
        response=json.dumps(
            {"intent": "report_query", "type": None, "amount": None, "currency": None, "category": None, "note": None, "period": "week"}
        )
    )

    intent = parse_finance_message(llm, "отчёт за неделю")

    assert intent.period == "week"


def test_parse_other_returns_none():
    llm = FakeLLM(
        response=json.dumps(
            {"intent": "other", "type": None, "amount": None, "currency": None, "category": None, "note": None, "period": None}
        )
    )

    assert parse_finance_message(llm, "как дела?") is None


def test_parse_bad_json_returns_none():
    llm = FakeLLM(response="not json")

    assert parse_finance_message(llm, "hello") is None


def test_parse_strips_code_fence():
    llm = FakeLLM(
        response="```json\n"
        + json.dumps(
            {"intent": "balance_query", "type": None, "amount": None, "currency": None, "category": None, "note": None, "period": None}
        )
        + "\n```"
    )

    intent = parse_finance_message(llm, "баланс?")

    assert intent.kind == "balance_query"
