"""Sections and buying guides for a site.

A flat list of a hundred mixed products reads like a thin affiliate page.
The model groups a site's products into topics ("Air Purifiers", "Car
Accessories", ...) and writes a comparison guide for each: an intro, how
to choose, who each product suits, and a verdict — only from the titles,
brands and feature bullets it is given (same hard rules as all copy).
"""
from __future__ import annotations

import re

from amzagent.content.llm import LLM, LLMError, parse_json
from amzagent.content.writer import AMAZON_MARKS, RULES
from amzagent.models import GuidePick, Product, ProductCopy, SiteSection

SYSTEM = (
    "You organise and write buying guides for an independent product-review "
    "website that earns Amazon affiliate commissions. " + RULES + " Reply with JSON only."
)
OTHER = "More Picks"
MIN_SECTION = 2  # smaller groups go to "More Picks"
MAX_SECTIONS = 12
GUIDE_PRODUCTS = 8  # products compared in one guide


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "picks"


def group_products(llm: LLM, items: list[tuple[Product, ProductCopy]],
                   language: str) -> list[SiteSection]:
    """Sections in the order of their best product; `items` come best first.
    Products the model leaves out (or puts in tiny groups) go to "More Picks"."""
    lines = [f"{p.asin} | {c.product_type or '-'} | {p.title[:90]}" for p, c in items]
    prompt = (
        f"TASK: group products into site sections.\nLanguage: {language}\n"
        "Products (ASIN | type | title):\n" + "\n".join(lines) + "\n\n"
        f"Group them into 3-{MAX_SECTIONS} topical sections a shopper would browse, "
        "e.g. \"Air Purifiers\", \"Car Accessories\", \"Kitchen Gadgets\". Each section "
        f"needs at least {MIN_SECTION} products; put odd ones out in no section. Section "
        "names: 1-3 words, plural, no brand names, no Amazon trademarks.\n"
        'Return a JSON array: [{"name": "...", "asins": ["...", ...]}, ...]'
    )
    data = parse_json(llm.generate(SYSTEM, prompt, max_tokens=4000))
    if not isinstance(data, list):
        raise LLMError("Expected a JSON array of sections")
    rank = {p.asin: i for i, (p, _) in enumerate(items)}
    placed: set[str] = set()
    sections: list[SiteSection] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = AMAZON_MARKS.sub("", str(entry.get("name", ""))).strip()[:40]
        asins = [a for a in dict.fromkeys(entry.get("asins") or [])
                 if isinstance(a, str) and a in rank and a not in placed]
        if not name or name.lower() == OTHER.lower() or len(asins) < MIN_SECTION:
            continue
        asins.sort(key=rank.get)
        placed.update(asins)
        sections.append(SiteSection(slug=slugify(name), name=name, asins=asins))
    sections.sort(key=lambda s: rank[s.asins[0]])
    sections = sections[:MAX_SECTIONS]
    placed = {a for s in sections for a in s.asins}
    leftover = [p.asin for p, _ in items if p.asin not in placed]
    if leftover:
        sections.append(SiteSection(slug=slugify(OTHER), name=OTHER, asins=leftover))
    seen: set[str] = set()
    for s in sections:  # unique slugs
        base, n = s.slug, 2
        while s.slug in seen:
            s.slug, n = f"{base}-{n}", n + 1
        seen.add(s.slug)
    return sections


def write_guide(llm: LLM, section: SiteSection, products: dict[str, tuple[Product, ProductCopy]],
                language: str) -> SiteSection:
    """Fill a section's guide from its best GUIDE_PRODUCTS products."""
    chosen = [products[a] for a in section.asins if a in products][:GUIDE_PRODUCTS]
    lines = [
        f"- asin: {p.asin}\n  title: {p.title}\n  brand: {p.brand}\n"
        f"  features: {'; '.join(p.features[:5]) or 'n/a'}"
        for p, _ in chosen
    ]
    prompt = (
        f"TASK: write a buying guide.\nSection: {section.name}\nLanguage: {language}\n"
        "Products, best first:\n" + "\n".join(lines) + "\n\n"
        "Return JSON with keys:\n"
        '  "guide_title": e.g. "Best Air Purifiers for Home: 6 Picks Compared" '
        "(no year, no Amazon trademarks),\n"
        '  "intro": 2-3 sentences on what this guide helps with,\n'
        '  "how_to_choose": 4-5 practical tips for choosing ANY product of this kind,\n'
        '  "picks": one object per product, in the same order: {"asin", "best_for": '
        '"Best for ..." under 8 words, "blurb": 1-2 sentences from its features},\n'
        '  "verdict": 2 sentences summing up which pick suits whom.'
    )
    data = parse_json(llm.generate(SYSTEM, prompt, max_tokens=3000))
    if not isinstance(data, dict):
        raise LLMError("Expected a JSON object for the guide")
    wanted = {p.asin for p, _ in chosen}
    picks = []
    for entry in data.get("picks") or []:
        if isinstance(entry, dict) and entry.get("asin") in wanted:
            picks.append(GuidePick(asin=entry["asin"], best_for=str(entry.get("best_for", ""))[:80],
                                   blurb=str(entry.get("blurb", ""))[:400]))
            wanted.discard(entry["asin"])
    if not picks:
        raise LLMError("Guide has no picks")
    title = str(data.get("guide_title", "")).strip()
    section.guide_title = (title if title and not AMAZON_MARKS.search(title)
                           else f"Best {section.name}: Our Picks Compared")[:120]
    section.intro = str(data.get("intro", ""))[:800]
    section.how_to_choose = [str(t)[:300] for t in data.get("how_to_choose") or []][:6]
    section.picks = picks
    section.verdict = str(data.get("verdict", ""))[:600]
    return section
