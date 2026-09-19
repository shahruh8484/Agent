from fbadsagent.finance.models import FinanceTransaction
from fbadsagent.finance.store import FinanceStore, compute_summary, period_range


def make_tx(id="t1", type="expense", amount=100.0, category="Еда", date="2026-09-10", currency="UZS"):
    return FinanceTransaction(
        id=id,
        type=type,
        amount=amount,
        currency=currency,
        category=category,
        note="",
        date=date,
        created_at=f"{date} 12:00 UTC",
        source="web",
    )


def test_empty_store(tmp_path):
    store = FinanceStore(tmp_path / "finance.json")
    assert store.list_transactions() == []


def test_add_and_list_transactions(tmp_path):
    store = FinanceStore(tmp_path / "finance.json")
    store.add_transaction(make_tx("t1", date="2026-09-01"))
    store.add_transaction(make_tx("t2", date="2026-09-05"))

    transactions = store.list_transactions()
    assert [t.id for t in transactions] == ["t2", "t1"]  # newest first


def test_get_transaction(tmp_path):
    store = FinanceStore(tmp_path / "finance.json")
    store.add_transaction(make_tx("t1"))

    assert store.get_transaction("t1").id == "t1"
    assert store.get_transaction("missing") is None


def test_remove_transaction(tmp_path):
    store = FinanceStore(tmp_path / "finance.json")
    store.add_transaction(make_tx("t1"))
    store.add_transaction(make_tx("t2"))
    store.remove_transaction("t1")

    assert [t.id for t in store.list_transactions()] == ["t2"]


def test_list_transactions_filters_by_date_range(tmp_path):
    store = FinanceStore(tmp_path / "finance.json")
    store.add_transaction(make_tx("t1", date="2026-09-01"))
    store.add_transaction(make_tx("t2", date="2026-09-15"))
    store.add_transaction(make_tx("t3", date="2026-09-30"))

    transactions = store.list_transactions(start_date="2026-09-05", end_date="2026-09-20")
    assert [t.id for t in transactions] == ["t2"]


def test_list_transactions_filters_by_type(tmp_path):
    store = FinanceStore(tmp_path / "finance.json")
    store.add_transaction(make_tx("t1", type="income"))
    store.add_transaction(make_tx("t2", type="expense"))

    assert [t.id for t in store.list_transactions(type_filter="income")] == ["t1"]


def test_list_transactions_filters_by_currency(tmp_path):
    store = FinanceStore(tmp_path / "finance.json")
    store.add_transaction(make_tx("t1", currency="UZS"))
    store.add_transaction(make_tx("t2", currency="USD"))

    assert [t.id for t in store.list_transactions(currency_filter="USD")] == ["t2"]


def test_distinct_currencies(tmp_path):
    store = FinanceStore(tmp_path / "finance.json")
    store.add_transaction(make_tx("t1", currency="UZS"))
    store.add_transaction(make_tx("t2", currency="USD"))
    store.add_transaction(make_tx("t3", currency="USD"))

    assert store.distinct_currencies() == ["USD", "UZS"]


def test_period_range_today():
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    assert period_range("today") == (today, today)


def test_period_range_all_returns_no_bounds():
    assert period_range("all") == (None, None)
    assert period_range("unknown") == (None, None)


def test_period_range_week_starts_on_monday():
    from datetime import datetime, timezone

    start, end = period_range("week")
    start_date = datetime.fromisoformat(start).date()
    assert start_date.weekday() == 0
    assert end == datetime.now(timezone.utc).date().isoformat()


def test_period_range_month_starts_on_first():
    start, _end = period_range("month")
    assert start.endswith("-01")


def test_compute_summary_totals_and_balance():
    transactions = [
        make_tx("t1", type="income", amount=1000.0),
        make_tx("t2", type="expense", amount=300.0, category="Еда"),
        make_tx("t3", type="expense", amount=200.0, category="Транспорт"),
    ]

    summary = compute_summary(transactions)

    assert summary["income"] == 1000.0
    assert summary["expense"] == 500.0
    assert summary["balance"] == 500.0
    assert summary["category_totals"] == {"Еда": 300.0, "Транспорт": 200.0}


def test_compute_summary_category_totals_sorted_descending():
    transactions = [
        make_tx("t1", type="expense", amount=50.0, category="Малое"),
        make_tx("t2", type="expense", amount=500.0, category="Большое"),
    ]

    summary = compute_summary(transactions)

    assert list(summary["category_totals"].keys()) == ["Большое", "Малое"]


def test_compute_summary_daily_breakdown():
    transactions = [
        make_tx("t1", type="income", amount=100.0, date="2026-09-01"),
        make_tx("t2", type="expense", amount=40.0, date="2026-09-01"),
        make_tx("t3", type="expense", amount=10.0, date="2026-09-02"),
    ]

    summary = compute_summary(transactions)

    assert summary["daily"] == [
        {"date": "2026-09-01", "income": 100.0, "expense": 40.0},
        {"date": "2026-09-02", "income": 0.0, "expense": 10.0},
    ]


def test_compute_summary_empty():
    summary = compute_summary([])
    assert summary == {
        "income": 0,
        "expense": 0,
        "balance": 0,
        "category_totals": {},
        "daily": [],
    }
