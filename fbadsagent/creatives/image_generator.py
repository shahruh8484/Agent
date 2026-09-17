"""Ad creative image generation, pluggable by provider.

``stub`` (the default) writes a small placeholder PNG and never touches the
network — safe for tests/offline runs. Set IMAGE_PROVIDER=openai and an
OPENAI_API_KEY in .env to generate real images via DALL-E, or
IMAGE_PROVIDER=gemini with GEMINI_API_KEY for Gemini's native image
generation (free-tier eligible, unlike OpenAI's pay-as-you-go).
"""
from __future__ import annotations

import base64
from pathlib import Path

from fbadsagent.config import Settings
from fbadsagent.models import AdCreativeCopy, GeneratedImage

# 1x1 transparent PNG, used as the stub placeholder image content.
_PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class ImageGenerationError(RuntimeError):
    pass


def generate_images(
    settings: Settings, creatives: list[AdCreativeCopy]
) -> list[GeneratedImage]:
    out_dir = Path(settings.output_dir) / "creatives"
    out_dir.mkdir(parents=True, exist_ok=True)

    if settings.image_provider == "stub":
        return [_generate_stub(out_dir, c) for c in creatives]
    if settings.image_provider == "openai":
        return [_generate_openai(settings, out_dir, c) for c in creatives]
    if settings.image_provider == "gemini":
        return [_generate_gemini(settings, out_dir, c) for c in creatives]
    raise ImageGenerationError(f"Unknown IMAGE_PROVIDER: {settings.image_provider!r}")


def _generate_stub(out_dir: Path, creative: AdCreativeCopy) -> GeneratedImage:
    path = out_dir / f"{creative.variant_id}.png"
    path.write_bytes(_PLACEHOLDER_PNG)
    return GeneratedImage(
        variant_id=creative.variant_id,
        path=str(path),
        prompt=creative.image_prompt,
        provider="stub",
    )


def _generate_openai(
    settings: Settings, out_dir: Path, creative: AdCreativeCopy
) -> GeneratedImage:
    if not settings.openai_api_key:
        raise ImageGenerationError("OPENAI_API_KEY is not set.")
    import openai

    client = openai.OpenAI(api_key=settings.openai_api_key)
    result = client.images.generate(
        model=settings.openai_image_model,
        prompt=creative.image_prompt,
        size="1024x1024",
        n=1,
    )
    b64_data = result.data[0].b64_json
    path = out_dir / f"{creative.variant_id}.png"
    path.write_bytes(base64.b64decode(b64_data))
    return GeneratedImage(
        variant_id=creative.variant_id,
        path=str(path),
        prompt=creative.image_prompt,
        provider="openai",
    )


def _generate_gemini(
    settings: Settings, out_dir: Path, creative: AdCreativeCopy
) -> GeneratedImage:
    if not settings.gemini_api_key:
        raise ImageGenerationError("GEMINI_API_KEY is not set.")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    try:
        response = client.models.generate_content(
            model=settings.gemini_image_model,
            contents=creative.image_prompt,
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )
    except Exception as exc:
        raise ImageGenerationError(f"Gemini image API error: {exc}") from exc

    image_bytes = None
    for part in response.candidates[0].content.parts:
        inline_data = getattr(part, "inline_data", None)
        if inline_data is not None:
            image_bytes = inline_data.data
            break
    if image_bytes is None:
        raise ImageGenerationError("Gemini response contained no image data.")
    if isinstance(image_bytes, str):
        image_bytes = base64.b64decode(image_bytes)

    path = out_dir / f"{creative.variant_id}.png"
    path.write_bytes(image_bytes)
    return GeneratedImage(
        variant_id=creative.variant_id,
        path=str(path),
        prompt=creative.image_prompt,
        provider="gemini",
    )
