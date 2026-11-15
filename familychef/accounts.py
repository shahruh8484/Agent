"""User accounts: SQLite storage, sessions, email codes, Google ID tokens.

Configuration (environment):
  FC_DATA_DIR          where familychef.db and secret.key live (default ./var)
  FC_SECRET            secret for hashing codes; generated and stored if not set
  FC_GOOGLE_CLIENT_ID  OAuth "Web application" client id — enables Google sign-in
  FC_EMAIL_PROVIDER    resend | smtp | dev — enables email sign-in (see mailer.py)
  FC_REQUIRE_LOGIN     1 = the planner works only for signed-in users
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SESSION_DAYS = 180
CODE_TTL = 10 * 60
CODE_COOLDOWN = 60
CODE_MAX_PER_HOUR = 5
CODE_MAX_ATTEMPTS = 5
IP_MAX_PER_HOUR = 20
PLANS_KEPT = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE,
    google_sub TEXT UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    picture TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    last_login_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS email_codes (
    email TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    sent_at INTEGER NOT NULL,
    hour_start INTEGER NOT NULL,
    hour_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    settings TEXT NOT NULL DEFAULT '{}',
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    title TEXT NOT NULL,
    cost REAL NOT NULL,
    currency TEXT NOT NULL,
    request TEXT NOT NULL,
    result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS plans_user ON plans(user_id, created_at DESC);
"""

# Columns added after the first release: (table, column, definition).
MIGRATIONS = [
    ("users", "plus_until", "INTEGER NOT NULL DEFAULT 0"),
    ("plans", "kind", "TEXT NOT NULL DEFAULT 'plus'"),
]


