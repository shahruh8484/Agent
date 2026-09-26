"""Push creative images (icon + main image), drawn locally with Pillow.

Amazon's product images may only be shown on your own site, not in
off-site ads, so push creatives use generated artwork instead: the site's
initial on the icon, and the push title on a colored banner.
"""
from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ICON_SIZE = (192, 192)
IMAGE_SIZE = (492, 328)

PALETTES = [
    ((20, 83, 136), (46, 144, 209)),
    ((120, 38, 96), (214, 82, 130)),
    ((24, 110, 80), (70, 190, 130)),
    ((150, 70, 20), (235, 140, 50)),
    ((60, 50, 140), (120, 110, 230)),
]


def _palette(seed: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    return PALETTES[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(PALETTES)]


def _gradient(size: tuple[int, int], top, bottom) -> Image.Image:
    img = Image.new("RGB", size, top)
    draw = ImageDraw.Draw(img)
    for y in range(size[1]):
        t = y / max(size[1] - 1, 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (size[0], y)], fill=color)
    return img


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def render_creatives(out_dir: Path, name: str, site_title: str, headline: str) -> tuple[Path, Path]:
    """Write <name>-icon.png and <name>-image.png into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    top, bottom = _palette(site_title)

    icon = _gradient(ICON_SIZE, top, bottom)
    draw = ImageDraw.Draw(icon)
    initial = (site_title.strip()[:1] or "?").upper()
    draw.text((ICON_SIZE[0] / 2, ICON_SIZE[1] / 2), initial, font=_font(110),
              fill="white", anchor="mm")
    icon_path = out_dir / f"{name}-icon.png"
    icon.save(icon_path)

    image = _gradient(IMAGE_SIZE, top, bottom)
    draw = ImageDraw.Draw(image)
    lines = textwrap.wrap(headline, width=18)[:3]
    y = IMAGE_SIZE[1] / 2 - (len(lines) - 1) * 26
    for line in lines:
        draw.text((IMAGE_SIZE[0] / 2, y), line, font=_font(44), fill="white", anchor="mm")
        y += 52
    draw.text((IMAGE_SIZE[0] / 2, IMAGE_SIZE[1] - 24), site_title, font=_font(20),
              fill=(255, 255, 255), anchor="mm")
    image_path = out_dir / f"{name}-image.png"
    image.save(image_path)
    return icon_path, image_path
