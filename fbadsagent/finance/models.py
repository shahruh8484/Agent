from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class FinanceTransaction(BaseModel):
    """One income or expense record for the company's bookkeeping."""

    id: str
    type: str  # "income" | "expense"
    amount: float  # always positive; sign comes from `type`
    currency: str = "UZS"
    category: str = "Прочее"
    note: str = ""
    date: str  # "YYYY-MM-DD" — the transaction's own date
    created_at: str  # "YYYY-MM-DD HH:MM UTC" — when the record was entered
    source: str = "web"  # "web" | "telegram"
    telegram_user_id: Optional[int] = None
