"""Import of Amazon Creator Connections opportunities.

The Creators API has no endpoint that lists Creator Connections campaigns
(only earnings reports), so the dashboard takes the text of the
"New Opportunities" / "Accepted" page as copied from the browser
(Ctrl+A, Ctrl+C) and this module pulls out every ASIN and its
"Estimated EPC". Live product data (rating, reviews, price, sales rank)
is then fetched through the API as for any other product.

Bonus commission only applies to campaigns you accepted in Associates
Central, so accept them there before importing.
"""
from __future__ import annotations

import math
import time
import re

from amzagent.models import Product

ASIN_RE = re.compile(r"\b(B0[A-Z0-9]{8})\b")
EPC_RE = re.compile(r"EPC[^$\n]*\$\s*([\d]+(?:\.\d+)?)", re.IGNORECASE)


def parse_opportunities(text: str) -> dict[str, float | None]:
    """{asin: estimated EPC in $ or None}, in page order."""
    matches = list(ASIN_RE.finditer(text.upper()))
    result: dict[str, float | None] = {}
    for i, m in enumerate(matches):
        asin = m.group(1)
        # The EPC line sits between this ASIN and the next one.
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        epc_match = EPC_RE.search(text, m.end(), end)
        epc = float(epc_match.group(1)) if epc_match else None
        if asin not in result or (epc is not None and result[asin] is None):
            result[asin] = epc
    return result


RATING_RE = re.compile(r"^\s*([1-5](?:\.\d)?)\s*(?:out of 5 stars)?\s*\(([\d,]+)\)")
NOISE_RE = re.compile(
    r"^(recommended|accept|accepted|new opportunit|estimated epc|budget availability|"
    r"limited time deal|ends in|list price|asin|sort by|filters|search|by clicking)",
    re.IGNORECASE,
)
PRICE_RE = re.compile(r"(\$\s*\d|\d+\s*%\s*off|^-\d+%)", re.IGNORECASE)


DANGLING = {"for", "with", "and", "&", "of", "the", "a", "to", "in", "-", "|", ",", "+"}


def _clean_title(line: str) -> str:
    """Card titles are cut mid-word with "..."; drop the cut word and any
    dangling connector so the title reads as a complete name."""
    cut = re.sub(r"\s*(\.\.\.|…)\s*$", "", line).strip()
    if cut == line.strip():
        return cut
    words = cut.split()[:-1] or cut.split()
    while len(words) > 1 and words[-1].lower().strip(",;:-") in DANGLING | {""}:
        words.pop()
    return " ".join(words).rstrip(",;:-& ")


def parse_opportunity_details(text: str) -> dict[str, dict]:
    """{asin: {"title", "brand", "rating", "reviews"}} read from the card
    text above each ASIN. Used only when the Creators API is unavailable:
    title/brand go on the site, rating/reviews only rank products (Amazon
    allows showing ratings only when they come from the API)."""
    upper = text.upper()
    matches = list(ASIN_RE.finditer(upper))
    details: dict[str, dict] = {}
    prev_end = 0
    for m in matches:
        segment = text[prev_end : m.start()]
        prev_end = m.end()
        # The previous card's EPC/budget lines come first; the current
        # card starts after its last "Accept" / "Budget availability".
        lines = [ln.strip() for ln in segment.splitlines() if ln.strip()]
        rating = reviews = None
        text_lines = []
        for ln in lines:
            r = RATING_RE.match(ln)
            if r:
                rating, reviews = float(r.group(1)), int(r.group(2).replace(",", ""))
                continue
            if NOISE_RE.match(ln) or PRICE_RE.search(ln) or len(ln) < 2:
                if NOISE_RE.match(ln) and ln.lower().startswith(("accept", "budget")):
                    text_lines = []  # everything before belonged to the previous card
                continue
            text_lines.append(ln)
        title = brand = ""
        if text_lines:
            title = _clean_title(max(text_lines, key=len))
            idx = max(range(len(text_lines)), key=lambda i: len(text_lines[i]))
            if idx > 0 and len(text_lines[idx - 1]) <= 40:
                brand = text_lines[idx - 1]
        asin = m.group(1)
        if asin not in details or (title and not details[asin]["title"]):
            details[asin] = {"title": title, "brand": brand, "rating": rating,
                             "reviews": reviews}
    return details


def marketplace_host(country: str) -> str:
    try:
        from amazon_creatorsapi.core.marketplaces import MARKETPLACES

        return MARKETPLACES.get(country.upper(), "www.amazon.com")
    except ImportError:
        return "www.amazon.com"


def accepted_link(host: str, asin: str, partner_tag: str) -> str:
    """The link Creator Connections' "Get associate link" gives for an
    accepted campaign, built the same way so the bonus is credited like a
    link copied from there (the tag is what attributes the sale)."""
    link_id = f"{asin}_{int(time.time() * 1000)}"
    return (f"https://{host}/dp/{asin}?ref=t_ac_spc_accepted_tile&linkCode=tr1"
            f"&tag={partner_tag}&linkId={link_id}")


def offline_products(
    asins: dict[str, float | None],
    meta: dict[str, dict],
    partner_tag: str,
    country: str,
    min_rating: float,
    min_reviews: int,
    limit: int,
) -> tuple[list[Product], dict[str, str]]:
    """Products built from the pasted page alone, best first, for when the
    Creators API refuses access. Returns (selected, {asin: rejection})."""
    host = marketplace_host(country)
    ranked: list[tuple[float, Product]] = []
    rejected: dict[str, str] = {}
    for asin, epc in asins.items():
        m = meta.get(asin) or {}
        rating, reviews = m.get("rating"), m.get("reviews")
        if not m.get("title"):
            rejected[asin] = "no title in pasted text"
            continue
        if rating is not None and rating < min_rating:
            rejected[asin] = f"rating {rating} < {min_rating}"
            continue
        if reviews is not None and reviews < min_reviews:
            rejected[asin] = f"{reviews} reviews < {min_reviews}"
            continue
        # Same weights as selector.score; rating/reviews rank but are never shown.
        rank = (rating or 4.0) * math.log10(max(reviews or 1, 1)) + min(epc or 0, 5) * 2
        ranked.append((rank, Product(
            asin=asin,
            title=m["title"],
            brand=m.get("brand", ""),
            url=accepted_link(host, asin, partner_tag),
            epc=epc,
            offline=True,
        )))
    ranked.sort(key=lambda r: r[0], reverse=True)
    return [p for _, p in ranked[:limit]], rejected
