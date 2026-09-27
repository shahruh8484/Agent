"""Settings editable from the dashboard.

Values saved in the panel are stored in the database and override .env,
so budgets can be changed without SSH or a restart. Every consumer reads
`effective(settings, store)`, which layers the overrides on top.
"""
from __future__ import annotations

import json
import re

from amzagent.config import Settings
from amzagent.push.propeller import MIN_DAILY_AMOUNT

OVERRIDES_KEY = "settings_overrides"

# field: (type, min, max). Budgets follow PropellerAds' limits.
EDITABLE: dict[str, tuple[type, float | None, float | None]] = {
    "push_live": (bool, None, None),
    "campaign_daily_budget": (float, MIN_DAILY_AMOUNT, 10_000),
    "max_daily_spend": (float, 0, 100_000),
    "campaigns_per_site": (int, 0, 50),
    "push_bid_cpc": (float, 0.001, 10),
    "push_countries": (str, None, None),
    "kill_min_spend": (float, 0, 10_000),
    "min_amazon_rate": (float, 0, 100),
    "max_cost_per_amazon_click": (float, 0, 100),
    "zone_min_visits": (int, 1, 100_000),
    "zone_min_spend": (float, 0, 10_000),
    "push_creatives": (str, None, None),
    "auto_niches": (int, 0, 50),
    "import_site_size": (int, 1, 500),
}

COUNTRIES_RE = re.compile(r"^[a-z]{2}(,[a-z]{2})*$")


def load_overrides(store) -> dict:
    try:
        data = json.loads(store.get_flag(OVERRIDES_KEY, "{}"))
    except ValueError:
        return {}
    return {k: v for k, v in data.items() if k in EDITABLE}


def effective(settings: Settings, store) -> Settings:
    overrides = load_overrides(store)
    return settings.model_copy(update=overrides) if overrides else settings


def parse_form(form: dict[str, str]) -> tuple[dict, list[str]]:
    """Validate submitted values. Returns (overrides, errors in Russian)."""
    values: dict = {}
    errors: list[str] = []
    for field, (kind, low, high) in EDITABLE.items():
        if kind is bool:
            values[field] = form.get(field) in ("1", "on", "true")
            continue
        raw = (form.get(field) or "").strip()
        if raw == "":
            continue
        if kind is str:
            if field == "push_countries":
                raw = raw.lower().replace(" ", "")
                if not COUNTRIES_RE.match(raw):
                    errors.append("Страны: двухбуквенные коды через запятую, например us,ca,gb")
                    continue
            if field == "push_creatives" and raw not in ("ai", "simple"):
                errors.append("Картинки: ai или simple")
                continue
            values[field] = raw
            continue
        try:
            number = kind(raw.replace(",", "."))
        except ValueError:
            errors.append(f"{field}: нужно число")
            continue
        if low is not None and number < low:
            errors.append(f"{field}: минимум {low:g}")
            continue
        if high is not None and number > high:
            errors.append(f"{field}: максимум {high:g}")
            continue
        values[field] = number
    if (
        "campaign_daily_budget" in values
        and "max_daily_spend" in values
        and values["max_daily_spend"] < values["campaign_daily_budget"]
        and values["max_daily_spend"] > 0
    ):
        errors.append("Общий дневной лимит меньше бюджета одной кампании — ни одна не запустится")
    return values, errors


def save_overrides(store, values: dict) -> None:
    store.set_flag(OVERRIDES_KEY, json.dumps(values))
