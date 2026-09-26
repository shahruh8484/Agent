"""LLM-written copy for the site and the push notifications.

The model only rewrites facts it is given (title, brand, feature bullets,
rating) — the prompts forbid inventing specs, prices, discounts or
urgency, since both Amazon and PropellerAds reject misleading claims and
prices in ads go stale within hours.
"""
from __future__ import annotations

from pydantic import ValidationError

from amzagent.content.llm import LLM, LLMError, parse_json
from amzagent.models import Product, ProductCopy, SiteCopy

RULES = (
    "Hard rules: use only facts present in the input. Never invent specs, "
    "prices, discounts, deadlines, stock levels, awards or testimonials. "
    "Never claim to have personally tested a product. Never mention a "
    "price or a percentage off. Never impersonate Amazon or say the site "
    "is Amazon. Plain text only, no emoji, no ALL CAPS."
)

SITE_SYSTEM = (
    "You write copy for a small independent product-review website that "
    "earns Amazon affiliate commissions. " + RULES + " Reply with JSON only."
)

PRODUCT_SYSTEM = (
    "You write short, honest product summaries for an independent review "
    "website, plus a push-notification ad that sends readers to that "
    "website's product page (not to Amazon). " + RULES + " Reply with JSON only."
)


def write_site_copy(llm: LLM, keywords: str, language: str) -> SiteCopy:
    prompt = (
        f"Niche: {keywords}\nLanguage: {language}\n\n"
        "Return JSON with keys:\n"
        '  "site_title": 2-4 word brand-like name for the site (not containing "Amazon"),\n'
        '  "tagline": under 10 words,\n'
        '  "intro": 2-3 sentences explaining the site picks well-rated '
        "products in this niche based on customer ratings and review counts."
    )
    data = parse_json(llm.generate(SITE_SYSTEM, prompt, max_tokens=600))
    try:
        return SiteCopy(**data)
    except (TypeError, ValidationError) as exc:
        raise LLMError(f"Bad site copy from model: {exc}") from exc


# Products per LLM call: a whole 30-product import in one reply can run
# past the output limit and come back as truncated, unparseable JSON.
COPY_BATCH = 8


def write_product_copy(llm: LLM, products: list[Product], language: str) -> dict[str, ProductCopy]:
    """Returns {asin: copy}; products the model skipped are simply missing.
    A failed batch is skipped (and retried on the next cycle) unless every
    batch failed."""
    result: dict[str, ProductCopy] = {}
    errors: list[LLMError] = []
    for start in range(0, len(products), COPY_BATCH):
        try:
            result.update(_write_copy_batch(llm, products[start : start + COPY_BATCH], language))
        except LLMError as exc:
            errors.append(exc)
    if errors and not result:
        raise errors[0]
    return result


def _write_copy_batch(llm: LLM, products: list[Product], language: str) -> dict[str, ProductCopy]:
    lines = []
    for p in products:
        lines.append(
            f"- asin: {p.asin}\n  title: {p.title}\n  brand: {p.brand}\n"
            f"  rating: {p.rating} from {p.review_count} reviews\n"
            f"  features: {'; '.join(p.features) or 'n/a'}"
        )
    prompt = (
        f"Language: {language}\nProducts:\n" + "\n".join(lines) + "\n\n"
        "Return a JSON array, one object per product, with keys:\n"
        '  "asin",\n'
        '  "summary": 2-3 sentences on who it suits and why, from the features,\n'
        '  "pros": 2-4 short bullets from the features,\n'
        '  "cons": 1-2 honest limitations implied by the features (or generic '
        'ones like "check size before buying"),\n'
        '  "push_title": push notification title, max 30 characters,\n'
        '  "push_text": push notification body, max 60 characters, curiosity '
        "about the product type, no price, no fake urgency."
    )
    data = parse_json(llm.generate(PRODUCT_SYSTEM, prompt, max_tokens=4000))
    if not isinstance(data, list):
        raise LLMError("Expected a JSON array of product copy")
    wanted = {p.asin for p in products}
    result: dict[str, ProductCopy] = {}
    for entry in data:
        if not isinstance(entry, dict) or entry.get("asin") not in wanted:
            continue
        try:
            copy = ProductCopy(**{k: v for k, v in entry.items() if k != "asin"})
        except ValidationError:
            continue
        copy.push_title = copy.push_title[:30]
        copy.push_text = copy.push_text[:60]
        result[entry["asin"]] = copy
    return result
