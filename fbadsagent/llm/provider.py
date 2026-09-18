"""Provider-agnostic text generation used by the copywriter, competitor
analysis and landing page modules. Backed by Anthropic (default), OpenAI,
or Gemini (google-genai has a free tier — good if you want $0 text gen).
"""
from __future__ import annotations

import base64
import logging
import mimetypes
from abc import ABC, abstractmethod
from pathlib import Path

from fbadsagent.config import Settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def _guess_media_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "image/png"


def _encode_image(path: str) -> tuple[str, str]:
    media_type = _guess_media_type(path)
    data = base64.standard_b64encode(Path(path).read_bytes()).decode()
    return media_type, data


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, system: str, prompt: str, max_tokens: int = 1024) -> str:
        """Return the model's raw text response."""

    @abstractmethod
    def generate_with_images(
        self, system: str, prompt: str, image_paths: list[str], max_tokens: int = 1024
    ) -> str:
        """Return the model's raw text response, given one or more local
        image files as additional context (e.g. competitor landing page
        screenshots) alongside the text prompt."""


class AnthropicProvider(LLMProvider):
    def __init__(self, settings: Settings):
        if not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set.")
        import anthropic

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    def generate(self, system: str, prompt: str, max_tokens: int = 1024) -> str:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            logger.exception("Anthropic API call failed")
            raise LLMError(f"Anthropic API error: {exc}") from exc
        return "".join(block.text for block in response.content if hasattr(block, "text"))

    def generate_with_images(
        self, system: str, prompt: str, image_paths: list[str], max_tokens: int = 1024
    ) -> str:
        content = []
        for path in image_paths:
            media_type, data = _encode_image(path)
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                }
            )
        content.append({"type": "text", "text": prompt})
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as exc:
            logger.exception("Anthropic vision API call failed")
            raise LLMError(f"Anthropic API error: {exc}") from exc
        return "".join(block.text for block in response.content if hasattr(block, "text"))


class OpenAIProvider(LLMProvider):
    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise LLMError("OPENAI_API_KEY is not set.")
        import openai

        self._client = openai.OpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model

    def generate(self, system: str, prompt: str, max_tokens: int = 1024) -> str:
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
            logger.exception("OpenAI API call failed")
            raise LLMError(f"OpenAI API error: {exc}") from exc
        return response.choices[0].message.content or ""

    def generate_with_images(
        self, system: str, prompt: str, image_paths: list[str], max_tokens: int = 1024
    ) -> str:
        content = [{"type": "text", "text": prompt}]
        for path in image_paths:
            media_type, data = _encode_image(path)
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}}
            )
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
            )
        except Exception as exc:
            logger.exception("OpenAI vision API call failed")
            raise LLMError(f"OpenAI API error: {exc}") from exc
        return response.choices[0].message.content or ""


class GeminiProvider(LLMProvider):
    def __init__(self, settings: Settings):
        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not set.")
        from google import genai

        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model

    def generate(self, system: str, prompt: str, max_tokens: int = 1024) -> str:
        from google.genai import types

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=max_tokens,
                ),
            )
        except Exception as exc:
            logger.exception("Gemini API call failed")
            raise LLMError(f"Gemini API error: {exc}") from exc
        return response.text or ""

    def generate_with_images(
        self, system: str, prompt: str, image_paths: list[str], max_tokens: int = 1024
    ) -> str:
        from google.genai import types

        parts = []
        for path in image_paths:
            media_type = _guess_media_type(path)
            parts.append(types.Part.from_bytes(data=Path(path).read_bytes(), mime_type=media_type))
        parts.append(types.Part.from_text(text=prompt))

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=parts,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=max_tokens,
                ),
            )
        except Exception as exc:
            logger.exception("Gemini vision API call failed")
            raise LLMError(f"Gemini API error: {exc}") from exc
        return response.text or ""


def get_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(settings)
    if settings.llm_provider == "openai":
        return OpenAIProvider(settings)
    if settings.llm_provider == "gemini":
        return GeminiProvider(settings)
    raise LLMError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