class AuthError(ValueError):
    """User-facing auth problem; .code is a stable key for translations."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def _now() -> int:
    return int(time.time())


class Accounts:
    def __init__(self, data_dir: str | os.PathLike | None = None):
        self.dir = Path(data_dir or os.environ.get("FC_DATA_DIR", "var"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "familychef.db"
        self.secret = self._load_secret()
        self._lock = threading.Lock()
        self._ip_hits: dict[str, list[int]] = {}
        with self._db() as db:
            db.executescript(SCHEMA)
            for table, column, definition in MIGRATIONS:
                cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    # ------------------------------------------------------------ plumbing
    def _load_secret(self) -> bytes:
        env = os.environ.get("FC_SECRET")
        if env:
            return env.encode()
        f = self.dir / "secret.key"
        if not f.exists():
            f.write_text(secrets.token_hex(32))
            f.chmod(0o600)
        return f.read_text().strip().encode()

    @contextmanager
    def _db(self):
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _h(self, *parts: str) -> str:
        return hmac.new(self.secret, "\x1f".join(parts).encode(), hashlib.sha256).hexdigest()

    # ------------------------------------------------------------ users
    @staticmethod
    def _user_dict(row: sqlite3.Row) -> dict:
        plus_until = row["plus_until"] if "plus_until" in row.keys() else 0
        return {"id": row["id"], "email": row["email"], "name": row["name"],
                "picture": row["picture"], "google": bool(row["google_sub"]),
                "created_at": row["created_at"], "plus_until": plus_until,
                "plus": plus_until > _now()}

    def get_user(self, user_id: int) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._user_dict(row) if row else None

    def _upsert(self, db, *, email: str | None, google_sub: str | None, name: str, picture: str = "") -> int:
        now = _now()
        row = None
        if google_sub:
            row = db.execute("SELECT * FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
        if row is None and email:
            row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            cur = db.execute(
                "INSERT INTO users (email, google_sub, name, picture, created_at, last_login_at)"
                " VALUES (?, ?, ?, ?, ?, ?)", (email, google_sub, name, picture, now, now))
            return cur.lastrowid
        db.execute(
            "UPDATE users SET last_login_at = ?, google_sub = COALESCE(google_sub, ?),"
            " email = COALESCE(email, ?), name = CASE WHEN name = '' THEN ? ELSE name END,"
            " picture = CASE WHEN ? != '' THEN ? ELSE picture END WHERE id = ?",
            (now, google_sub, email, name, picture, picture, row["id"]))
        return row["id"]

    def update_name(self, user_id: int, name: str) -> None:
        name = name.strip()[:60]
        if not name:
            raise AuthError("bad_name")
        with self._db() as db:
            db.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))

    def delete_user(self, user_id: int) -> None:
        with self._db() as db:
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    # ------------------------------------------------------------ sessions
    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                       (self._h("session", token), user_id, now, now + SESSION_DAYS * 86400))
        return token

    def session_user(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self._db() as db:
            row = db.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id"
                " WHERE s.token_hash = ? AND s.expires_at > ?",
                (self._h("session", token), _now())).fetchone()
        return self._user_dict(row) if row else None

    def end_session(self, token: str | None) -> None:
        if token:
            with self._db() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (self._h("session", token),))

    # ------------------------------------------------------------ email codes
    def _check_ip(self, ip: str) -> None:
        now = _now()
        hits = [t for t in self._ip_hits.get(ip, []) if t > now - 3600]
        if len(hits) >= IP_MAX_PER_HOUR:
            raise AuthError("too_many", 429)
        hits.append(now)
        self._ip_hits[ip] = hits

    def start_email_code(self, email: str, ip: str = "") -> str:
        """Create a code for `email`; returns it so the caller can send it."""
        email = normalize_email(email)
        self._check_ip(ip or "?")
        now = _now()
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self._db() as db:
            row = db.execute("SELECT * FROM email_codes WHERE email = ?", (email,)).fetchone()
            hour_start, hour_count = now, 0
            if row:
                if now - row["sent_at"] < CODE_COOLDOWN:
                    raise AuthError("wait", 429)
                if now - row["hour_start"] < 3600:
                    hour_start, hour_count = row["hour_start"], row["hour_count"]
                if hour_count >= CODE_MAX_PER_HOUR:
                    raise AuthError("too_many", 429)
            db.execute(
                "INSERT OR REPLACE INTO email_codes VALUES (?, ?, ?, 0, ?, ?, ?)",
                (email, self._h("code", email, code), now + CODE_TTL, now, hour_start, hour_count + 1))
        return code

    def verify_email_code(self, email: str, code: str, name: str = "") -> int:
        email = normalize_email(email)
        code = "".join(ch for ch in str(code) if ch.isdigit())
        error = None
        with self._db() as db:
            row = db.execute("SELECT * FROM email_codes WHERE email = ?", (email,)).fetchone()
            if not row or row["expires_at"] < _now():
                error = AuthError("code_expired")
            elif row["attempts"] >= CODE_MAX_ATTEMPTS:
                error = AuthError("too_many", 429)
            elif not hmac.compare_digest(row["code_hash"], self._h("code", email, code)):
                # Count the failed attempt; it must be committed, so don't raise inside the transaction.
                db.execute("UPDATE email_codes SET attempts = attempts + 1 WHERE email = ?", (email,))
                error = AuthError("code_wrong")
            else:
                db.execute("DELETE FROM email_codes WHERE email = ?", (email,))
                default_name = name.strip()[:60] or email.split("@")[0]
                user_id = self._upsert(db, email=email, google_sub=None, name=default_name)
        if error:
            raise error
        return user_id

    # ------------------------------------------------------------ google
    def login_google(self, claims: dict) -> int:
        email = normalize_email(claims["email"]) if claims.get("email") and claims.get("email_verified") else None
        with self._db() as db:
            return self._upsert(db, email=email, google_sub=str(claims["sub"]),
                                name=(claims.get("name") or "")[:60] or (email or "").split("@")[0],
                                picture=claims.get("picture") or "")

    # ------------------------------------------------------------ profile & plans
    def get_settings(self, user_id: int) -> dict:
        with self._db() as db:
            row = db.execute("SELECT settings FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        return json.loads(row["settings"]) if row else {}

    def save_settings(self, user_id: int, settings: dict) -> None:
        blob = json.dumps(settings, ensure_ascii=False)
        if len(blob) > 100_000:
            raise AuthError("too_big")
        with self._db() as db:
            db.execute("INSERT INTO profiles VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE"
                       " SET settings = excluded.settings, updated_at = excluded.updated_at",
                       (user_id, blob, _now()))

    # ------------------------------------------------------------ subscription
    def set_plus(self, user_id: int, until: int) -> None:
        with self._db() as db:
            db.execute("UPDATE users SET plus_until = ? WHERE id = ?", (int(until), user_id))

    def find_user_by_email(self, email: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (normalize_email(email),)).fetchone()
        return self._user_dict(row) if row else None

    def last_free_plan_at(self, user_id: int) -> int | None:
        with self._db() as db:
            row = db.execute("SELECT MAX(created_at) AS t FROM plans WHERE user_id = ? AND kind = 'free'",
                             (user_id,)).fetchone()
        return row["t"] if row and row["t"] else None

    def save_plan(self, user_id: int, request: dict, result: dict, kind: str = "plus") -> int:
        title = f'{result["region"]["name"]}, {result["people"]["adults"]}+{result["people"]["children"]}'
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO plans (user_id, created_at, title, cost, currency, request, result, kind)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, _now(), title, result["totals"]["cost"], result["region"]["currency"],
                 json.dumps(request, ensure_ascii=False), json.dumps(result, ensure_ascii=False), kind))
            db.execute("DELETE FROM plans WHERE user_id = ? AND id NOT IN"
                       " (SELECT id FROM plans WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?)",
                       (user_id, user_id, PLANS_KEPT))
            return cur.lastrowid

    def list_plans(self, user_id: int, limit: int = PLANS_KEPT) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT id, created_at, title, cost, currency, kind FROM plans"
                              " WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                              (user_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        now = _now()
        with self._db() as db:
            one = lambda q, *a: db.execute(q, a).fetchone()[0]
            return {
                "users": one("SELECT COUNT(*) FROM users"),
                "plus_users": one("SELECT COUNT(*) FROM users WHERE plus_until > ?", now),
                "active_7d": one("SELECT COUNT(*) FROM users WHERE last_login_at > ?", now - 7 * 86400),
                "plans_7d": one("SELECT COUNT(*) FROM plans WHERE created_at > ?", now - 7 * 86400),
            }

    def get_plan(self, user_id: int, plan_id: int) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT request, result FROM plans WHERE id = ? AND user_id = ?",
                             (plan_id, user_id)).fetchone()
        if not row:
            return None
        return {"request": json.loads(row["request"]), "result": json.loads(row["result"])}

    def delete_plan(self, user_id: int, plan_id: int) -> None:
        with self._db() as db:
            db.execute("DELETE FROM plans WHERE id = ? AND user_id = ?", (plan_id, user_id))


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    local, _, domain = email.partition("@")
    if not local or "." not in domain or len(email) > 200 or " " in email:
        raise AuthError("bad_email")
    return email


# ---------------------------------------------------------------- Google ID tokens
_jwks_client = None


def verify_google_token(credential: str, client_id: str) -> dict:
    """Validate a Google ID token (signature, audience, issuer, expiry)."""
    global _jwks_client
    import jwt  # PyJWT

    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs", cache_keys=True)
    try:
        key = _jwks_client.get_signing_key_from_jwt(credential).key
        claims = jwt.decode(credential, key, algorithms=["RS256"], audience=client_id,
                            issuer=["accounts.google.com", "https://accounts.google.com"],
                            leeway=60)
    except Exception as exc:  # any token problem is the same to the user
        raise AuthError("google_invalid", 401) from exc
    if not claims.get("sub"):
        raise AuthError("google_invalid", 401)
    return claims
