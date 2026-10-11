"""USD → UZS rate from the Central Bank of Uzbekistan (cbu.uz), applied to
the panel's rate setting once a day when auto-update is on. Only new
records use the setting: orders and payments keep the rate they were
entered with, so history is never recalculated."""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import date
from typing import Callable

from finance.db import Repo

logger = logging.getLogger(__name__)

CBU_URL = "https://cbu.uz/uz/arkhiv-kursov-valyut/json/USD/"


class RateError(RuntimeError):
    pass


def fetch_cbu_rate(timeout: float = 10) -> tuple[float, str]:
    """(rate, date the CB set it for, as DD.MM.YYYY)."""
    try:
        with urllib.request.urlopen(CBU_URL, timeout=timeout) as resp:
            data = json.load(resp)
        item = next(x for x in data if x.get("Ccy") == "USD")
        return float(item["Rate"]), str(item.get("Date", ""))
    except Exception as exc:  # network, JSON, unexpected shape
        raise RateError(f"Не удалось получить курс ЦБ: {exc}") from exc


def update_rate(repo: Repo, today: date, fetch: Callable[[], tuple[float, str]] = fetch_cbu_rate) -> float:
    """Fetch the CB rate, apply the correction %, store it. Returns the new rate."""
    cb_rate, cb_date = fetch()
    if cb_rate <= 0:
        raise RateError("ЦБ вернул нулевой курс.")
    rate = round(cb_rate * (1 + repo.settings()["rate_adjust_pct"] / 100), 2)
    repo.set_setting("usd_uzs_rate", rate)
    repo.set_meta("rate_cb", f"{cb_rate}")
    repo.set_meta("rate_cb_date", cb_date)
    repo.set_meta("rate_checked", today.isoformat())
    return rate


def auto_update_due(repo: Repo, today: date) -> bool:
    return bool(repo.settings()["rate_auto"]) and repo.meta("rate_checked") != today.isoformat()


def auto_update(repo: Repo, today: date, fetch: Callable[[], tuple[float, str]] = fetch_cbu_rate) -> None:
    """Once a day. On failure the old rate stays and the caller retries later."""
    if not auto_update_due(repo, today):
        return
    try:
        update_rate(repo, today, fetch)
    except RateError:
        logger.warning("Auto rate update failed", exc_info=True)
