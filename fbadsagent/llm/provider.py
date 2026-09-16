"""Provider-agnostic text generation used by the copywriter, competitor
analysis and landing page modules. Backed by Anthropic (default) or OpenAI.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from fbadsagent.config import Settings


class LLMError(RuntimeError):
    pass


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, system: str, prompt: str, max_tokens: int = 1024) -> str:
        """Return the model's raw text response."""


class AnthropicProvider(LLMProvider):
    def __init__(self, settings: Settings):
        if not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set.")
        import anthropic

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    def generate(self, system: str, prompt: str, max_tokens: int = 1024) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if hasattr(block, "text"))


class OpenAIProvider(LLMProvider):
    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise LLMError("OPENAI_API_KEY is not set.")
        import openai

        self._client = openai.OpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model

    def generate(self, system: str, prompt: str, max_tokens: int = 1024) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or ""


def get_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(settings)
    if settings.llm_provider == "openai":
        return OpenAIProvider(settings)
    raise LLMError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
