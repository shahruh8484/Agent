"""Persists the sites the blog agent created and every article it has
published to them.
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from fbadsagent.models import BlogArticle, BlogSite


class BlogStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = Lock()
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write({"sites": [], "articles": []})

    def _read(self) -> dict:
        with self._lock:
            return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        with self._lock:
            self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def list_sites(self) -> list[BlogSite]:
        return [BlogSite(**s) for s in self._read().get("sites", [])]

    def get_site(self, site_id: str) -> BlogSite | None:
        return next((s for s in self.list_sites() if s.id == site_id), None)

    def get_site_by_slug(self, slug: str) -> BlogSite | None:
        return next((s for s in self.list_sites() if s.slug == slug), None)

    def add_site(self, site: BlogSite) -> None:
        data = self._read()
        data["sites"] = [s for s in data.get("sites", []) if s["id"] != site.id] + [site.model_dump()]
        self._write(data)

    def remove_site(self, site_id: str) -> None:
        data = self._read()
        data["sites"] = [s for s in data.get("sites", []) if s["id"] != site_id]
        data["articles"] = [a for a in data.get("articles", []) if a["site_id"] != site_id]
        self._write(data)

    def list_articles(self, site_id: str) -> list[BlogArticle]:
        """Newest first."""
        articles = [
            BlogArticle(**a) for a in self._read().get("articles", []) if a["site_id"] == site_id
        ]
        return list(reversed(articles))

    def get_article(self, site_id: str, slug: str) -> BlogArticle | None:
        return next((a for a in self.list_articles(site_id) if a.slug == slug), None)

    def add_article(self, article: BlogArticle) -> None:
        data = self._read()
        data.setdefault("articles", []).append(article.model_dump())
        self._write(data)

    def remove_article(self, site_id: str, slug: str) -> None:
        data = self._read()
        data["articles"] = [
            a
            for a in data.get("articles", [])
            if not (a["site_id"] == site_id and a["slug"] == slug)
        ]
        self._write(data)
