"""SQLite storage for everything the panel tracks.

All money is stored in USD (amount_usd). Things that happen in sum
(orders, courier payouts, delivery) also keep the original amount_uzs and
the exchange rate used at the time, so changing the rate later never
rewrites history.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    terms TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS advertisers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    terms TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- Direction 1: a web -> offer -> advertiser chain.
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY,
    web_id INTEGER NOT NULL REFERENCES webs(id),
    advertiser_id INTEGER NOT NULL REFERENCES advertisers(id),
    offer TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- Rates change over time: each row applies from valid_from onwards.
CREATE TABLE IF NOT EXISTS link_rates (
    id INTEGER PRIMARY KEY,
    link_id INTEGER NOT NULL REFERENCES links(id) ON DELETE CASCADE,
    valid_from TEXT NOT NULL,
    adv_pay_type TEXT NOT NULL,      -- 'approve' | 'valid'
    adv_rate REAL NOT NULL,
    guarantee_pct REAL NOT NULL DEFAULT 0,
    web_pay_type TEXT NOT NULL,      -- 'approve' | 'lead'
    web_rate REAL NOT NULL,
    -- Guarantee you pass on to the web (paid per approve): the web is paid
    -- for max(approves, leads * pct) just like the rekl pays you.
    web_guarantee_pct REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS traffic_stats (
    id INTEGER PRIMARY KEY,
    link_id INTEGER NOT NULL REFERENCES links(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    leads INTEGER NOT NULL DEFAULT 0,
    valid INTEGER NOT NULL DEFAULT 0,
    approves INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    UNIQUE(link_id, date)
);

-- Money actually moved. party_type decides the sign:
--   advertiser -> money in (prepayment from a rekl)
--   courier    -> money in (cash-on-delivery payout from the delivery service)
--   web        -> money out (payment to a web, per direction)
--   owner      -> your own money put in (+) or taken out (-); not profit
-- A negative amount is a refund in the opposite direction.
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    direction TEXT NOT NULL,         -- 'traffic' | 'product' | 'general' (owner)
    party_type TEXT NOT NULL,        -- 'advertiser' | 'web' | 'courier' | 'owner'
    party_id INTEGER,
    amount_usd REAL NOT NULL,
    amount_uzs REAL,
    rate REAL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    direction TEXT NOT NULL,         -- 'traffic' | 'product' | 'general'
    category TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    amount_uzs REAL,
    rate REAL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- Direction 2: own product.
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    unit_cost_usd REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_web_rates (
    id INTEGER PRIMARY KEY,
    web_id INTEGER NOT NULL REFERENCES webs(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    valid_from TEXT NOT NULL,
    pay_type TEXT NOT NULL,          -- 'approve' | 'lead'
    rate_usd REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS product_stats (
    id INTEGER PRIMARY KEY,
    web_id INTEGER NOT NULL REFERENCES webs(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    date TEXT NOT NULL,
    leads INTEGER NOT NULL DEFAULT 0,
    approves INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    UNIQUE(web_id, product_id, date)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    ship_date TEXT NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id),
    web_id INTEGER REFERENCES webs(id),
    qty INTEGER NOT NULL,
    amount_uzs REAL NOT NULL,
    rate REAL NOT NULL,
    unit_cost_usd REAL NOT NULL,
    delivery_uzs REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'shipped',   -- 'shipped' | 'delivered' | 'returned'
    status_date TEXT,
    operator_pct REAL,
    tax_pct REAL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_moves (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty INTEGER NOT NULL,                     -- + in, - out
    kind TEXT NOT NULL,                       -- 'purchase' | 'adjust' | 'ship' | 'return'
    order_id INTEGER REFERENCES orders(id) ON DELETE CASCADE,
    cost_usd REAL NOT NULL DEFAULT 0,         -- cash paid for a purchase
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- Earned/owed amounts entered by hand when there are no lead/approve
-- stats for a period (e.g. history before the panel): an advertiser accrual
-- is revenue he owes you for traffic, a web accrual is what you owe the web.
CREATE TABLE IF NOT EXISTS accruals (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    direction TEXT NOT NULL,         -- 'traffic' | 'product'
    party_type TEXT NOT NULL,        -- 'advertiser' | 'web'
    party_id INTEGER NOT NULL,
    amount_usd REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- Reconciliation points: "on <date> the balance with this party was X".
-- Balances start from X and only count what is dated after <date>.
CREATE TABLE IF NOT EXISTS balance_checkpoints (
    id INTEGER PRIMARY KEY,
    party_type TEXT NOT NULL,        -- 'advertiser' | 'web'
    party_id INTEGER NOT NULL,
    direction TEXT NOT NULL,         -- 'traffic' | 'product'
    date TEXT NOT NULL,
    balance REAL NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(party_type, party_id, direction)
);

-- Remembered "this name in my spreadsheet means X" choices from imports.
CREATE TABLE IF NOT EXISTS import_aliases (
    name TEXT PRIMARY KEY COLLATE NOCASE,
    kind TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    actions_json TEXT,
    actions_status TEXT,             -- NULL | 'pending' | 'applied' | 'rejected'
    actions_result TEXT,
    created_at TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "usd_uzs_rate": "11500",
    "operator_pct": "15",
    "tax_pct": "0",
    "restock_days": "10",
    "advertiser_low_days": "2",
}


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


class DB:
    """Thin wrapper over one SQLite connection, serialized with a lock
    (the panel is single-user, so this is plenty)."""

    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()
            for key, value in DEFAULT_SETTINGS.items():
                self._conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value)
                )
            self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(link_rates)")}
        if "web_guarantee_pct" not in cols:
            self._conn.execute("ALTER TABLE link_rates ADD COLUMN web_guarantee_pct REAL NOT NULL DEFAULT 0")

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple | list = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid


@dataclass
class Rate:
    adv_pay_type: str
    adv_rate: float
    guarantee_pct: float
    web_pay_type: str
    web_rate: float
    web_guarantee_pct: float = 0.0


class Repo:
    """All reads/writes the panel, the reports and the chat assistant use."""

    def __init__(self, db: DB):
        self.db = db

    # --- settings ---

    def settings(self) -> dict[str, float]:
        return {r["key"]: float(r["value"]) for r in self.db.query("SELECT * FROM settings")}

    def set_setting(self, key: str, value: float) -> None:
        if key not in DEFAULT_SETTINGS:
            raise ValueError(f"Неизвестная настройка: {key}")
        self.db.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def usd_uzs_rate(self) -> float:
        return self.settings()["usd_uzs_rate"]

    # --- webs / advertisers ---

    def add_web(self, name: str, terms: str = "", note: str = "") -> int:
        return self._add_party("webs", name, terms, note)

    def add_advertiser(self, name: str, terms: str = "", note: str = "") -> int:
        return self._add_party("advertisers", name, terms, note)

    def _add_party(self, table: str, name: str, terms: str, note: str) -> int:
        name = name.strip()
        if not name:
            raise ValueError("Имя не может быть пустым.")
        existing = self._find(table, name)
        if existing and existing["name"].casefold() != name.casefold():
            existing = None
        if existing:
            # Blank fields keep what was there (re-adding a web from another
            # form must not wipe its terms or network name).
            self.db.execute(
                f"UPDATE {table} SET terms = ?, note = ? WHERE id = ?",
                (terms.strip() or existing["terms"], note.strip() or existing["note"], existing["id"]),
            )
            return existing["id"]
        return self.db.execute(
            f"INSERT INTO {table}(name, terms, note, created_at) VALUES (?, ?, ?, ?)",
            (name, terms.strip(), note.strip(), now_str()),
        )

    def webs(self) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM webs ORDER BY name")

    def advertisers(self) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM advertisers ORDER BY name")

    def find_web(self, name: str) -> sqlite3.Row | None:
        return self._find("webs", name)

    def find_advertiser(self, name: str) -> sqlite3.Row | None:
        return self._find("advertisers", name)

    def find_product(self, name: str) -> sqlite3.Row | None:
        return self._find("products", name)

    def _find(self, table: str, name: str) -> sqlite3.Row | None:
        """Exact name match ignoring case, else a unique partial match.
        Done in Python: SQLite's NOCASE/lower() only fold ASCII, so
        "макс" would not match "Макс"."""
        key = (name or "").strip().casefold()
        if not key:
            return None
        rows = self.db.query(f"SELECT * FROM {table}")
        exact = [r for r in rows if r["name"].casefold() == key]
        if exact:
            return exact[0]
        partial = [r for r in rows if key in r["name"].casefold()]
        return partial[0] if len(partial) == 1 else None

    # --- links (direction 1) ---

    def add_link(
        self,
        web_id: int,
        advertiser_id: int,
        offer: str,
        valid_from: str,
        adv_pay_type: str,
        adv_rate: float,
        guarantee_pct: float,
        web_pay_type: str,
        web_rate: float,
        web_guarantee_pct: float = 0,
    ) -> int:
        """Create a link — or, if this web → rekl (offer) link already
        exists, just add the rates from valid_from to it, so the same link
        never ends up twice."""
        existing = self.same_link(web_id, advertiser_id, offer)
        if existing:
            link_id = existing["id"]
        else:
            link_id = self.db.execute(
                "INSERT INTO links(web_id, advertiser_id, offer, created_at) VALUES (?, ?, ?, ?)",
                (web_id, advertiser_id, offer.strip(), now_str()),
            )
        self.set_link_rate(
            link_id, valid_from, adv_pay_type, adv_rate, guarantee_pct, web_pay_type, web_rate,
            web_guarantee_pct,
        )
        return link_id

    def set_link_rate(
        self,
        link_id: int,
        valid_from: str,
        adv_pay_type: str,
        adv_rate: float,
        guarantee_pct: float,
        web_pay_type: str,
        web_rate: float,
        web_guarantee_pct: float = 0,
    ) -> None:
        if adv_pay_type not in ("approve", "valid"):
            raise ValueError("Рекл платит за 'approve' или 'valid'.")
        if web_pay_type not in ("approve", "lead"):
            raise ValueError("Вебу платим за 'approve' или 'lead'.")
        self.db.execute("DELETE FROM link_rates WHERE link_id = ? AND valid_from = ?", (link_id, valid_from))
        self.db.execute(
            "INSERT INTO link_rates(link_id, valid_from, adv_pay_type, adv_rate, guarantee_pct, "
            "web_pay_type, web_rate, web_guarantee_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (link_id, valid_from, adv_pay_type, adv_rate, guarantee_pct or 0, web_pay_type, web_rate,
             web_guarantee_pct or 0),
        )

    def links(self) -> list[sqlite3.Row]:
        return self.db.query(
            "SELECT l.*, w.name AS web_name, a.name AS advertiser_name FROM links l "
            "JOIN webs w ON w.id = l.web_id JOIN advertisers a ON a.id = l.advertiser_id "
            "ORDER BY a.name, w.name, l.offer"
        )

    def link(self, link_id: int) -> sqlite3.Row | None:
        return self.db.one(
            "SELECT l.*, w.name AS web_name, a.name AS advertiser_name FROM links l "
            "JOIN webs w ON w.id = l.web_id JOIN advertisers a ON a.id = l.advertiser_id "
            "WHERE l.id = ?",
            (link_id,),
        )

    def find_link(self, web_name: str, advertiser_name: str, offer: str = "") -> sqlite3.Row | None:
        web = self.find_web(web_name)
        adv = self.find_advertiser(advertiser_name)
        if not web or not adv:
            return None
        rows = self.db.query(
            "SELECT * FROM links WHERE web_id = ? AND advertiser_id = ?", (web["id"], adv["id"])
        )
        if offer:
            exact = [r for r in rows if r["offer"].lower() == offer.strip().lower()]
            if exact:
                rows = exact
        return rows[0] if len(rows) == 1 else None

    def link_rates(self, link_id: int) -> list[sqlite3.Row]:
        return self.db.query(
            "SELECT * FROM link_rates WHERE link_id = ? ORDER BY valid_from", (link_id,)
        )

    def rate_on(self, link_id: int, date: str) -> Rate | None:
        rows = self.link_rates(link_id)
        if not rows:
            return None
        chosen = rows[0]
        for r in rows:
            if r["valid_from"] <= date:
                chosen = r
        return Rate(
            chosen["adv_pay_type"],
            chosen["adv_rate"],
            chosen["guarantee_pct"],
            chosen["web_pay_type"],
            chosen["web_rate"],
            chosen["web_guarantee_pct"],
        )

    def same_link(self, web_id: int, advertiser_id: int, offer: str) -> sqlite3.Row | None:
        key = offer.strip().casefold()
        for row in self.db.query(
            "SELECT * FROM links WHERE web_id = ? AND advertiser_id = ? ORDER BY id", (web_id, advertiser_id)
        ):
            if row["offer"].strip().casefold() == key:
                return row
        return None

    def delete_link(self, link_id: int) -> None:
        self.db.execute("DELETE FROM traffic_stats WHERE link_id = ?", (link_id,))
        self.db.execute("DELETE FROM link_rates WHERE link_id = ?", (link_id,))
        self.db.execute("DELETE FROM links WHERE id = ?", (link_id,))

    def delete_link_rate(self, rate_id: int) -> None:
        self.db.execute("DELETE FROM link_rates WHERE id = ?", (rate_id,))

    def all_link_rates(self) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM link_rates ORDER BY link_id, valid_from")

    def set_link_active(self, link_id: int, active: bool) -> None:
        self.db.execute("UPDATE links SET active = ? WHERE id = ?", (1 if active else 0, link_id))

    def upsert_traffic_stat(
        self, link_id: int, date: str, leads: int, valid: int, approves: int, note: str = ""
    ) -> None:
        if min(leads, valid, approves) < 0:
            raise ValueError("Числа не могут быть отрицательными.")
        self.db.execute(
            "INSERT INTO traffic_stats(link_id, date, leads, valid, approves, note) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(link_id, date) DO UPDATE SET "
            "leads = excluded.leads, valid = excluded.valid, approves = excluded.approves, "
            "note = excluded.note",
            (link_id, date, leads, valid, approves, note.strip()),
        )

    def traffic_stats(self, start: str | None = None, end: str | None = None) -> list[sqlite3.Row]:
        sql, params = _date_filter("SELECT * FROM traffic_stats", "date", start, end)
        return self.db.query(sql + " ORDER BY date DESC, link_id", params)

    def delete_traffic_stats_range(self, link_id: int, start: str, end: str) -> None:
        self.db.execute(
            "DELETE FROM traffic_stats WHERE link_id = ? AND date >= ? AND date <= ?", (link_id, start, end)
        )

    def delete_traffic_stat(self, stat_id: int) -> None:
        self.db.execute("DELETE FROM traffic_stats WHERE id = ?", (stat_id,))

    # --- payments & expenses ---

    def add_payment(
        self,
        date: str,
        direction: str,
        party_type: str,
        party_id: int | None,
        amount_usd: float,
        amount_uzs: float | None = None,
        rate: float | None = None,
        note: str = "",
    ) -> int:
        if party_type == "owner":
            direction = "general"
        elif direction not in ("traffic", "product"):
            raise ValueError("Направление: traffic или product.")
        if party_type not in ("advertiser", "web", "courier", "owner"):
            raise ValueError("Тип платежа: advertiser, web, courier или owner.")
        if not amount_usd:
            raise ValueError("Сумма не может быть нулевой.")
        return self.db.execute(
            "INSERT INTO payments(date, direction, party_type, party_id, amount_usd, amount_uzs, "
            "rate, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (date, direction, party_type, party_id, round(amount_usd, 2), amount_uzs, rate, note.strip(), now_str()),
        )

    def payments(self, start: str | None = None, end: str | None = None) -> list[sqlite3.Row]:
        sql, params = _date_filter(
            "SELECT p.*, COALESCE(w.name, a.name, CASE p.party_type WHEN 'owner' THEN 'Свои деньги' "
            "ELSE 'Курьерка' END) AS party_name FROM payments p "
            "LEFT JOIN webs w ON p.party_type = 'web' AND w.id = p.party_id "
            "LEFT JOIN advertisers a ON p.party_type = 'advertiser' AND a.id = p.party_id",
            "p.date",
            start,
            end,
        )
        return self.db.query(sql + " ORDER BY p.date DESC, p.id DESC", params)

    def delete_payment(self, payment_id: int) -> None:
        self.db.execute("DELETE FROM payments WHERE id = ?", (payment_id,))

    def add_expense(
        self,
        date: str,
        direction: str,
        category: str,
        amount_usd: float,
        amount_uzs: float | None = None,
        rate: float | None = None,
        note: str = "",
    ) -> int:
        if direction not in ("traffic", "product", "general"):
            raise ValueError("Направление: traffic, product или general.")
        if amount_usd <= 0:
            raise ValueError("Сумма расхода должна быть больше нуля.")
        return self.db.execute(
            "INSERT INTO expenses(date, direction, category, amount_usd, amount_uzs, rate, note, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (date, direction, category.strip() or "Прочее", round(amount_usd, 2), amount_uzs, rate, note.strip(), now_str()),
        )

    def expenses(self, start: str | None = None, end: str | None = None) -> list[sqlite3.Row]:
        sql, params = _date_filter("SELECT * FROM expenses", "date", start, end)
        return self.db.query(sql + " ORDER BY date DESC, id DESC", params)

    def delete_expense(self, expense_id: int) -> None:
        self.db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))

    # --- products (direction 2) ---

    def add_product(self, name: str, unit_cost_usd: float, initial_stock: int = 0, date: str = "") -> int:
        name = name.strip()
        if not name:
            raise ValueError("Название товара не может быть пустым.")
        existing = self.find_product(name)
        if existing and existing["name"].casefold() == name.casefold():
            self.db.execute("UPDATE products SET unit_cost_usd = ? WHERE id = ?", (unit_cost_usd, existing["id"]))
            return existing["id"]
        product_id = self.db.execute(
            "INSERT INTO products(name, unit_cost_usd, created_at) VALUES (?, ?, ?)",
            (name, unit_cost_usd, now_str()),
        )
        if initial_stock:
            self.add_stock_move(date, product_id, initial_stock, "adjust", note="Начальный остаток")
        return product_id

    def products(self) -> list[sqlite3.Row]:
        return self.db.query("SELECT * FROM products ORDER BY name")

    def product(self, product_id: int) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM products WHERE id = ?", (product_id,))

    def set_product_web_rate(
        self, web_id: int, product_id: int, valid_from: str, pay_type: str, rate_usd: float
    ) -> None:
        if pay_type not in ("approve", "lead"):
            raise ValueError("Вебу платим за 'approve' или 'lead'.")
        self.db.execute(
            "DELETE FROM product_web_rates WHERE web_id = ? AND product_id = ? AND valid_from = ?",
            (web_id, product_id, valid_from),
        )
        self.db.execute(
            "INSERT INTO product_web_rates(web_id, product_id, valid_from, pay_type, rate_usd) "
            "VALUES (?, ?, ?, ?, ?)",
            (web_id, product_id, valid_from, pay_type, rate_usd),
        )

    def product_web_rates(self) -> list[sqlite3.Row]:
        return self.db.query(
            "SELECT r.*, w.name AS web_name, p.name AS product_name FROM product_web_rates r "
            "JOIN webs w ON w.id = r.web_id JOIN products p ON p.id = r.product_id "
            "ORDER BY p.name, w.name, r.valid_from"
        )

    def product_rate_on(self, web_id: int, product_id: int, date: str) -> tuple[str, float] | None:
        rows = self.db.query(
            "SELECT * FROM product_web_rates WHERE web_id = ? AND product_id = ? ORDER BY valid_from",
            (web_id, product_id),
        )
        if not rows:
            return None
        chosen = rows[0]
        for r in rows:
            if r["valid_from"] <= date:
                chosen = r
        return chosen["pay_type"], chosen["rate_usd"]

    def upsert_product_stat(
        self, web_id: int, product_id: int, date: str, leads: int, approves: int, note: str = ""
    ) -> None:
        if min(leads, approves) < 0:
            raise ValueError("Числа не могут быть отрицательными.")
        self.db.execute(
            "INSERT INTO product_stats(web_id, product_id, date, leads, approves, note) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(web_id, product_id, date) DO UPDATE SET "
            "leads = excluded.leads, approves = excluded.approves, note = excluded.note",
            (web_id, product_id, date, leads, approves, note.strip()),
        )

    def product_stats(self, start: str | None = None, end: str | None = None) -> list[sqlite3.Row]:
        sql, params = _date_filter(
            "SELECT s.*, w.name AS web_name, p.name AS product_name FROM product_stats s "
            "JOIN webs w ON w.id = s.web_id JOIN products p ON p.id = s.product_id",
            "s.date",
            start,
            end,
        )
        return self.db.query(sql + " ORDER BY s.date DESC", params)

    def delete_product_stat(self, stat_id: int) -> None:
        self.db.execute("DELETE FROM product_stats WHERE id = ?", (stat_id,))

    # --- orders & stock ---

    def add_order(
        self,
        ship_date: str,
        product_id: int,
        qty: int,
        amount_uzs: float,
        delivery_uzs: float = 0,
        web_id: int | None = None,
        rate: float | None = None,
        note: str = "",
    ) -> int:
        product = self.product(product_id)
        if product is None:
            raise ValueError("Товар не найден.")
        if qty <= 0 or amount_uzs <= 0:
            raise ValueError("Количество и сумма заказа должны быть больше нуля.")
        order_id = self.db.execute(
            "INSERT INTO orders(ship_date, product_id, web_id, qty, amount_uzs, rate, unit_cost_usd, "
            "delivery_uzs, status, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'shipped', ?, ?)",
            (
                ship_date,
                product_id,
                web_id,
                qty,
                amount_uzs,
                rate or self.usd_uzs_rate(),
                product["unit_cost_usd"],
                delivery_uzs or 0,
                note.strip(),
                now_str(),
            ),
        )
        self.add_stock_move(ship_date, product_id, -qty, "ship", order_id=order_id)
        return order_id

    def set_order_status(self, order_id: int, status: str, date: str, delivery_uzs: float | None = None) -> None:
        if status not in ("shipped", "delivered", "returned"):
            raise ValueError("Статус: shipped, delivered или returned.")
        order = self.order(order_id)
        if order is None:
            raise ValueError(f"Заказ #{order_id} не найден.")
        s = self.settings()
        operator_pct = s["operator_pct"] if status == "delivered" else None
        tax_pct = s["tax_pct"] if status == "delivered" else None
        self.db.execute(
            "UPDATE orders SET status = ?, status_date = ?, operator_pct = ?, tax_pct = ?, "
            "delivery_uzs = COALESCE(?, delivery_uzs) WHERE id = ?",
            (status, None if status == "shipped" else date, operator_pct, tax_pct, delivery_uzs, order_id),
        )
        # A returned parcel goes back on the shelf.
        self.db.execute("DELETE FROM stock_moves WHERE order_id = ? AND kind = 'return'", (order_id,))
        if status == "returned":
            self.add_stock_move(date, order["product_id"], order["qty"], "return", order_id=order_id)

    def order(self, order_id: int) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM orders WHERE id = ?", (order_id,))

    def orders(self, status: str | None = None, limit: int = 300) -> list[sqlite3.Row]:
        sql = (
            "SELECT o.*, p.name AS product_name, w.name AS web_name FROM orders o "
            "JOIN products p ON p.id = o.product_id LEFT JOIN webs w ON w.id = o.web_id"
        )
        params: list = []
        if status:
            sql += " WHERE o.status = ?"
            params.append(status)
        return self.db.query(sql + " ORDER BY o.id DESC LIMIT ?", params + [limit])

    def delete_order(self, order_id: int) -> None:
        self.db.execute("DELETE FROM stock_moves WHERE order_id = ?", (order_id,))
        self.db.execute("DELETE FROM orders WHERE id = ?", (order_id,))

    def add_stock_move(
        self,
        date: str,
        product_id: int,
        qty: int,
        kind: str,
        order_id: int | None = None,
        cost_usd: float = 0,
        note: str = "",
    ) -> int:
        return self.db.execute(
            "INSERT INTO stock_moves(date, product_id, qty, kind, order_id, cost_usd, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (date, product_id, qty, kind, order_id, cost_usd, note.strip(), now_str()),
        )

    def stock_moves(self, product_id: int | None = None, limit: int = 200) -> list[sqlite3.Row]:
        sql = "SELECT m.*, p.name AS product_name FROM stock_moves m JOIN products p ON p.id = m.product_id"
        params: list = []
        if product_id:
            sql += " WHERE m.product_id = ?"
            params.append(product_id)
        return self.db.query(sql + " ORDER BY m.date DESC, m.id DESC LIMIT ?", params + [limit])

    def stock_purchases(self, start: str | None = None, end: str | None = None) -> list[sqlite3.Row]:
        sql = (
            "SELECT m.*, p.name AS product_name FROM stock_moves m JOIN products p ON p.id = m.product_id "
            "WHERE m.kind = 'purchase'"
        )
        params: list = []
        if start:
            sql += " AND m.date >= ?"
            params.append(start)
        if end:
            sql += " AND m.date <= ?"
            params.append(end)
        return self.db.query(sql + " ORDER BY m.date DESC, m.id DESC", params)

    def delete_stock_move(self, move_id: int) -> None:
        # Only manual moves — ship/return moves belong to their order.
        self.db.execute(
            "DELETE FROM stock_moves WHERE id = ? AND kind IN ('purchase', 'adjust')", (move_id,)
        )

    def count_payments(self, date: str, party_type: str, party_id: int | None, amount_usd: float) -> int:
        return self.db.one(
            "SELECT COUNT(*) AS n FROM payments WHERE date = ? AND party_type = ? AND party_id IS ? "
            "AND ABS(amount_usd - ?) < 0.005",
            (date, party_type, party_id, amount_usd),
        )["n"]

    def count_expenses(self, date: str, category: str, amount_usd: float) -> int:
        return self.db.one(
            "SELECT COUNT(*) AS n FROM expenses WHERE date = ? AND category = ? COLLATE NOCASE "
            "AND ABS(amount_usd - ?) < 0.005",
            (date, category, amount_usd),
        )["n"]

    # --- manual accruals ---

    def add_accrual(self, date: str, direction: str, party_type: str, party_id: int, amount_usd: float, note: str = "") -> int:
        if party_type not in ("advertiser", "web"):
            raise ValueError("Начисление: для рекла или веба.")
        if party_type == "advertiser":
            direction = "traffic"
        if direction not in ("traffic", "product"):
            raise ValueError("Направление: traffic или product.")
        if not amount_usd:
            raise ValueError("Сумма не может быть нулевой.")
        return self.db.execute(
            "INSERT INTO accruals(date, direction, party_type, party_id, amount_usd, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (date, direction, party_type, party_id, round(amount_usd, 2), note.strip(), now_str()),
        )

    def accruals(self, start: str | None = None, end: str | None = None) -> list[sqlite3.Row]:
        sql, params = _date_filter(
            "SELECT c.*, COALESCE(w.name, a.name) AS party_name FROM accruals c "
            "LEFT JOIN webs w ON c.party_type = 'web' AND w.id = c.party_id "
            "LEFT JOIN advertisers a ON c.party_type = 'advertiser' AND a.id = c.party_id",
            "c.date",
            start,
            end,
        )
        return self.db.query(sql + " ORDER BY c.date DESC, c.id DESC", params)

    def delete_accrual(self, accrual_id: int) -> None:
        self.db.execute("DELETE FROM accruals WHERE id = ?", (accrual_id,))

    def redate_reconcile_accruals(self, new_date: str) -> int:
        """Move every reconciliation correction to one date. They fix up
        the whole history, so booking them on the day of the reconcile
        makes that month's profit look wrong."""
        n = self.db.one("SELECT COUNT(*) AS n FROM accruals WHERE note LIKE 'сверка%'")["n"]
        self.db.execute("UPDATE accruals SET date = ? WHERE note LIKE 'сверка%'", (new_date,))
        return n

    # --- reconciliation checkpoints ---

    def set_checkpoint(self, party_type: str, party_id: int, direction: str, date: str, balance: float) -> None:
        if party_type not in ("advertiser", "web") or direction not in ("traffic", "product"):
            raise ValueError("Сверка: для рекла или веба, трафик или товар.")
        self.db.execute(
            "INSERT INTO balance_checkpoints(party_type, party_id, direction, date, balance, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(party_type, party_id, direction) DO UPDATE SET "
            "date = excluded.date, balance = excluded.balance, created_at = excluded.created_at",
            (party_type, party_id, direction, date, round(balance, 2), now_str()),
        )

    def checkpoints(self, party_type: str, direction: str) -> dict[int, dict]:
        return {
            r["party_id"]: dict(r)
            for r in self.db.query(
                "SELECT * FROM balance_checkpoints WHERE party_type = ? AND direction = ?", (party_type, direction)
            )
        }

    def delete_checkpoint(self, party_type: str, party_id: int, direction: str) -> None:
        self.db.execute(
            "DELETE FROM balance_checkpoints WHERE party_type = ? AND party_id = ? AND direction = ?",
            (party_type, party_id, direction),
        )

    # --- import aliases ---

    def import_aliases(self) -> dict[str, str]:
        return {r["name"].lower(): r["kind"] for r in self.db.query("SELECT * FROM import_aliases")}

    def set_import_alias(self, name: str, kind: str) -> None:
        self.db.execute(
            "INSERT INTO import_aliases(name, kind) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET kind = excluded.kind",
            (name.strip(), kind),
        )

    # --- chat ---

    def add_chat_message(self, role: str, content: str, actions: list[dict] | None = None) -> int:
        return self.db.execute(
            "INSERT INTO chat_messages(role, content, actions_json, actions_status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                role,
                content,
                json.dumps(actions, ensure_ascii=False) if actions else None,
                "pending" if actions else None,
                now_str(),
            ),
        )

    def chat_messages(self, limit: int = 200) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM (SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,)
        )
        return [_chat_row(r) for r in rows]

    def chat_message(self, message_id: int) -> dict | None:
        row = self.db.one("SELECT * FROM chat_messages WHERE id = ?", (message_id,))
        return _chat_row(row) if row else None

    def set_chat_actions_status(self, message_id: int, status: str, result: str = "") -> None:
        self.db.execute(
            "UPDATE chat_messages SET actions_status = ?, actions_result = ? WHERE id = ?",
            (status, result, message_id),
        )

    def clear_chat(self) -> None:
        self.db.execute("DELETE FROM chat_messages")


def _chat_row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "role": r["role"],
        "content": r["content"],
        "actions": json.loads(r["actions_json"]) if r["actions_json"] else [],
        "actions_status": r["actions_status"],
        "actions_result": r["actions_result"],
        "created_at": r["created_at"],
    }


def _date_filter(sql: str, column: str, start: str | None, end: str | None) -> tuple[str, list]:
    clauses, params = [], []
    if start:
        clauses.append(f"{column} >= ?")
        params.append(start)
    if end:
        clauses.append(f"{column} <= ?")
        params.append(end)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return sql, params
