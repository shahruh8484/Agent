"""Date range for the dashboard's campaign statistics.

Ranges are whole days in the panel's time zone (Tashkent by default);
`start`/`end` are the matching UTC instants, end exclusive.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PRESETS = {
    "today": "Сегодня",
    "yesterday": "Вчера",
    "7d": "7 дней",
    "30d": "30 дней",
    "all": "Всё время",
}


@dataclass
class Period:
    key: str  # a PRESETS key, or "custom"
    day_from: date | None = None
    day_to: date | None = None  # inclusive
    start: datetime | None = None
    end: datetime | None = None

    @property
    def is_all(self) -> bool:
        return self.start is None

    @property
    def since(self) -> str | None:
        return self.start.isoformat(timespec="seconds") if self.start else None

    @property
    def until(self) -> str | None:
        return self.end.isoformat(timespec="seconds") if self.end else None

    def label(self) -> str:
        if self.is_all:
            return "всё время"
        a, b = self.day_from.strftime("%d.%m.%Y"), self.day_to.strftime("%d.%m.%Y")
        return a if a == b else f"{a} — {b}"


def _tz(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def _parse_day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def parse_period(preset: str | None, day_from: str | None, day_to: str | None, tz_name: str,
                 now: datetime | None = None) -> Period:
    tz = _tz(tz_name)
    today = (now or datetime.now(timezone.utc)).astimezone(tz).date()
    a, b = _parse_day(day_from), _parse_day(day_to)
    if a or b:
        key = "custom"
        a, b = a or b, b or a
        if a > b:
            a, b = b, a
    else:
        key = preset if preset in PRESETS else "all"
        if key == "all":
            return Period("all")
        a, b = {
            "today": (today, today),
            "yesterday": (today - timedelta(days=1), today - timedelta(days=1)),
            "7d": (today - timedelta(days=6), today),
            "30d": (today - timedelta(days=29), today),
        }[key]
    start = datetime.combine(a, time.min, tz).astimezone(timezone.utc)
    end = datetime.combine(b + timedelta(days=1), time.min, tz).astimezone(timezone.utc)
    return Period(key, a, b, start, end)
