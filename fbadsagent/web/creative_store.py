"""Persists generated ad creative sets (copy + images) published on the
"Creatives" dashboard page, so they can be browsed and reused later.
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from fbadsagent.models import SavedCreativeSet


class CreativeStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = Lock()
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write({"sets": []})

    def _read(self) -> dict:
        with self._lock:
            return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        with self._lock:
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_sets(self) -> list[SavedCreativeSet]:
        sets = [SavedCreativeSet(**s) for s in self._read().get("sets", [])]
        return list(reversed(sets))  # newest first

    def add_set(self, creative_set: SavedCreativeSet) -> None:
        data = self._read()
        sets = data.get("sets", [])
        sets.append(creative_set.model_dump())
        data["sets"] = sets
        self._write(data)

    def remove_set(self, set_id: str) -> None:
        data = self._read()
        data["sets"] = [s for s in data.get("sets", []) if s["id"] != set_id]
        self._write(data)
