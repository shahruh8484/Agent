"""Fetches competitor landing pages the user points the agent at, so
generate_landing_copy() can use their structure/offer framing as a style
reference instead of writing blind from ad copy alone.
"""
from __future__ import annotations

from html.parser import HTMLParser

import requests

# Keeps prompts a reasonable size — a few competitor pages' worth of text,
# not entire HTML documents.
MAX_REFERENCE_CHARS = 3000

_SKIP_TAGS = {"script", "style", "noscript", "svg"}


class ReferenceFetchError(RuntimeError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self.chunks.append(text)


def fetch_reference_text(url: str, timeout: int = 15) -> str:
    """Plain visible text from a competitor's landing page URL, truncated
    to MAX_REFERENCE_CHARS. Raises ReferenceFetchError on any failure —
    callers should treat a bad reference URL as best-effort, not fatal.
    """
    try:
        response = requests.get(
            url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}
        )
    except requests.RequestException as exc:
        raise ReferenceFetchError(f"Could not fetch {url}: {exc}") from exc

    if response.status_code != 200:
        raise ReferenceFetchError(f"{url} returned status {response.status_code}")

    extractor = _TextExtractor()
    extractor.feed(response.text)
    text = " ".join(extractor.chunks)
    return text[:MAX_REFERENCE_CHARS]
