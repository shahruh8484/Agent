"""Turns a free-text message (any language, any phrasing) into a finance
action: log a transaction, or answer a balance/report question — so the
Telegram bot doesn't need rigid command syntax.

Parsing is LLM-based rather than regex — a strict JSON system prompt keeps
it a plain classifier, not something that can be prompt-injected into
taking other actions.
"""
from __future__ import annotations

import json

from fbadsagent.llm.provider import LLMProvider

FINANCE_SYSTEM_PROMPT = (
    "You are the parser for a small company's finance-tracking bot. Classify "
    "the user's message and respond with STRICT JSON only, matching: "
    '{"intent": "add_transaction" | "balance_query" | "report_query" | "other", '
    '"type": "income" | "expense" | null, "amount": number | null, '
    '"currency": string | null, "category": string | null, "note": string | null, '
    '"period": "today" | "week" | "month" | "all" | null}. '
    'For intent="add_transaction": type and amount (a positive number) are '
    "required. category is a short 1-3 word category guessed from context, in "
    "the same language as the message (e.g. \"Еда\", \"Зарплата\", \"Аренда\", "
    '"Реклама", "Транспорт"). note is a brief free-text description (empty '
    "string if none). currency is the ISO-like currency code only if the "
    'message explicitly names one (e.g. "USD", "UZS"), otherwise null. For '
    'intent="report_query", set period (default "month" if the message asks '
    'for a report/summary without naming a period). For intent="balance_query", '
    "period may be null (it means all-time balance). For balance_query/"
    "report_query, currency is the ISO-like code only if the message "
    'explicitly names one (e.g. "balance in USD"), otherwise null — null means '
    "the caller's default currency. If the message isn't about logging or "
    'checking company finances at all, use intent="other" with every other '
    "field null. No prose outside the JSON."
)


class TransactionDraft:
    def __init__(self, type_: str, amount: float, currency: str | None, category: str, note: str):
        self.type = type_
        self.amount = amount
        self.currency = currency
        self.category = category or "Прочее"
        self.note = note or ""


class FinanceIntent:
    def __init__(
        self,
        kind: str,
        transaction: TransactionDraft | None = None,
        period: str | None = None,
        currency: str | None = None,
    ):
        self.kind = kind  # "add_transaction" | "balance_query" | "report_query"
        self.transaction = transaction
        self.period = period
        self.currency = currency  # balance_query/report_query only; None = caller's default


def parse_finance_message(llm: LLMProvider, message: str) -> FinanceIntent | None:
    """None means the message isn't a finance action — callers should fall
    back to a normal conversational reply in that case."""
    raw = llm.generate(FINANCE_SYSTEM_PROMPT, message, max_tokens=250)
    try:
        data = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError:
        return None

    intent = data.get("intent")
    if intent == "add_transaction":
        tx_type = data.get("type")
        amount = data.get("amount")
        if tx_type not in ("income", "expense") or not isinstance(amount, (int, float)) or amount <= 0:
            return None
        return FinanceIntent(
            "add_transaction",
            transaction=TransactionDraft(
                type_=tx_type,
                amount=float(amount),
                currency=(data.get("currency") or None),
                category=str(data.get("category") or "Прочее"),
                note=str(data.get("note") or ""),
            ),
        )
    if intent == "balance_query":
        return FinanceIntent("balance_query", currency=(data.get("currency") or None))
    if intent == "report_query":
        period = data.get("period") if data.get("period") in ("today", "week", "month", "all") else "month"
        return FinanceIntent("report_query", period=period, currency=(data.get("currency") or None))
    return None


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return text
