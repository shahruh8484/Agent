"""Persists the agent chat history shown on the "Chat" dashboard page."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from fbadsagent.models import ChatMessage


class ChatStore:
    def __init__(self, path: Path, max_messages: int = 200):
        self._path = path
        self._lock = Lock()
        self._max_messages = max_messages
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._write({"messages": []})

    def _read(self) -> dict:
        with self._lock:
            return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        with self._lock:
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_messages(self) -> list[ChatMessage]:
        return [ChatMessage(**m) for m in self._read().get("messages", [])]

    def append(self, message: ChatMessage) -> None:
        data = self._read()
        messages = data.get("messages", [])
        messages.append(message.model_dump())
        messages = messages[-self._max_messages :]
        data["messages"] = messages
        self._write(data)

    def clear(self) -> None:
        self._write({"messages": []})
