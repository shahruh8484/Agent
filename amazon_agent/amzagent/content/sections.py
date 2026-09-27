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
from amzagent.models import (
    Article,
    ArticlePart,
    Collection,
    CollectionItem,
    FaqItem,
    GuidePick,
    Product,
    ProductCopy,
    SiteSection,
    Versus,
    VersusRow,
)

SYSTEM = (
    "You organise and write buying guides for an independent product-review "
    "website that earns Amazon affiliate commissions. " + RULES + " Reply with JSON only."
)
OTHER = "More Picks"
MIN_SECTION = 2  # smaller groups go to "More Picks"
MAX_SECTIONS = 12
GUIDE_PRODUCTS = 8  # products compared in one guide
# Bump when guides gain fields: older plans are rebuilt once.
# 2: FAQ. 3: who it's for, care tips, advice article. 4: more articles,
# head-to-heads, gift and seasonal collections.
PLAN_VERSION = 5

# Advice articles per section: the key is part of the prompt's topic.
ARTICLE_TOPICS = {
    "choose": "choosing and using {name}",
    "mistakes": "common mistakes people make when buying and using {name}, and how to avoid them",
    "care": "how to look after {name} and get the most out of them over the years",
}

GIFT_BUDGETS = (25, 50, 100)

# US shopping seasons: month -> (key, title idea, what the list is about).
SEASONS = {
    1: ("new-year", "New Year, Fresh Start", "home organisation and good-habit picks"),
    2: ("valentines", "Valentine's Day Gift Ideas", "thoughtful gifts for a partner"),
    3: ("spring", "Spring Refresh Essentials", "spring cleaning and home refresh picks"),
    4: ("spring", "Spring Refresh Essentials", "spring cleaning and home refresh picks"),
    5: ("mothers-day", "Mother's Day Gift Ideas", "gifts for mom"),
    6: ("fathers-day", "Father's Day Gift Ideas", "gifts for dad"),
    7: ("summer", "Summer Travel and Outdoor Essentials", "travel, car and outdoor picks"),
    8: ("back-to-school", "Back to School Essentials", "dorm, study and commute picks"),
    9: ("fall", "Fall Home Refresh", "cosy home, air quality and seasonal comfort picks"),
    10: ("fall", "Fall Home Refresh", "cosy home, air quality and seasonal comfort picks"),
    11: ("holiday", "Holiday Gift Guide", "gifts for everyone on the list"),
    12: ("holiday", "Holiday Gift Guide", "gifts for everyone on the list"),
}


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
        "e.g. \"Air Purifiers\", \"Car Accessories\", \"Sinus Care\". Each section "
        f"needs at least {MIN_SECTION} products; put odd ones out in no section. Section "
        "names: 1-3 words, plural, no brand names, no Amazon trademarks.\n"
        "A product goes into a section only if a shopper would look for it there by "
        "what the product is and where it is used: a sinus rinse is health care, not a "
        "kitchen item; a phone mount is a car accessory. No catch-all sections such as "
        "\"Gadgets\", \"Essentials\" or \"Home Products\" - leave a product out rather "
        "than force it into a section where it doesn't belong.\n"
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
        '  "verdict": 2 sentences summing up which pick suits whom,\n'
        '  "faq": 3-4 questions shoppers commonly ask about choosing or using this '
        'kind of product, as [{"q": "...", "a": "2-3 sentence answer"}] — general '
        "advice, no claims about specific models, no prices,\n"
        '  "who_for": 2 sentences on who should buy this kind of product and who '
        "probably doesn't need one,\n"
        '  "care_tips": 3-4 short tips on using and looking after this kind of '
        "product (cleaning, filters, storage, safety) — general advice only."
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
    section.faq = [FaqItem(q=str(f["q"])[:200], a=str(f["a"])[:600])
                   for f in data.get("faq") or []
                   if isinstance(f, dict) and f.get("q") and f.get("a")][:5]
    section.who_for = str(data.get("who_for", ""))[:500]
    section.care_tips = [str(t)[:300] for t in data.get("care_tips") or []
                         if isinstance(t, str)][:5]
    return section


