"""Ad creative image generation, pluggable by provider.

``stub`` (the default) writes a small placeholder PNG and never touches the
network — safe for tests/offline runs. Set IMAGE_PROVIDER=openai and an
OPENAI_API_KEY in .env to generate real images.
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
