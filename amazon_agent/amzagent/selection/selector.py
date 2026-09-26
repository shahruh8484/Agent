"""Picks which Amazon products go on a site.

Deterministic on purpose (no LLM): the same search results always give
the same ranking, and every rejection has a plain reason in the run log.

A product qualifies when it has an image, a current price, and enough
social proof (rating + review count). Qualified products are ranked by
    rating * log10(reviews)          social proof, diminishing in volume
  + discount bonus                   push traffic reacts to deals
  + sales-rank bonus                 bestsellers convert better
  + EPC bonus                        Creator Connections pays more per click
"""
from __future__ import annotations

import math

from amzagent.models import Product


def score(product: Product) -> float:
    s = (product.rating or 0) * math.log10(max(product.review_count, 1))
    if product.savings_percent:
        s += min(product.savings_percent, 60) / 10
    if product.sales_rank:
        # rank 1 -> +3, rank 1000 -> +1, rank 1M -> ~0
        s += max(0.0, 3 - math.log10(product.sales_rank) * 0.5)
    if product.epc:
        # $1 of estimated earnings per click ~ one extra star of social proof
        s += min(product.epc, 5) * 2
    return round(s, 3)


def rejection_reason(product: Product, min_rating: float, min_reviews: int) -> str | None:
    if not product.image_url:
        return "no image"
    if product.price is None:
        return "no price / unavailable"
    if (product.rating or 0) < min_rating:
        return f"rating {product.rating} < {min_rating}"
    if product.review_count < min_reviews:
        return f"{product.review_count} reviews < {min_reviews}"
    return None


def select_products(
    candidates: list[Product],
    limit: int,
    min_rating: float,
    min_reviews: int,
) -> tuple[list[Product], dict[str, str]]:
    """Return (selected products best-first, {asin: rejection reason})."""
    seen: set[str] = set()
    qualified: list[Product] = []
    rejected: dict[str, str] = {}
    for p in candidates:
        if p.asin in seen:
            continue
        seen.add(p.asin)
        reason = rejection_reason(p, min_rating, min_reviews)
        if reason:
            rejected[p.asin] = reason
        else:
            qualified.append(p)
    qualified.sort(key=score, reverse=True)
    return qualified[:limit], rejected