def write_article(llm: LLM, section: SiteSection, language: str,
                  topic: str = "choose") -> Article:
    """A general advice article about this kind of product (no model claims)."""
    subject = ARTICLE_TOPICS.get(topic, ARTICLE_TOPICS["choose"]).format(name=section.name.lower())
    prompt = (
        f"TASK: write an advice article.\nTopic: {subject}\n"
        f"Language: {language}\n\n"
        "Write a practical explainer a shopper would read before buying, like a "
        "consumer magazine's advice column. General knowledge about this kind of "
        "product only: no specific brands or models, no prices, no statistics or "
        "test results you can't be sure of, no claims of hands-on testing.\n"
        "Return JSON with keys:\n"
        '  "title": e.g. "How to Choose an Air Purifier for Your Home" (no Amazon '
        "trademarks),\n"
        '  "summary": 1-2 sentences,\n'
        '  "parts": 4-6 sections as [{"heading": "...", "paragraphs": ["...", "..."]}], '
        "each with 1-3 short paragraphs (e.g. what to look for, sizes and types, "
        "common mistakes, care and running costs)."
    )
    data = parse_json(llm.generate(SYSTEM, prompt, max_tokens=3000))
    if not isinstance(data, dict):
        raise LLMError("Expected a JSON object for the article")
    parts = []
    for part in data.get("parts") or []:
        if isinstance(part, dict) and part.get("heading"):
            paragraphs = [str(x)[:1200] for x in part.get("paragraphs") or [] if str(x).strip()]
            if paragraphs:
                parts.append(ArticlePart(heading=str(part["heading"])[:120],
                                         paragraphs=paragraphs[:4]))
    title = str(data.get("title", "")).strip()
    if not parts or not title:
        raise LLMError("Article has no title or sections")
    if AMAZON_MARKS.search(title):
        title = f"How to Choose {section.name}"
    return Article(slug=slugify(title)[:80], title=title[:140],
                   summary=str(data.get("summary", ""))[:400], parts=parts[:6], topic=topic)


def _facts(p: Product) -> str:
    return (f"- asin: {p.asin}\n  title: {p.title}\n  brand: {p.brand}\n"
            f"  features: {'; '.join(p.features[:6]) or 'n/a'}")


def write_versus(llm: LLM, section: SiteSection, a: Product, b: Product,
                 language: str) -> Versus:
    """Head-to-head of two products, only from their listed features."""
    prompt = (
        f"TASK: write a head-to-head comparison.\nSection: {section.name}\nLanguage: {language}\n"
        "Products:\n" + _facts(a) + "\n" + _facts(b) + "\n\n"
        "Compare ONLY what the titles and features say; where a feature isn't listed "
        'for one product, write "not listed". No prices, no ratings, no testing claims.\n'
        "Return JSON with keys:\n"
        '  "intro": 2 sentences,\n'
        '  "rows": 4-6 aspects as [{"aspect": "...", "a": "...", "b": "..."}] (short cells),\n'
        '  "choose_a": 1-2 sentences "Choose the first if ...",\n'
        '  "choose_b": 1-2 sentences "Choose the second if ...",\n'
        '  "verdict": 2 sentences.'
    )
    data = parse_json(llm.generate(SYSTEM, prompt, max_tokens=2000))
    if not isinstance(data, dict):
        raise LLMError("Expected a JSON object for the comparison")
    rows = [VersusRow(aspect=str(r["aspect"])[:60], a=str(r.get("a", ""))[:160],
                      b=str(r.get("b", ""))[:160])
            for r in data.get("rows") or [] if isinstance(r, dict) and r.get("aspect")][:6]
    if not rows:
        raise LLMError("Comparison has no rows")
    short = [(p.brand or p.title.split()[0]).strip() + " " + " ".join(p.title.split()[1:3])
             for p in (a, b)]
    title = f"{short[0]} vs {short[1]}: Which Should You Buy?"
    return Versus(slug=slugify(f"{a.asin}-vs-{b.asin}"), title=AMAZON_MARKS.sub("", title)[:140],
                  a=a.asin, b=b.asin, intro=str(data.get("intro", ""))[:500], rows=rows,
                  choose_a=str(data.get("choose_a", ""))[:400],
                  choose_b=str(data.get("choose_b", ""))[:400],
                  verdict=str(data.get("verdict", ""))[:500])


def write_collection(llm: LLM, title: str, about: str, candidates: list[Product],
                     language: str, pick: int = 10, max_price: float | None = None,
                     choose: bool = False) -> Collection:
    """A gift/seasonal list: blurbs for `candidates` (best first), or — with
    `choose` — the model picks the `pick` that fit `about` best."""
    task = (f"Pick the {pick} products that best fit the theme and write a line for each."
            if choose else "Write a line for each product.")
    prompt = (
        f"TASK: write a gift list.\nTitle: {title}\nTheme: {about}\nLanguage: {language}\n"
        "Products:\n" + "\n".join(_facts(p) for p in candidates) + "\n\n" + task + "\n"
        "No prices, no ratings, no testing claims; only facts from the features.\n"
        'Return JSON: {"intro": "2 sentences", "items": [{"asin": "...", '
        '"blurb": "1 sentence on who it suits as a gift"}]}'
    )
    data = parse_json(llm.generate(SYSTEM, prompt, max_tokens=2500))
    if not isinstance(data, dict):
        raise LLMError("Expected a JSON object for the gift list")
    known = {p.asin for p in candidates}
    items, seen = [], set()
    for entry in data.get("items") or []:
        if isinstance(entry, dict) and entry.get("asin") in known and entry["asin"] not in seen:
            seen.add(entry["asin"])
            items.append(CollectionItem(asin=entry["asin"], blurb=str(entry.get("blurb", ""))[:300]))
    if len(items) < 3:
        raise LLMError("Gift list has too few items")
    return Collection(slug=slugify(title), title=title, intro=str(data.get("intro", ""))[:500],
                      max_price=max_price, items=items[:pick])
