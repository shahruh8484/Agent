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
        "Write compelling, benefit-driven landing page copy for this product."
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
