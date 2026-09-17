"""Persists the products/offers the autonomous agent manages, plus a
rolling log of what each run produced (or failed on).
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from fbadsagent.models import AgentProduct, AgentRunResult


class ProductStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = Lock()
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write({"products": []})

    def _read(self) -> dict:
        with self._lock:
            return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        with self._lock:
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_products(self) -> list[AgentProduct]:
        return [AgentProduct(**p) for p in self._read().get("products", [])]

    def get_product(self, product_id: str) -> AgentProduct | None:
        for product in self.list_products():
            if product.id == product_id:
                return product
        return None

    def add_product(self, product: AgentProduct) -> None:
        data = self._read()
        products = [p for p in data.get("products", []) if p["id"] != product.id]
        products.append(product.model_dump())
        data["products"] = products
        self._write(data)

    def remove_product(self, product_id: str) -> None:
        data = self._read()
        data["products"] = [p for p in data.get("products", []) if p["id"] != product_id]
        self._write(data)


class AgentRunLogStore:
    def __init__(self, path: Path, max_entries: int = 100):
        self._path = path
        self._lock = Lock()
        self._max_entries = max_entries
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write({"runs": []})

    def _read(self) -> dict:
        with self._lock:
            return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        with self._lock:
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_runs(self) -> list[AgentRunResult]:
        runs = [AgentRunResult(**r) for r in self._read().get("runs", [])]
        return list(reversed(runs))  # newest first

    def append(self, result: AgentRunResult) -> None:
        data = self._read()
        runs = data.get("runs", [])
        runs.append(result.model_dump())
        data["runs"] = runs[-self._max_entries :]
        self._write(data)
