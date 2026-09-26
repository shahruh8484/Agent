"""Niche discovery: the agent picks what to build sites about.

1. The LLM brainstorms candidate niches suited to push traffic (impulse
   buys, broad appeal, mid-range prices, no restricted categories).
2. Each candidate is tested against the live Amazon catalog: how many
   products pass the selector, how strong they are, and whether prices sit
   in the range where a commission is worth a paid click.
3. The best-scoring candidates become niches.

The LLM only proposes; Amazon's data decides.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from amzagent.amazon.catalog import Catalog, CatalogError
from amzagent.content.llm import LLM, LLMError, parse_json
from amzagent.selection.selector import score, select_products

SEARCH_INDEXES = [
    "Electronics", "HomeAndKitchen", "SportsAndOutdoors", "Beauty", "ToysAndGames",
    "PetSupplies", "ToolsAndHomeImprovement", "OfficeProducts", "Automotive",
    "GardenAndOutdoor", "Baby", "Fashion", "VideoGames",
]

# Commission on a sub-$15 item rarely pays for the clicks it takes to sell
# it; above ~$120 push-traffic visitors seldom buy on impulse.
PRICE_SWEET_SPOT = (20.0, 120.0)
MIN_QUALIFIED = 6

SYSTEM = (
    "You are an Amazon affiliate marketer choosing product niches for small "
    "review sites promoted with web push-notification ads (cheap, broad, "
    "mobile, impulse-driven traffic). Reply with JSON only."
)


@dataclass
class NicheCandidate:
    keywords: str
    search_index: str
    qualified: int = 0
    median_price: float | None = None
    avg_score: float = 0.0
    total: float = 0.0
    reason: str = ""


def propose_niches(llm: LLM, n: int, exclude: list[str], country: str) -> list[NicheCandidate]:
    prompt = (
        f"Marketplace: Amazon {country}.\n"
        f"Propose {n} distinct product niches. Good niches: impulse-friendly, "
        "giftable or problem-solving, broad audience, typical price $20-$120, "
        "many well-reviewed products. Avoid: supplements/medicine/medical "
        "claims, adult, weapons, gambling, tobacco, baby formula, digital "
        "goods, gift cards, and trademarked brand names.\n"
        f"Do not repeat these existing niches: {', '.join(exclude) or 'none'}.\n"
        f"Allowed search_index values: {', '.join(SEARCH_INDEXES)}.\n"
        'Return a JSON array of {"keywords": "2-4 word English search '
        'phrase", "search_index": "..."}.'
    )
    data = parse_json(llm.generate(SYSTEM, prompt, max_tokens=1500))
    if not isinstance(data, list):
        raise LLMError("Expected a JSON array of niches")
    taken = {e.strip().lower() for e in exclude}
    out: list[NicheCandidate] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        keywords = str(entry.get("keywords", "")).strip()
        if not keywords or keywords.lower() in taken:
            continue
        taken.add(keywords.lower())
        index = entry.get("search_index")
        out.append(NicheCandidate(keywords, index if index in SEARCH_INDEXES else "All"))
    return out


def evaluate(
    catalog: Catalog, c: NicheCandidate, min_rating: float, min_reviews: int
) -> NicheCandidate:
    try:
        products = catalog.search(c.keywords, c.search_index)
    except CatalogError as exc:
        c.reason = f"search failed: {exc}"
        return c
    selected, _ = select_products(products, 10, min_rating, min_reviews)
    c.qualified = len(selected)
    if c.qualified < MIN_QUALIFIED:
        c.reason = f"only {c.qualified} good products"
        return c
    c.median_price = statistics.median(p.price for p in selected)
    c.avg_score = statistics.mean(score(p) for p in selected)
    low, high = PRICE_SWEET_SPOT
    if c.median_price < low:
        price_factor = max(0.3, c.median_price / low)
    elif c.median_price > high:
        price_factor = max(0.5, high / c.median_price)
    else:
        price_factor = 1.0
    c.total = round(c.avg_score * price_factor * (c.qualified / 10), 2)
    return c


def discover_niches(
    llm: LLM,
    catalog: Catalog,
    count: int,
    existing: list[str],
    country: str,
    min_rating: float,
    min_reviews: int,
    log,
) -> list[NicheCandidate]:
    """Return up to `count` winning candidates, best first."""
    candidates = propose_niches(llm, max(8, count * 4), existing, country)
    log(f"niche discovery: testing {len(candidates)} ideas on Amazon")
    for c in candidates:
        evaluate(catalog, c, min_rating, min_reviews)
        if c.reason:
            log(f"  - {c.keywords!r}: rejected ({c.reason})")
        else:
            log(
                f"  - {c.keywords!r}: {c.qualified} good products, median "
                f"${c.median_price:.0f}, score {c.total}"
            )
    winners = sorted((c for c in candidates if not c.reason), key=lambda c: c.total, reverse=True)
    return winners[:count]
