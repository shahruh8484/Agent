"""Логика бота: промпт, вызов LLM, разбор JSON-ответа.

Этот модуль общий для консольного чата (chat.py) и автопрогона (run_eval.py).
На следующих этапах он же станет «мозгом» голосового бота.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")
load_dotenv(HERE / ".env")

# Действия, которые бот может вернуть в поле action.
ACTIONS = ["continue", "confirmed", "rejected", "fake", "transfer", "callback", "end"]
FINAL_ACTIONS = set(ACTIONS) - {"continue"}

ADDRESS_FIELDS = ["region", "district", "street", "landmark", "delivery_time", "quantity"]

# Схема ответа. vLLM не даст модели выдать JSON, который ей не соответствует.
REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "say": {"type": "string"},
        "lang": {"type": "string", "enum": ["uz", "ru"]},
        "action": {"type": "string", "enum": ACTIONS},
        "fields": {
            "type": "object",
            "properties": {f: {"type": ["string", "null"]} for f in ADDRESS_FIELDS},
            "required": ADDRESS_FIELDS,
            "additionalProperties": False,
        },
        "callback_time": {"type": ["string", "null"]},
        "note": {"type": ["string", "null"]},
    },
    "required": ["say", "lang", "action", "fields", "callback_time", "note"],
    "additionalProperties": False,
}

CALL_START = "[QO'NG'IROQ BOSHLANDI — mijoz go'shakni oldi. Birinchi bo'lib salomlash.]"


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def make_client(base_url: str | None = None) -> OpenAI:
    return OpenAI(base_url=base_url or env("LLM_BASE_URL", "http://localhost:8000/v1"),
                  api_key=env("LLM_API_KEY", "local"), timeout=120)


def model_name(client: OpenAI) -> str:
    """Имя модели, которую сейчас отдаёт сервер vLLM."""
    return env("LLM_MODEL") or client.models.list().data[0].id


def load_system_prompt() -> str:
    path = Path(env("PROMPT_FILE", "prompts/system_prompt_uz.md"))
    if not path.is_absolute():
        path = HERE / path
    text = path.read_text(encoding="utf-8")
    product = HERE / "prompts" / "product.md"
    if "{{PRODUCT}}" in text and product.exists():
        text = text.replace("{{PRODUCT}}", product.read_text(encoding="utf-8"))
    return text


def extra_body() -> dict:
    body: dict = {}
    if env("DISABLE_THINKING", "1") == "1":
        # Отключаем «размышления» у моделей, которые их поддерживают: для звонка важна скорость.
        body["chat_template_kwargs"] = {"enable_thinking": False}
    return body


@dataclass
class Turn:
    raw: str
    reply: dict
    seconds: float


@dataclass
class Conversation:
    client: OpenAI
    model: str
    system_prompt: str
    messages: list = field(default_factory=list)
    fields: dict = field(default_factory=lambda: {f: None for f in ADDRESS_FIELDS})
    turns: list = field(default_factory=list)

    def __post_init__(self):
        self.messages = [{"role": "system", "content": self.system_prompt}]

    def _ask(self) -> Turn:
        t0 = time.perf_counter()
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=self.messages,
            temperature=float(env("TEMPERATURE", "0.3")),
            max_tokens=int(env("MAX_REPLY_TOKENS", "400")),
            response_format={"type": "json_schema",
                             "json_schema": {"name": "bot_reply", "schema": REPLY_SCHEMA}},
            extra_body=extra_body(),
        )
        seconds = time.perf_counter() - t0
        raw = resp.choices[0].message.content or ""
        try:
            reply = json.loads(raw)
        except json.JSONDecodeError:
            reply = {"say": raw, "lang": "uz", "action": "continue", "fields": {},
                     "callback_time": None, "note": "INVALID_JSON"}
        for k, v in (reply.get("fields") or {}).items():
            if v:
                self.fields[k] = v
        # В историю кладём сырой JSON — так модель видит свой формат и держит его.
        self.messages.append({"role": "assistant", "content": raw})
        turn = Turn(raw=raw, reply=reply, seconds=seconds)
        self.turns.append(turn)
        return turn

    def start(self) -> Turn:
        self.messages.append({"role": "user", "content": CALL_START})
        return self._ask()

    def customer_says(self, text: str) -> Turn:
        self.messages.append({"role": "user", "content": text})
        return self._ask()


def new_conversation(client: OpenAI | None = None) -> Conversation:
    client = client or make_client()
    return Conversation(client=client, model=model_name(client), system_prompt=load_system_prompt())
