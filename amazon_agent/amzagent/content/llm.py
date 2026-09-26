"""Text generation backends (Anthropic by default, OpenAI optional)."""
from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from amzagent.config import Settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def generate(self, system: str, prompt: str, max_tokens: int = 2048) -> str: ...


class AnthropicLLM:
    def __init__(self, settings: Settings):
        if not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set.")
        import anthropic

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    def generate(self, system: str, prompt: str, max_tokens: int = 2048) -> str:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise LLMError(f"Anthropic API error: {exc}") from exc
        return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")


class OpenAILLM:
    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise LLMError("OPENAI_API_KEY is not set.")
        import openai

        self._client = openai.OpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model

    def generate(self, system: str, prompt: str, max_tokens: int = 2048) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception as exc:
            raise LLMError(f"OpenAI API error: {exc}") from exc
        return response.choices[0].message.content or ""


def get_llm(settings: Settings) -> LLM:
    if settings.llm_provider == "anthropic":
        return AnthropicLLM(settings)
    if settings.llm_provider == "openai":
        return OpenAILLM(settings)
    raise LLMError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")


def parse_json(text: str) -> dict | list:
    """Extract the JSON value from a model reply, tolerating ```json fences
    or a sentence before/after it."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise LLMError(f"No JSON in model reply: {text[:200]!r}")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise LLMError(f"Invalid JSON in model reply: {exc}") from exc
    return value
