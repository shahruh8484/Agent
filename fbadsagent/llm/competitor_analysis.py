from __future__ import annotations

import json

from fbadsagent.llm.provider import LLMProvider
from fbadsagent.models import CompetitorAd, CompetitorInsights

SYSTEM_PROMPT = (
    "You are a senior Facebook Ads media buyer. You analyze a batch of "
    "competitor ads pulled from the Meta Ad Library and extract actionable "
    "patterns. Respond with STRICT JSON only, no prose, matching this "
    'schema: {"common_hooks": [string], "common_offers": [string], '
    '"tone_observations": string, "recommended_angle": string}'
)


def analyze_competitor_ads(
    llm: LLMProvider, product_name: str, ads: list[CompetitorAd]
) -> CompetitorInsights:
    if not ads:
        return CompetitorInsights(
            tone_observations="No competitor ads found for this query.",
            recommended_angle=f"No competitor data available - lead with {product_name}'s core value proposition.",
            raw_ads_analyzed=0,
        )

    ads_block = "\n".join(
        f"- Page: {ad.page_name} | Headline: {ad.ad_creative_link_title!r} | "
        f"Body: {ad.ad_creative_body[:280]!r}"
        for ad in ads
    )
    prompt = (
        f"Product being advertised: {product_name}\n\n"
        f"Competitor ads found in the Meta Ad Library:\n{ads_block}\n\n"
        "Extract the recurring hooks, offers/discounts, overall tone, and a "
        "recommended differentiated angle for our own ad campaign."
    )

    raw = llm.generate(SYSTEM_PROMPT, prompt, max_tokens=800)
    data = _parse_json(raw)

    return CompetitorInsights(
        common_hooks=data.get("common_hooks", []),
        common_offers=data.get("common_offers", []),
        tone_observations=data.get("tone_observations", ""),
        recommended_angle=data.get("recommended_angle", ""),
        raw_ads_analyzed=len(ads),
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
