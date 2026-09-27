"""Picks which Amazon products go on a site.

Deterministic on purpose (no LLM): the same search results always give
the same ranking, and every rejection has a plain reason in the run log.

A product qualifies when it has an image, a current price, and enough
social proof (rating + review count). The Creators API sends no reviews to
new accounts: then the rating/reviews read from a pasted Creator
Connections page are used, and without those the product is judged on
sales rank and discount alone. Qualified products are ranked by
    rating * log10(reviews)          social proof, diminishing in volume
  + discount bonus                   push traffic reacts to deals
  + sales-rank bonus                 bestsellers convert better
  + EPC bonus                        Creator Connections pays more per click
  + bonus-budget score               high first, low (budget nearly gone) last
"""
from __future__ import annotations

import math

from amzagent.amazon.creator_connections import BUDGET_BONUS
from amzagent.models import Product


def _social_proof(p: Product) -> tuple[float | None, int]:
    """(rating, reviews) from the API, else the pasted-page hint."""
    if p.rating is not None or p.review_count:
        return p.rating, p.review_count
    return p.hint_rating, p.hint_reviews


def score(product: Product) -> float:
    rating, reviews = _social_proof(product)
    s = (rating or 0) * math.log10(max(reviews, 1))
    if product.savings_percent:
        s += min(product.savings_percent, 60) / 10
    if product.sales_rank:
        # rank 1 -> +3, rank 1000 -> +1, rank 1M -> ~0
        s += max(0.0, 3 - math.log10(product.sales_rank) * 0.5)
    if product.epc:
        # $1 of estimated earnings per click ~ one extra star of social proof
        s += min(product.epc, 5) * 2
    s += BUDGET_BONUS.get(product.cc_budget, 0.0)
    return round(s, 3)


def rejection_reason(product: Product, min_rating: float, min_reviews: int) -> str | None:
    if not product.image_url:
        return "no image"
    if product.price is None:
        return "no price / unavailable"
    rating, reviews = _social_proof(product)
    if rating is None and not reviews:
        # The API sends no reviews for this account and there's no hint:
        # can't judge social proof, so rank by sales rank / discount instead.
        return None
    if (rating or 0) < min_rating:
        return f"rating {rating} < {min_rating}"
    if reviews < min_reviews:
        return f"{reviews} reviews < {min_reviews}"
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
