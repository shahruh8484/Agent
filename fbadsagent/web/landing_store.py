"""Persists landing page configs published on the "Landing Pages" page:
content plus which CPA network / campaign hash their lead form submits to.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from threading import Lock

from fbadsagent.models import LandingPageConfig


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "landing"


class LandingPageStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = Lock()
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write({"pages": []})

    def _read(self) -> dict:
        with self._lock:
            return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        with self._lock:
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_pages(self) -> list[LandingPageConfig]:
        return [LandingPageConfig(**p) for p in self._read().get("pages", [])]

    def get_page(self, slug: str) -> LandingPageConfig | None:
        for page in self.list_pages():
            if page.slug == slug:
                return page
        return None

    def add_page(self, config: LandingPageConfig) -> None:
        data = self._read()
        pages = [p for p in data.get("pages", []) if p["slug"] != config.slug]
        pages.append(config.model_dump())
        data["pages"] = pages
        self._write(data)

    def remove_page(self, slug: str) -> None:
        data = self._read()
        data["pages"] = [p for p in data.get("pages", []) if p["slug"] != slug]
        self._write(data)
