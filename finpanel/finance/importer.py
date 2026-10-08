"""Import a cash ledger pasted from a spreadsheet:

    date | приход | расход | name

(rows copied from Google Sheets / Excel arrive tab-separated). Each
distinct name is mapped once to what it is — an advertiser, a web, an
expense category, your own money — and every row becomes a payment or an
expense. Re-importing the same rows is safe: exact duplicates are skipped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from finance.db import Repo

KINDS = [
    ("advertiser", "Рекл (деньги от него)"),
    ("web_traffic", "Веб — трафик"),
    ("web_product", "Веб — мой товар"),
    ("courier", "Курьерка (наложка)"),
    ("expense_traffic", "Расход — трафик"),
    ("expense_product", "Расход — товар"),
    ("expense_general", "Расход — общий"),
    ("owner", "Свои деньги (вложил / вывел)"),
    ("skip", "Пропустить"),
]
KIND_KEYS = {k for k, _ in KINDS}

# Names that are almost always an expense or own money, whatever the sheet.
_EXPENSE_WORDS = ("комисс", "сервер", "хостинг", "домен", "прокси", "фб", "fb", "акк", "ии", "ai", "gpt", "трекер", "зарплат", "прогер", "программист")
_OWNER_WORDS = ("пополнение", "вложен", "свои", "вывод")


@dataclass
class Row:
    line: int
    date: str
    amount_in: float
    amount_out: float
    name: str


def _number(text: str) -> float:
    text = text.strip().replace(" ", "").replace(" ", "").replace("$", "")
    if not text:
        return 0.0
    if "," in text and "." in text:  # 1,234.56
        text = text.replace(",", "")
    text = text.replace(",", ".")
    return float(text)


def _date(text: str) -> str | None:
    text = text.strip()
    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", text)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        y = y + 2000 if y < 100 else y
    else:
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
        if not m:
            return None
        y, mo, d = (int(x) for x in m.groups())
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def parse(text: str) -> tuple[list[Row], list[str]]:
    """Rows without a valid date (headers, totals, blanks) are ignored;
    rows with a date but unreadable numbers are reported."""
    rows, problems = [], []
    for i, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        cells = raw.split("\t") if "\t" in raw else re.split(r";|\s{2,}", raw)
        cells = [c.strip() for c in cells]
        # Find the date cell (column A may be empty when copied with row numbers off).
        idx = next((j for j, c in enumerate(cells) if _date(c)), None)
        if idx is None:
            continue
        rest = cells[idx + 1:] + ["", "", ""]
        d = _date(cells[idx])
        try:
            amount_in = _number(rest[0])
            amount_out = _number(rest[1])
        except ValueError:
            problems.append(f"Строка {i}: не понял суммы — «{raw.strip()}»")
            continue
        name = rest[2].strip()
        if not amount_in and not amount_out:
            continue
        if amount_in and amount_out:
            problems.append(f"Строка {i}: и приход, и расход в одной строке — пропущена")
            continue
        if not name:
            problems.append(f"Строка {i}: нет имени — пропущена")
            continue
        rows.append(Row(i, d, amount_in, amount_out, name))
    return rows, problems


def guess_kind(repo: Repo, name: str, has_in: bool, has_out: bool, aliases: dict[str, str]) -> str:
    key = name.strip().lower()
    if key in aliases:
        return aliases[key]
    if any(w in key for w in _OWNER_WORDS):
        return "owner"
    if any(key == w or key.startswith(w) for w in _EXPENSE_WORDS):
        return "expense_general" if "сервер" in key or key in ("ии", "ai", "gpt") else "expense_traffic"
    if repo.find_advertiser(name) and repo.find_advertiser(name)["name"].lower() == key:
        return "advertiser"
    if repo.find_web(name) and repo.find_web(name)["name"].lower() == key:
        return "web_traffic"
    if has_in and not has_out:
        return "advertiser"
    return "web_traffic"


def summarize(repo: Repo, rows: list[Row]) -> list[dict]:
    """One entry per distinct name, with totals and a guessed kind."""
    aliases = repo.import_aliases()
    names: dict[str, dict] = {}
    for r in rows:
        key = r.name.lower()
        n = names.setdefault(key, {"name": r.name, "count": 0, "total_in": 0.0, "total_out": 0.0})
        n["count"] += 1
        n["total_in"] += r.amount_in
        n["total_out"] += r.amount_out
    for n in names.values():
        n["kind"] = guess_kind(repo, n["name"], n["total_in"] > 0, n["total_out"] > 0, aliases)
    return sorted(names.values(), key=lambda n: (-n["count"], n["name"].lower()))


@dataclass
class ImportResult:
    payments: int = 0
    expenses: int = 0
    duplicates: int = 0
    skipped: int = 0
    created: list[str] = None
    errors: list[str] = None


def _display_name(name: str) -> str:
    return name if any(c.isupper() for c in name) else name[:1].upper() + name[1:]


def apply(repo: Repo, rows: list[Row], mapping: dict[str, str]) -> ImportResult:
    res = ImportResult(created=[], errors=[])
    party_ids: dict[str, int] = {}
    # The same sheet can legitimately have two identical rows on one day
    # (two payments of 253 to the same web). A row is a duplicate only if
    # the books already hold more such records than this import has seen.
    seen: dict[tuple, int] = {}

    def is_duplicate(signature: tuple, existing: int) -> bool:
        seen[signature] = seen.get(signature, 0) + 1
        return existing >= seen[signature]

    for key, kind in mapping.items():
        if kind not in KIND_KEYS:
            raise ValueError(f"Неизвестный тип: {kind}")

    for r in rows:
        key = r.name.lower()
        kind = mapping.get(key, "skip")
        if kind == "skip":
            res.skipped += 1
            continue
        # + money came in, - money went out
        signed = r.amount_in - r.amount_out
        note = "импорт"
        try:
            if kind.startswith("expense_"):
                if signed > 0:
                    res.errors.append(f"Строка {r.line}: «{r.name}» — приход записан как расход, пропущено")
                    continue
                category = _display_name(r.name)
                amount = -signed
                if is_duplicate(("e", r.date, category.lower(), round(amount, 2)),
                                repo.count_expenses(r.date, category, amount)):
                    res.duplicates += 1
                    continue
                repo.add_expense(r.date, kind.removeprefix("expense_"), category, amount, note=note)
                res.expenses += 1
                continue

            party_type, direction, amount = {
                "advertiser": ("advertiser", "traffic", signed),     # money from rekl is +
                "courier": ("courier", "product", signed),
                "owner": ("owner", "general", signed),
                "web_traffic": ("web", "traffic", -signed),         # money to web is +
                "web_product": ("web", "product", -signed),
            }[kind]

            party_id = None
            if party_type in ("advertiser", "web"):
                if key not in party_ids:
                    finder = repo.find_advertiser if party_type == "advertiser" else repo.find_web
                    found = finder(r.name)
                    if found and found["name"].lower() == key:
                        party_ids[key] = found["id"]
                    else:
                        adder = repo.add_advertiser if party_type == "advertiser" else repo.add_web
                        party_ids[key] = adder(_display_name(r.name))
                        res.created.append(("рекл " if party_type == "advertiser" else "веб ") + _display_name(r.name))
                party_id = party_ids[key]

            if is_duplicate(("p", r.date, party_type, party_id, round(amount, 2)),
                            repo.count_payments(r.date, party_type, party_id, amount)):
                res.duplicates += 1
                continue
            repo.add_payment(r.date, direction, party_type, party_id, amount, note=note)
            res.payments += 1
        except ValueError as exc:
            res.errors.append(f"Строка {r.line}: {exc}")

    for key, kind in mapping.items():
        repo.set_import_alias(key, kind)
    return res
