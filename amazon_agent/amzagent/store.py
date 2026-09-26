"""SQLite persistence: niches (one site each), their products, push
campaigns, visit/click events and agent run logs.

One file under DATA_DIR, so the whole state survives container rebuilds
when DATA_DIR is a mounted volume (see docker-compose.yml).
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from amzagent.models import Niche, Product, ProductCopy, SiteCopy

SCHEMA = """
CREATE TABLE IF NOT EXISTS niches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    keywords TEXT NOT NULL,
    search_index TEXT NOT NULL DEFAULT 'All',
    language TEXT NOT NULL DEFAULT 'English',
    max_price REAL,
    enabled INTEGER NOT NULL DEFAULT 1,
    site_copy TEXT,
    asins TEXT,
    asin_meta TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    niche_id INTEGER NOT NULL,
    asin TEXT NOT NULL,
    data TEXT NOT NULL,
    copy TEXT,
    score REAL NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (niche_id, asin)
);
CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    niche_id INTEGER NOT NULL,
    asin TEXT NOT NULL,
    external_id TEXT,
    status TEXT NOT NULL,
    daily_budget REAL NOT NULL,
    spend REAL NOT NULL DEFAULT 0,
    payload TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS campaign_zones (
    campaign_id INTEGER NOT NULL,
    zone TEXT NOT NULL,
    impressions INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    spent REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (campaign_id, zone)
);
CREATE TABLE IF NOT EXISTS zone_blacklist (
    campaign_id INTEGER NOT NULL,
    zone TEXT NOT NULL,
    PRIMARY KEY (campaign_id, zone)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    niche_id INTEGER NOT NULL,
    asin TEXT,
    campaign_id INTEGER,
    zone TEXT
);
CREATE INDEX IF NOT EXISTS events_campaign ON events (campaign_id, type);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    niche_id INTEGER,
    ok INTEGER NOT NULL,
    log TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Campaign statuses
DRY_RUN = "dry_run"  # payload built and logged, never sent (PUSH_LIVE=false)
ACTIVE = "active"
STOPPED = "stopped"
ERROR = "error"
RUNNING_STATUSES = (ACTIVE, DRY_RUN)

# runs.ok values: 0 = finished with errors, 1 = ok, 2 = still running
RUN_IN_PROGRESS = 2


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:50] or "site"


