"""Turns a parsed finance intent (see nlp.py) into a plain-text reply and
the store mutation it implies. Kept free of any Telegram/FastAPI-specific
types so it's usable from the bot, the dashboard, and tests alike.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fbadsagent.config import Settings
from fbadsagent.finance.models import FinanceTransaction
from fbadsagent.finance.nlp import FinanceIntent, TransactionDraft
from fbadsagent.finance.store import FinanceStore, compute_summary, period_range

PERIOD_LABELS = {"today": "сегодня", "week": "эту неделю", "month": "этот месяц", "all": "всё время"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fmt_amount(amount: float, currency: str) -> str:
    return f"{amount:,.0f} {currency}".replace(",", " ")


def add_transaction(
    store: FinanceStore,
    draft: TransactionDraft,
    settings: Settings,
    source: str = "web",
    telegram_user_id: int | None = None,
    date: str | None = None,
) -> FinanceTransaction:
    now = _now()
    transaction = FinanceTransaction(
        id=uuid.uuid4().hex[:12],
        type=draft.type,
        amount=draft.amount,
        currency=draft.currency or settings.finance_default_currency,
        category=draft.category,
        note=draft.note,
        date=date or now[:10],
        created_at=now,
        source=source,
        telegram_user_id=telegram_user_id,
    )
    store.add_transaction(transaction)
    return transaction


def format_add_confirmation(transaction: FinanceTransaction, store: FinanceStore) -> str:
    verb = "Доход" if transaction.type == "income" else "Расход"
    balance = compute_summary(store.list_transactions())["balance"]
    lines = [
        f"{verb} записан: {_fmt_amount(transaction.amount, transaction.currency)} "
        f"— {transaction.category}."
    ]
    if transaction.note:
        lines.append(f"Заметка: {transaction.note}")
    lines.append(f"Текущий баланс: {_fmt_amount(balance, transaction.currency)}.")
    return "\n".join(lines)


def format_balance_reply(store: FinanceStore, currency: str) -> str:
    balance = compute_summary(store.list_transactions())["balance"]
    return f"Текущий баланс: {_fmt_amount(balance, currency)}."


def format_report_reply(store: FinanceStore, period: str, currency: str) -> str:
    start, end = period_range(period)
    summary = compute_summary(store.list_transactions(start_date=start, end_date=end))
    label = PERIOD_LABELS.get(period, period)
    lines = [
        f"Отчёт за {label}:",
        f"Доходы: {_fmt_amount(summary['income'], currency)}",
        f"Расходы: {_fmt_amount(summary['expense'], currency)}",
        f"Итого: {_fmt_amount(summary['balance'], currency)}",
    ]
    if summary["category_totals"]:
        lines.append("")
        lines.append("Расходы по категориям:")
        for category, amount in summary["category_totals"].items():
            lines.append(f"  {category}: {_fmt_amount(amount, currency)}")
    return "\n".join(lines)


def handle_finance_intent(
    store: FinanceStore,
    settings: Settings,
    intent: FinanceIntent,
    source: str = "web",
    telegram_user_id: int | None = None,
) -> str:
    if intent.kind == "add_transaction":
        transaction = add_transaction(
            store, intent.transaction, settings, source=source, telegram_user_id=telegram_user_id
        )
        return format_add_confirmation(transaction, store)
    if intent.kind == "balance_query":
        return format_balance_reply(store, settings.finance_default_currency)
    if intent.kind == "report_query":
        return format_report_reply(store, intent.period or "month", settings.finance_default_currency)
    raise ValueError(f"Unhandled finance intent kind: {intent.kind}")
