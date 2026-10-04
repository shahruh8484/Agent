"""JSON-backed persistence for cloner projects, same pattern as the main
dashboard's stores (fbadsagent/web/*_store.py)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from threading import Lock

from fbadsagent.cloner.models import ClonerProject


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "landing"


class ClonerProjectStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = Lock()
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write({"projects": []})

    def _read(self) -> dict:
        with self._lock:
            return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        with self._lock:
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_projects(self) -> list[ClonerProject]:
        projects = [ClonerProject(**p) for p in self._read().get("projects", [])]
        return list(reversed(projects))  # newest first

    def get_project(self, project_id: str) -> ClonerProject | None:
        for project in self.list_projects():
            if project.id == project_id:
                return project
        return None

    def get_by_slug(self, slug: str) -> ClonerProject | None:
        for project in self.list_projects():
            if project.slug == slug:
                return project
        return None

    def save(self, project: ClonerProject) -> None:
        data = self._read()
        projects = [p for p in data.get("projects", []) if p["id"] != project.id]
        projects.append(project.model_dump())
        data["projects"] = projects
        self._write(data)

    def remove(self, project_id: str) -> None:
        data = self._read()
        data["projects"] = [p for p in data.get("projects", []) if p["id"] != project_id]
        self._write(data)
