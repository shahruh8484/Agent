from datetime import date

import pytest

from finance.db import DB, Repo

TODAY = date(2026, 10, 10)


@pytest.fixture
def repo():
    return Repo(DB(":memory:"))


class FakeBackend:
    """Stands in for ClaudeBackend: returns a canned reply + actions and
    records what it was asked."""

    def __init__(self, text: str = "", actions: list[dict] | None = None):
        self.text = text
        self.actions = actions or []
        self.calls: list[tuple[str, list[dict]]] = []

    def respond(self, system, messages, read_tool=None):
        self.calls.append((system, messages))
        self.read_tool = read_tool
        return self.text, self.actions
