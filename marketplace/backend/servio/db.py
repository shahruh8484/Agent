"""SQLite storage: schema, connection helper and catalog seeding."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from servio.catalog import CATALOG

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    phone TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'client' CHECK (role IN ('client', 'specialist')),
    city TEXT NOT NULL DEFAULT '',
    lang TEXT NOT NULL DEFAULT 'uz' CHECK (lang IN ('ru', 'uz')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_codes (
    phone TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    parent_id INTEGER REFERENCES categories(id),
    name_ru TEXT NOT NULL,
    name_uz TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS specialist_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    bio TEXT NOT NULL DEFAULT '',
    experience_years INTEGER NOT NULL DEFAULT 0,
    price_from INTEGER,
    remote INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS specialist_categories (
    user_id INTEGER NOT NULL REFERENCES users(id),
    category_id INTEGER NOT NULL REFERENCES categories(id),
    PRIMARY KEY (user_id, category_id)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES users(id),
    category_id INTEGER NOT NULL REFERENCES categories(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    budget INTEGER,
    remote INTEGER NOT NULL DEFAULT 0,
    when_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'in_progress', 'completed', 'closed')),
    specialist_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_feed ON orders(status, category_id, city);
CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    specialist_id INTEGER NOT NULL REFERENCES users(id),
    message TEXT NOT NULL,
    price INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (order_id, specialist_id)
);
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    client_id INTEGER NOT NULL REFERENCES users(id),
    specialist_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    UNIQUE (order_id, specialist_id)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL REFERENCES chats(id),
    sender_id INTEGER NOT NULL REFERENCES users(id),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
    client_id INTEGER NOT NULL REFERENCES users(id),
    specialist_id INTEGER NOT NULL REFERENCES users(id),
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    plan_id TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    plan_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    amount INTEGER NOT NULL,  -- whole so'm
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'succeeded', 'canceled')),
    provider_id TEXT UNIQUE,
    created_at TEXT NOT NULL
);
-- Payme Merchant API transactions (https://developer.help.paycom.uz/)
CREATE TABLE IF NOT EXISTS payme_transactions (
    id INTEGER PRIMARY KEY,
    payme_id TEXT NOT NULL UNIQUE,
    payment_id INTEGER NOT NULL REFERENCES payments(id),
    amount INTEGER NOT NULL,  -- tiyin
    state INTEGER NOT NULL,
    payme_time INTEGER NOT NULL,
    create_time INTEGER NOT NULL,
    perform_time INTEGER NOT NULL DEFAULT 0,
    cancel_time INTEGER NOT NULL DEFAULT 0,
    reason INTEGER
);
CREATE TABLE IF NOT EXISTS push_tokens (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id)
);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            for section, services in CATALOG:
                parent_id = conn.execute(
                    "INSERT INTO categories (name_ru, name_uz) VALUES (?, ?)", section
                ).lastrowid
                conn.executemany(
                    "INSERT INTO categories (parent_id, name_ru, name_uz) VALUES (?, ?, ?)",
                    [(parent_id, ru, uz) for ru, uz in services],
                )
        conn.commit()
    finally:
        conn.close()