class Store:
    def __init__(self, data_dir: str):
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            str(Path(data_dir) / "amzagent.db"), check_same_thread=False
        )
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            # Databases created before curated (ASIN-list) sites existed.
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(niches)")}
            for col in ("asins", "asin_meta"):
                if col not in cols:
                    self._db.execute(f"ALTER TABLE niches ADD COLUMN {col} TEXT")
            # Ad network stats on campaigns (added later).
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(campaigns)")}
            for col, decl in (("impressions", "INTEGER NOT NULL DEFAULT 0"),
                              ("ad_clicks", "INTEGER NOT NULL DEFAULT 0"),
                              ("stats_at", "TEXT")):
                if col not in cols:
                    self._db.execute(f"ALTER TABLE campaigns ADD COLUMN {col} {decl}")
            self._db.commit()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()
            return cur

    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(sql, params).fetchone()

    # --- niches -----------------------------------------------------------

    def add_niche(
        self,
        keywords: str,
        search_index: str = "All",
        language: str = "English",
        max_price: float | None = None,
        asins: dict[str, float | None] | None = None,
        asin_meta: dict[str, dict] | None = None,
    ) -> Niche:
        base = slugify(keywords)
        slug, n = base, 2
        while self._one("SELECT 1 FROM niches WHERE slug = ?", (slug,)):
            slug, n = f"{base}-{n}", n + 1
        cur = self._exec(
            "INSERT INTO niches (slug, keywords, search_index, language, max_price, asins,"
            " asin_meta, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (slug, keywords, search_index or "All", language or "English", max_price,
             json.dumps(asins) if asins else None,
             json.dumps(asin_meta) if asin_meta else None, now_iso()),
        )
        return self.get_niche(cur.lastrowid)

    @staticmethod
    def _niche(row: sqlite3.Row) -> Niche:
        return Niche(
            id=row["id"],
            slug=row["slug"],
            keywords=row["keywords"],
            search_index=row["search_index"],
            language=row["language"],
            max_price=row["max_price"],
            enabled=bool(row["enabled"]),
            asins=json.loads(row["asins"]) if row["asins"] else {},
            asin_meta=json.loads(row["asin_meta"]) if row["asin_meta"] else {},
        )

    def merge_niche_asins(
        self,
        niche_id: int,
        asins: dict[str, float | None],
        asin_meta: dict[str, dict] | None = None,
    ) -> int:
        """Add ASINs to a curated site (newer EPC and card details win).
        Returns the new total."""
        niche = self.get_niche(niche_id)
        merged = dict(niche.asins)
        for asin, epc in asins.items():
            if epc is not None or asin not in merged:
                merged[asin] = epc
        meta = dict(niche.asin_meta)
        for asin, m in (asin_meta or {}).items():
            if m.get("title") or asin not in meta:
                meta[asin] = m
        self._exec("UPDATE niches SET asins = ?, asin_meta = ? WHERE id = ?",
                   (json.dumps(merged), json.dumps(meta), niche_id))
        return len(merged)

    def clear_product_copy(self, niche_id: int, asin: str) -> None:
        self._exec("UPDATE products SET copy = NULL WHERE niche_id = ? AND asin = ?",
                   (niche_id, asin))

    def get_niche(self, niche_id: int) -> Niche | None:
        row = self._one("SELECT * FROM niches WHERE id = ?", (niche_id,))
        return self._niche(row) if row else None

    def get_niche_by_slug(self, slug: str) -> Niche | None:
        row = self._one("SELECT * FROM niches WHERE slug = ?", (slug,))
        return self._niche(row) if row else None

    def list_niches(self) -> list[Niche]:
        return [self._niche(r) for r in self._all("SELECT * FROM niches ORDER BY id")]

    def set_niche_enabled(self, niche_id: int, enabled: bool) -> None:
        self._exec("UPDATE niches SET enabled = ? WHERE id = ?", (int(enabled), niche_id))

    def delete_niche(self, niche_id: int) -> None:
        for table in ("products", "events"):
            self._exec(f"DELETE FROM {table} WHERE niche_id = ?", (niche_id,))
        self._exec("DELETE FROM niches WHERE id = ?", (niche_id,))

    def set_site_copy(self, niche_id: int, copy: SiteCopy) -> None:
        self._exec(
            "UPDATE niches SET site_copy = ? WHERE id = ?", (copy.model_dump_json(), niche_id)
        )

    def get_site_copy(self, niche_id: int) -> SiteCopy | None:
        row = self._one("SELECT site_copy FROM niches WHERE id = ?", (niche_id,))
        return SiteCopy.model_validate_json(row["site_copy"]) if row and row["site_copy"] else None

    # --- products ---------------------------------------------------------

    def replace_products(
        self, niche_id: int, ranked: list[tuple[Product, float]]
    ) -> None:
        """Make `ranked` the site's product list (best first). Products that
        dropped out are kept but deactivated, so their copy is reused if
        they come back and old push campaigns can still be resolved."""
        self._exec("UPDATE products SET active = 0 WHERE niche_id = ?", (niche_id,))
        for position, (product, score) in enumerate(ranked):
            self._exec(
                "INSERT INTO products (niche_id, asin, data, score, position, active, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 1, ?)"
                " ON CONFLICT (niche_id, asin) DO UPDATE SET data = excluded.data,"
                " score = excluded.score, position = excluded.position, active = 1,"
                " updated_at = excluded.updated_at",
                (niche_id, product.asin, product.model_dump_json(), score, position, now_iso()),
            )

    def update_product_data(self, niche_id: int, product: Product) -> None:
        self._exec(
            "UPDATE products SET data = ?, updated_at = ? WHERE niche_id = ? AND asin = ?",
            (product.model_dump_json(), now_iso(), niche_id, product.asin),
        )

    def set_product_copy(self, niche_id: int, asin: str, copy: ProductCopy) -> None:
        self._exec(
            "UPDATE products SET copy = ? WHERE niche_id = ? AND asin = ?",
            (copy.model_dump_json(), niche_id, asin),
        )

    def list_products(
        self, niche_id: int, active_only: bool = True
    ) -> list[tuple[Product, ProductCopy | None]]:
        sql = "SELECT data, copy FROM products WHERE niche_id = ?"
        if active_only:
            sql += " AND active = 1"
        rows = self._all(sql + " ORDER BY position", (niche_id,))
        return [
            (
                Product.model_validate_json(r["data"]),
                ProductCopy.model_validate_json(r["copy"]) if r["copy"] else None,
            )
            for r in rows
        ]

    def get_product(self, niche_id: int, asin: str) -> tuple[Product, ProductCopy | None] | None:
        r = self._one(
            "SELECT data, copy FROM products WHERE niche_id = ? AND asin = ?", (niche_id, asin)
        )
        if not r:
            return None
        return (
            Product.model_validate_json(r["data"]),
            ProductCopy.model_validate_json(r["copy"]) if r["copy"] else None,
        )

    # --- campaigns --------------------------------------------------------

    def add_campaign(
        self, niche_id: int, asin: str, status: str, daily_budget: float, note: str = ""
    ) -> int:
        ts = now_iso()
        cur = self._exec(
            "INSERT INTO campaigns (niche_id, asin, status, daily_budget, note, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (niche_id, asin, status, daily_budget, note, ts, ts),
        )
        return cur.lastrowid

    def update_campaign(self, campaign_id: int, **fields) -> None:
        if "payload" in fields and not isinstance(fields["payload"], str):
            fields["payload"] = json.dumps(fields["payload"], ensure_ascii=False)
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(
            f"UPDATE campaigns SET {cols} WHERE id = ?", (*fields.values(), campaign_id)
        )

    def list_campaigns(
        self, niche_id: int | None = None, statuses: tuple[str, ...] | None = None
    ) -> list[dict]:
        sql, params = "SELECT * FROM campaigns WHERE 1 = 1", []
        if niche_id is not None:
            sql += " AND niche_id = ?"
            params.append(niche_id)
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            params.extend(statuses)
        return [dict(r) for r in self._all(sql + " ORDER BY id DESC", tuple(params))]

    def get_campaign(self, campaign_id: int) -> dict | None:
        r = self._one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
        return dict(r) if r else None

    def running_daily_budget(self) -> float:
        """Sum of daily budgets of campaigns that can spend money now."""
        r = self._one("SELECT COALESCE(SUM(daily_budget), 0) AS s FROM campaigns WHERE status = ?",
                      (ACTIVE,))
        return float(r["s"])

    def upsert_zone_stats(self, campaign_id: int, zone: str, impressions: int, clicks: int,
                          spent: float) -> None:
        self._exec(
            "INSERT INTO campaign_zones (campaign_id, zone, impressions, clicks, spent,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (campaign_id, zone) DO UPDATE"
            " SET impressions = excluded.impressions, clicks = excluded.clicks,"
            " spent = excluded.spent, updated_at = excluded.updated_at",
            (campaign_id, zone, impressions, clicks, spent, now_iso()),
        )

    def zone_stats(self, campaign_id: int) -> list[dict]:
        rows = self._all("SELECT * FROM campaign_zones WHERE campaign_id = ?"
                         " ORDER BY spent DESC, impressions DESC", (campaign_id,))
        return [dict(r) for r in rows]

    def unblacklist_zone(self, campaign_id: int, zone: str) -> None:
        self._exec("DELETE FROM zone_blacklist WHERE campaign_id = ? AND zone = ?",
                   (campaign_id, zone))

    def blacklist_zone(self, campaign_id: int, zone: str) -> bool:
        cur = self._exec(
            "INSERT OR IGNORE INTO zone_blacklist (campaign_id, zone) VALUES (?, ?)",
            (campaign_id, zone),
        )
        return cur.rowcount > 0

    def blacklisted_zones(self, campaign_id: int) -> set[str]:
        rows = self._all("SELECT zone FROM zone_blacklist WHERE campaign_id = ?", (campaign_id,))
        return {r["zone"] for r in rows}

    # --- events -----------------------------------------------------------

    def log_event(
        self,
        type_: str,
        niche_id: int,
        asin: str | None = None,
        campaign_id: int | None = None,
        zone: str | None = None,
    ) -> None:
        self._exec(
            "INSERT INTO events (ts, type, niche_id, asin, campaign_id, zone)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (now_iso(), type_, niche_id, asin, campaign_id, zone),
        )

    @staticmethod
    def _window(since: str | None, until: str | None) -> tuple[str, tuple]:
        """SQL + params limiting events to [since, until) (UTC ISO strings)."""
        sql, params = "", ()
        if since:
            sql, params = sql + " AND ts >= ?", params + (since,)
        if until:
            sql, params = sql + " AND ts < ?", params + (until,)
        return sql, params

    def count_events(self, campaign_id: int, type_: str, since: str | None = None,
                     until: str | None = None) -> int:
        window, extra = self._window(since, until)
        r = self._one(
            "SELECT COUNT(*) AS n FROM events WHERE campaign_id = ? AND type = ?" + window,
            (campaign_id, type_, *extra),
        )
        return int(r["n"])

    def events_by_zone(self, campaign_id: int, type_: str, since: str | None = None,
                       until: str | None = None) -> dict[str, int]:
        window, extra = self._window(since, until)
        rows = self._all(
            "SELECT zone, COUNT(*) AS n FROM events WHERE campaign_id = ? AND type = ?"
            " AND zone IS NOT NULL" + window + " GROUP BY zone",
            (campaign_id, type_, *extra),
        )
        return {r["zone"]: int(r["n"]) for r in rows}

    def niche_stats(self, niche_id: int, days: int = 7) -> dict[str, int]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self._all(
            "SELECT type, COUNT(*) AS n FROM events WHERE niche_id = ? AND ts >= ? GROUP BY type",
            (niche_id, since),
        )
        stats = {"visit": 0, "click": 0}
        stats.update({r["type"]: int(r["n"]) for r in rows})
        return stats

    # --- runs + flags -----------------------------------------------------

    def start_run(self, niche_id: int | None) -> int:
        """Open a run row that the dashboard shows live while it fills."""
        cur = self._exec(
            "INSERT INTO runs (ts, niche_id, ok, log) VALUES (?, ?, ?, '')",
            (now_iso(), niche_id, RUN_IN_PROGRESS),
        )
        return cur.lastrowid

    def append_run_log(self, run_id: int, line: str) -> None:
        self._exec(
            "UPDATE runs SET log = CASE WHEN log = '' THEN ? ELSE log || char(10) || ? END"
            " WHERE id = ?",
            (line, line, run_id),
        )

    def finish_run(self, run_id: int, ok: bool) -> None:
        self._exec("UPDATE runs SET ok = ? WHERE id = ?", (int(ok), run_id))

    def close_interrupted_runs(self) -> None:
        """Runs still "in progress" at startup died with the old process."""
        self._exec("UPDATE runs SET ok = 0, log = log || char(10) || ? WHERE ok = ?",
                   ("(прервано перезапуском сервера)", RUN_IN_PROGRESS))

    def run_in_progress(self) -> bool:
        return self._one("SELECT 1 FROM runs WHERE ok = ?", (RUN_IN_PROGRESS,)) is not None

    def list_runs(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self._all("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]

    # --- contact form -----------------------------------------------------

    def add_message(self, name: str, email: str, body: str) -> None:
        self._exec("INSERT INTO messages (ts, name, email, body) VALUES (?, ?, ?, ?)",
                   (now_iso(), name, email, body))

    def list_messages(self, limit: int = 20) -> list[dict]:
        rows = self._all("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def count_recent_messages(self, hours: int = 1) -> int:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
        r = self._one("SELECT COUNT(*) AS n FROM messages WHERE ts >= ?", (since,))
        return int(r["n"])

    def get_flag(self, key: str, default: str = "") -> str:
        r = self._one("SELECT value FROM kv WHERE key = ?", (key,))
        return r["value"] if r else default

    def set_flag(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO kv (key, value) VALUES (?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
