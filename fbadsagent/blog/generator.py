"""The blog agent: turns a topic into a site profile (name, tagline,
description), then keeps writing new, non-duplicate articles for it.

Articles come back from the LLM as structured JSON (title, summary,
sections of paragraphs) rather than HTML, so the public pages render them
through Jinja's autoescaping and a model can never inject markup/scripts.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from fbadsagent.llm.provider import LLMError, LLMProvider
from fbadsagent.models import ArticleSection, BlogArticle, BlogSite

# Titles of the most recent articles passed back to the model so it picks a
# new angle instead of rewriting an existing post.
MAX_EXISTING_TITLES_IN_PROMPT = 60

HONESTY_RULES = (
    "Honesty rules (never break these): do not invent statistics, study "
    "results, quotes, named experts, or sources. Do not make medical, "
    "legal, or financial promises. If a claim needs a number you don't "
    "reliably know, describe it qualitatively instead."
)

SITE_SYSTEM_PROMPT = (
    "You design small content websites. Given a topic, reply with ONLY a "
    "JSON object: {\"name\": short memorable site name, \"tagline\": one "
    "line, \"description\": 2-3 sentences for the home page and meta "
    "description, \"audience\": who the site is for}. Write every value in "
    "the requested language."
)

ARTICLE_SYSTEM_PROMPT = (
    "You are the staff writer of a content website. Write one genuinely "
    "useful, original article for its readers. Reply with ONLY a JSON "
    "object: {\"title\": string, \"summary\": 1-2 sentence teaser, "
    "\"sections\": [{\"heading\": string, \"paragraphs\": [string, ...]}], "
    "\"tags\": [3-5 short strings]}. Use 4-7 sections with 1-4 paragraphs "
    "each, plain text only (no Markdown, no HTML). " + HONESTY_RULES
)

_CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", "ў": "o", "қ": "q",
    "ғ": "g", "ҳ": "h",
}


class BlogGenerationError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def slugify(text: str, fallback: str = "post", max_length: int = 70) -> str:
    """URL slug that also works for Cyrillic (Russian/Uzbek) titles."""
    lowered = "".join(_CYRILLIC_TO_LATIN.get(ch, ch) for ch in text.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug[:max_length].rstrip("-") or fallback


def unique_slug(base: str, taken: set[str]) -> str:
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    return slug


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Some models wrap the object in prose — fall back to the outermost braces.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def generate_site_profile(llm: LLMProvider, topic: str, language: str) -> dict:
    prompt = f"Topic: {topic}\nLanguage: {language}"
    data = _parse_json(llm.generate(SITE_SYSTEM_PROMPT, prompt, max_tokens=600))
    name = str(data.get("name", "")).strip()
    if not name:
        raise BlogGenerationError("The model did not return a site name.")
    return {
        "name": name,
        "tagline": str(data.get("tagline", "")).strip(),
        "description": str(data.get("description", "")).strip(),
        "audience": str(data.get("audience", "")).strip(),
    }


def generate_article(
    llm: LLMProvider,
    site: BlogSite,
    existing_titles: list[str],
    taken_slugs: set[str],
    triggered_by: str = "manual",
) -> BlogArticle:
    recent = existing_titles[-MAX_EXISTING_TITLES_IN_PROMPT:]
    already = "\n".join(f"- {t}" for t in recent) or "(none yet — this is the first article)"
    prompt = (
        f"Site: {site.name} — {site.tagline}\n"
        f"Topic: {site.topic}\n"
        f"Audience: {site.audience or 'general readers interested in the topic'}\n"
        f"Language: write everything in {site.language}.\n\n"
        f"Articles already published (pick a clearly different subject or angle):\n{already}"
    )
    data = _parse_json(llm.generate(ARTICLE_SYSTEM_PROMPT, prompt, max_tokens=4000))

    title = str(data.get("title", "")).strip()
    sections = []
    for raw_section in data.get("sections") or []:
        if not isinstance(raw_section, dict):
            continue
        paragraphs = [str(p).strip() for p in raw_section.get("paragraphs") or [] if str(p).strip()]
        if paragraphs:
            sections.append(
                ArticleSection(heading=str(raw_section.get("heading", "")).strip(), paragraphs=paragraphs)
            )
    if not title or not sections:
        raise BlogGenerationError("The model returned an empty or malformed article.")
    if title.lower() in {t.lower() for t in existing_titles}:
        raise BlogGenerationError(f'Duplicate article title: "{title}".')

    return BlogArticle(
        site_id=site.id,
        slug=unique_slug(slugify(title), taken_slugs),
        title=title,
        summary=str(data.get("summary", "")).strip(),
        sections=sections,
        tags=[str(t).strip() for t in data.get("tags") or [] if str(t).strip()][:5],
        created_at=_now(),
        triggered_by=triggered_by,
    )


def write_next_article(llm: LLMProvider, store, site: BlogSite, triggered_by: str) -> BlogArticle:
    """Generate one new article for `site` and publish it to `store`.
    Retries once on a malformed/duplicate response."""
    last_error: Exception | None = None
    for _ in range(2):
        articles = store.list_articles(site.id)
        try:
            article = generate_article(
                llm,
                site,
                existing_titles=[a.title for a in reversed(articles)],
                taken_slugs={a.slug for a in articles},
                triggered_by=triggered_by,
            )
        except (BlogGenerationError, LLMError) as exc:
            last_error = exc
            continue
        store.add_article(article)
        return article
    raise BlogGenerationError(str(last_error))
