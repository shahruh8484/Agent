"""Persists the Facebook access token and tracked ad accounts that the
"FB Accounts" dashboard page manages, so they survive container restarts
without needing to edit .env or SSH into the server.

Stored as a small JSON file rather than a database — this is a handful of
values edited rarely, not high-volume data.
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from fbadsagent.models import TrackedAccount


class AccountStore:
    def __init__(
        self,
        path: Path,
        seed_access_token: str = "",
        seed_account_ids: list[str] | None = None,
    ):
        self._path = path
        self._lock = Lock()
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write(
                {
                    "access_token": seed_access_token,
                    "accounts": [
                        {"id": account_id, "name": ""}
                        for account_id in (seed_account_ids or [])
                    ],
                }
            )

    def _read(self) -> dict:
        with self._lock:
            return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        with self._lock:
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get_access_token(self) -> str:
        return self._read().get("access_token", "")

    def set_access_token(self, token: str) -> None:
        data = self._read()
        data["access_token"] = token.strip()
        self._write(data)

    def list_accounts(self) -> list[TrackedAccount]:
        return [TrackedAccount(**a) for a in self._read().get("accounts", [])]

    def add_account(self, account_id: str, name: str = "") -> None:
        account_id = account_id.strip()
        if not account_id:
            return
        if not account_id.startswith("act_"):
            account_id = f"act_{account_id}"

        data = self._read()
        accounts = data.get("accounts", [])
        if not any(a["id"] == account_id for a in accounts):
            accounts.append({"id": account_id, "name": name.strip()})
        data["accounts"] = accounts
        self._write(data)

    def remove_account(self, account_id: str) -> None:
        data = self._read()
        data["accounts"] = [a for a in data.get("accounts", []) if a["id"] != account_id]
        self._write(data)
