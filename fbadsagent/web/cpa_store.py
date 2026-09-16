"""Persists CPA / offer-network credentials (e.g. traff-hub.com) added on
the "CPA Networks" dashboard page.

This only stores credentials — there's no offer-fetching logic yet, since
that depends on each network's own API. Once you have API docs for a
network, add a client module (mirroring fbadsagent/web/insights_client.py)
that reads its base_url/api_key from here.
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from fbadsagent.models import CpaNetworkCredential


class CpaNetworkStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = Lock()
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write({"networks": []})

    def _read(self) -> dict:
        with self._lock:
            return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        with self._lock:
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_networks(self) -> list[CpaNetworkCredential]:
        return [CpaNetworkCredential(**n) for n in self._read().get("networks", [])]

    def add_network(self, name: str, base_url: str = "", api_key: str = "") -> None:
        name = name.strip()
        if not name:
            return
        data = self._read()
        networks = [n for n in data.get("networks", []) if n["name"] != name]
        networks.append({"name": name, "base_url": base_url.strip(), "api_key": api_key.strip()})
        data["networks"] = networks
        self._write(data)

    def remove_network(self, name: str) -> None:
        data = self._read()
        data["networks"] = [n for n in data.get("networks", []) if n["name"] != name]
        self._write(data)
