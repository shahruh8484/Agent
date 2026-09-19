"""Persists the company's income/expense transactions, plus the summary
math (balance, category breakdown, daily totals) shared by the Telegram
bot and the dashboard.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from fbadsagent.finance.models import FinanceTransaction


class FinanceStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = Lock()
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write({"transactions": []})

    def _read(self) -> dict:
        with self._lock:
            return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        with self._lock:
            self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def list_transactions(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        type_filter: str | None = None,
        currency_filter: str | None = None,
    ) -> list[FinanceTransaction]:
        transactions = [FinanceTransaction(**t) for t in self._read().get("transactions", [])]
        if start_date:
            transactions = [t for t in transactions if t.date >= start_date]
        if end_date:
            transactions = [t for t in transactions if t.date <= end_date]
        if type_filter:
            transactions = [t for t in transactions if t.type == type_filter]
        if currency_filter:
            transactions = [t for t in transactions if t.currency == currency_filter]
        transactions.sort(key=lambda t: (t.date, t.created_at), reverse=True)
        return transactions

    def distinct_currencies(self) -> list[str]:
        return sorted({t.currency for t in self.list_transactions()})

    def get_transaction(self, transaction_id: str) -> FinanceTransaction | None:
        for t in self.list_transactions():
            if t.id == transaction_id:
                return t
        return None

    def add_transaction(self, transaction: FinanceTransaction) -> None:
        data = self._read()
        transactions = data.get("transactions", [])
        transactions.append(transaction.model_dump())
        data["transactions"] = transactions
        self._write(data)

    def remove_transaction(self, transaction_id: str) -> None:
        data = self._read()
        data["transactions"] = [
            t for t in data.get("transactions", []) if t["id"] != transaction_id
        ]
        self._write(data)


def period_range(period: str) -> tuple[str | None, str | None]:
    """Maps "today" | "week" | "month" | "all" to a (start_date, end_date)
    pair of "YYYY-MM-DD" strings (inclusive), in UTC. "all" (or anything
    unrecognized) returns (None, None), meaning no filtering."""
    today = datetime.now(timezone.utc).date()
    if period == "today":
        return today.isoformat(), today.isoformat()
    if period == "week":
        start = today - timedelta(days=today.weekday())
        return start.isoformat(), today.isoformat()
    if period == "month":
        start = today.replace(day=1)
        return start.isoformat(), today.isoformat()
    return None, None


def compute_summary(transactions: list[FinanceTransaction]) -> dict:
    """Income/expense totals, balance, per-category expense breakdown, and
    daily income/expense totals for a list of transactions."""
    income = sum(t.amount for t in transactions if t.type == "income")
    expense = sum(t.amount for t in transactions if t.type == "expense")

    category_totals: dict[str, float] = {}
    for t in transactions:
        if t.type != "expense":
            continue
        key = t.category or "Прочее"
        category_totals[key] = category_totals.get(key, 0.0) + t.amount
    category_totals = dict(sorted(category_totals.items(), key=lambda kv: -kv[1]))

    daily: dict[str, dict[str, float]] = {}
    for t in transactions:
        bucket = daily.setdefault(t.date, {"income": 0.0, "expense": 0.0})
        bucket[t.type] = bucket.get(t.type, 0.0) + t.amount
    daily_list = [
        {"date": date, "income": v["income"], "expense": v["expense"]}
        for date, v in sorted(daily.items())
    ]

    return {
        "income": income,
        "expense": expense,
        "balance": income - expense,
        "category_totals": category_totals,
        "daily": daily_list,
    }
