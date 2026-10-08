"""Import daily traffic stats from a file exported from the CPA network
("Общая статистика", grouped by date) — .xlsx or .csv.

The exact export layout differs between networks, so the file is turned
into a plain table, columns are guessed from their headers, and the owner
confirms which column is the date / leads / valid / approves before
anything is written. Each day is upserted, so re-uploading a newer export
updates days whose approves came in late.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

from finance.db import Repo
from finance.importer import _date, _number

FIELDS = [("date", "Дата"), ("leads", "Лиды (Σ, всего)"), ("valid", "Валидные (Σв)"), ("approves", "Апрувы (✓)")]

# Header words per field, checked in this order (valid before leads: "Σв" contains "Σ").
_HINTS = [
    ("date", ("дата", "date", "день", "day")),
    ("valid", ("σв", "σ в", "валид", "valid")),
    ("approves", ("✓", "✔", "подтв", "approv", "принят", "апрув", "одобр")),
    ("leads", ("σ", "всего", "лиды", "лидов", "conversions", "конверсии", "total", "leads")),
]


class StatsFileError(ValueError):
    pass


def read_table(filename: str, data: bytes) -> list[list[str]]:
    """File bytes -> rows of cell strings (dates as DD.MM.YYYY)."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl

        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:  # corrupt / not really xlsx
            raise StatsFileError(f"Не удалось открыть Excel-файл: {exc}") from exc
        ws = wb.active
        rows = [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)]
        wb.close()
    elif name.endswith((".csv", ".txt", ".tsv")):
        text = _decode(data)
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t") if text.strip() else csv.excel
        rows = [list(r) for r in csv.reader(io.StringIO(text), dialect)]
    elif name.endswith(".xls"):
        raise StatsFileError("Старый формат .xls не поддерживается — откройте файл и сохраните как .xlsx или .csv.")
    else:
        raise StatsFileError("Загрузите файл .xlsx или .csv.")
    rows = [[c.strip() for c in r] for r in rows]
    return [r for r in rows if any(r)]


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _cell_date(text: str) -> str | None:
    # Exports often put a time or a weekday after the date: "08.10.2026 00:00".
    return _date(text) or _date(text.split(" ")[0]) if text else None


def guess_columns(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    """Returns (header_row_index, {field: column}). The header row is the
    last row above the first dated row; the date column is the one with
    most parseable dates."""
    width = max((len(r) for r in rows), default=0)
    date_counts = [sum(1 for r in rows if i < len(r) and _cell_date(r[i])) for i in range(width)]
    date_col = max(range(width), key=lambda i: date_counts[i]) if width else 0
    first_data = next((n for n, r in enumerate(rows) if date_col < len(r) and _cell_date(r[date_col])), 0)
    header_idx = max(first_data - 1, 0)

    # Multi-row headers ("Конверсии" over "Σ | Σв | ✓") — join the rows above data.
    headers = [" ".join(rows[h][i] for h in range(first_data) if i < len(rows[h])).lower() for i in range(width)]
    def is_percent(i: int) -> bool:
        vals = [r[i] for r in rows[first_data:first_data + 10] if i < len(r) and r[i]]
        return any("%" in v for v in vals)

    mapping = {"date": date_col}
    taken = {date_col}
    # Words are tried in priority order across all columns, so the exact
    # "✓" column wins over "Approve %" and the first "Σ" over later ones.
    for field, words in _HINTS[1:]:
        for w in words:
            col = next((i for i, h in enumerate(headers) if i not in taken and not is_percent(i) and w in h), None)
            if col is not None:
                mapping[field] = col
                taken.add(col)
                break
    return header_idx, mapping


def column_options(rows: list[list[str]], header_idx: int) -> list[dict]:
    """Per column: a label from the header rows and a few sample values."""
    width = max((len(r) for r in rows), default=0)
    first_data = header_idx + 1
    opts = []
    for i in range(width):
        label = " / ".join(rows[h][i] for h in range(first_data) if i < len(rows[h]) and rows[h][i])
        samples = [r[i] for r in rows[first_data:first_data + 4] if i < len(r) and r[i]]
        opts.append({"index": i, "label": label or f"Колонка {i + 1}", "samples": ", ".join(samples)})
    return opts


@dataclass
class DayStat:
    date: str
    leads: int
    valid: int
    approves: int


def covered_range(rows: list[list[str]], mapping: dict[str, int]) -> tuple[str, str] | None:
    """First and last date present in the file, empty days included."""
    col = mapping.get("date")
    dates = [d for r in rows if col is not None and col < len(r) and (d := _cell_date(r[col]))]
    return (min(dates), max(dates)) if dates else None


def extract(rows: list[list[str]], mapping: dict[str, int]) -> tuple[list[DayStat], list[str]]:
    days, problems = {}, []
    for n, r in enumerate(rows, start=1):
        get = lambda f: r[mapping[f]] if mapping.get(f) is not None and mapping[f] < len(r) else ""
        d = _cell_date(get("date"))
        if not d:
            continue  # header / "Итого" / blank
        try:
            leads = int(round(_number(get("leads") or "0")))
            approves = int(round(_number(get("approves") or "0")))
            valid = int(round(_number(get("valid")))) if get("valid") else leads
        except ValueError:
            problems.append(f"Строка {n}: не понял числа — {' | '.join(r)}")
            continue
        if not leads and not approves:
            continue  # empty day
        days[d] = DayStat(d, leads, valid, approves)
    return sorted(days.values(), key=lambda s: s.date), problems


def apply(repo: Repo, link_id: int, days: list[DayStat], replace: tuple[str, str] | None = None) -> int:
    """Upsert each day. With `replace`, the link's stats in that date range
    are cleared first — the export is the full truth for its period, so a
    day that is empty there must not keep stale numbers from before."""
    if repo.link(link_id) is None:
        raise ValueError("Связка не найдена.")
    if replace:
        repo.delete_traffic_stats_range(link_id, *replace)
    for s in days:
        repo.upsert_traffic_stat(link_id, s.date, s.leads, s.valid, s.approves, "импорт файла")
    return len(days)


def to_tsv(rows: list[list[str]]) -> str:
    return "\n".join("\t".join(c.replace("\t", " ").replace("\n", " ") for c in r) for r in rows)


def from_tsv(text: str) -> list[list[str]]:
    return [line.split("\t") for line in text.splitlines() if line.strip()]
