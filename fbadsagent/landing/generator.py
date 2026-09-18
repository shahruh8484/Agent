from __future__ import annotations

import json
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from fbadsagent.config import Settings
from fbadsagent.llm.provider import LLMProvider
from fbadsagent.models import CompetitorInsights, LandingPage, ProductInput

_TEMPLATE_DIR = Path(__file__).parent / "templates"

SYSTEM_PROMPT = (
    "You are a conversion copywriter. Given a product and the recommended "
    "marketing angle, write landing page content. Respond with STRICT JSON "
    'only matching {"headline": string, "subheadline": string, '
    '"benefits": [string, string, string, string], "cta_text": string}. '
    "No prose outside the JSON."
)


def generate_landing_copy(
    llm: LLMProvider,
    product: ProductInput,
    insights: CompetitorInsights,
    reference_texts: list[str] | None = None,
) -> dict:
    """LLM-written landing page copy, reused by both the CLI's static-file
    generator below and the web agent's published-landing-page flow.

    reference_texts, when given, is the visible text scraped from
    competitor landing pages the caller wants used as a style/structure
    reference — not to copy, but to match how the market already frames
    this kind of offer.
    """
    prompt = (
        f"Product: {product.name}\n"
        f"Description: {product.description}\n"
        f"Price: {product.price} {product.currency}\n"
        f"Recommended angle: {insights.recommended_angle}\n\n"
        "Write compelling, benefit-driven landing page copy for this product. "
        f"Write all copy in {product.language}."
    )
    if reference_texts:
        examples = "\n\n".join(
            f"--- Competitor landing page {i + 1} ---\n{text}"
            for i, text in enumerate(reference_texts)
        )
        prompt += (
            "\n\nHere is the visible text from competitor landing pages for "
            "similar products. Use them as a reference for structure, offer "
            "framing and tone — do not copy their wording, write original "
            "copy for this product:\n\n" + examples
        )
    raw = llm.generate(SYSTEM_PROMPT, prompt, max_tokens=800)
    data = _parse_json(raw)

    return {
        "headline": data.get("headline") or product.name,
        "subheadline": data.get("subheadline") or product.description,
        "benefits": data.get("benefits") or [product.description],
        "cta_text": data.get("cta_text") or "Buy Now",
    }


QUIZ_SYSTEM_PROMPT = (
    "You are a conversion copywriter building an interactive quiz-style "
    "landing page: a short sequence of lifestyle/preference questions with "
    "button-style answer options, ending in a personalized product "
    "recommendation screen.\n\n"
    "Hard rules, no exceptions:\n"
    "- Never invent a doctor, expert, clinic, institute, or any other "
    "persona or authority the product doesn't actually have.\n"
    "- Never invent statistics, study results, percentages, or patient/"
    "customer counts. Only use benefits explicitly given in the product "
    "description.\n"
    "- Never claim the product cures, treats, or eliminates a medical "
    "condition, or diagnose the reader based on their answers. If the "
    "product is health-adjacent, describe it only as support/lifestyle "
    "aid, matching how it's actually described, never as a cure.\n"
    "- Never fabricate social proof (e.g. 'X just ordered this') or fake "
    "urgency/scarcity ('only N left') not stated by the caller.\n"
    "- Questions should be genuinely engaging and relevant to why someone "
    "would want this product (habits, goals, preferences) — not a fake "
    "medical intake form.\n\n"
    "Respond with STRICT JSON only matching "
    '{"headline": string, "subheadline": string, '
    '"quiz_questions": [{"text": string, "options": [string, ...]}, ...] '
    "(3-5 questions, 2-4 options each), "
    '"quiz_result_message": string (a short, honest, benefit-driven pitch '
    "for the product show after the quiz, referencing the product's real "
    'described benefits only), "cta_text": string}. No prose outside the '
    "JSON."
)


def generate_quiz_landing_copy(
    llm: LLMProvider,
    product: ProductInput,
    insights: CompetitorInsights,
    reference_texts: list[str] | None = None,
) -> dict:
    """Like generate_landing_copy, but for the interactive quiz-style page
    (fbadsagent/web/templates/landing_quiz.html) — a question-by-question
    funnel instead of a single static page. See QUIZ_SYSTEM_PROMPT for the
    honesty constraints this enforces regardless of what reference_texts
    contains.
    """
    prompt = (
        f"Product: {product.name}\n"
        f"Description: {product.description}\n"
        f"Price: {product.price} {product.currency}\n"
        f"Recommended angle: {insights.recommended_angle}\n\n"
        "Write the quiz questions, options, result message and CTA for "
        "this product. "
        f"Write all copy in {product.language}."
    )
    if reference_texts:
        examples = "\n\n".join(
            f"--- Reference funnel {i + 1} ---\n{text}"
            for i, text in enumerate(reference_texts)
        )
        prompt += (
            "\n\nHere is a reference for the funnel's general structure and "
            "pacing (question style, tone, how it builds to the offer). Do "
            "not copy any claims, statistics, personas, or wording from it "
            "— only its structure, and only where it doesn't conflict with "
            "the hard rules in your system prompt:\n\n" + examples
        )
    raw = llm.generate(QUIZ_SYSTEM_PROMPT, prompt, max_tokens=1200)
    data = _parse_json(raw)

    questions = [
        {"text": q.get("text", ""), "options": q.get("options") or []}
        for q in (data.get("quiz_questions") or [])
        if q.get("text")
    ]

    return {
        "headline": data.get("headline") or product.name,
        "subheadline": data.get("subheadline") or product.description,
        "quiz_questions": questions,
        "quiz_result_message": data.get("quiz_result_message") or product.description,
        "cta_text": data.get("cta_text") or "Get Started",
    }


def generate_landing_page(
    llm: LLMProvider,
    settings: Settings,
    product: ProductInput,
    insights: CompetitorInsights,
    reference_texts: list[str] | None = None,
) -> LandingPage:
    copy = generate_landing_copy(llm, product, insights, reference_texts)
    headline = copy["headline"]
    subheadline = copy["subheadline"]
    benefits = copy["benefits"]
    cta_text = copy["cta_text"]

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("landing_base.html.j2")
    html = template.render(
        product_name=product.name,
        headline=headline,
        subheadline=subheadline,
        benefits=benefits,
        cta_text=cta_text,
        price=product.price,
        currency=product.currency,
        checkout_url=product.landing_url or "#",
    )

    out_dir = Path(settings.output_dir) / "landing_pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", product.name.lower()).strip("-") or "landing"
    html_path = out_dir / f"{slug}.html"
    html_path.write_text(html, encoding="utf-8")

    return LandingPage(
        product_name=product.name,
        html_path=str(html_path),
        headline=headline,
        subheadline=subheadline,
        benefits=benefits,
        cta_text=cta_text,
    )


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}
