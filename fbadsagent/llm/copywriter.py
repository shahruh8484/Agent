from __future__ import annotations

import json

from fbadsagent.facebook.ads_client import new_variant_id
from fbadsagent.llm.provider import LLMProvider
from fbadsagent.models import AdCreativeCopy, CompetitorInsights, ProductInput

SYSTEM_PROMPT = (
    "You are an expert Facebook Ads direct-response copywriter. Given a "
    "product and competitor insights, write distinct high-converting ad "
    "copy variants. Respond with STRICT JSON only: a list of objects each "
    'matching {"primary_text": string, "headline": string (<=40 chars), '
    '"description": string (<=30 chars), "call_to_action": one of '
    "SHOP_NOW|LEARN_MORE|SIGN_UP|GET_OFFER|SUBSCRIBE, "
    '"image_prompt": string describing an ad creative image to generate}. '
    "No prose outside the JSON array."
)


def generate_ad_variants(
    llm: LLMProvider,
    product: ProductInput,
    insights: CompetitorInsights,
    n: int = 3,
) -> list[AdCreativeCopy]:
    prompt = (
        f"Product: {product.name}\n"
        f"Description: {product.description}\n"
        f"Price: {product.price} {product.currency}\n\n"
        f"Competitor recommended angle: {insights.recommended_angle}\n"
        f"Competitor common hooks: {', '.join(insights.common_hooks) or 'none found'}\n"
        f"Competitor common offers: {', '.join(insights.common_offers) or 'none found'}\n\n"
        f"Write {n} distinct ad copy variants that differentiate us from "
        "competitors while staying on-brand for the product. "
        f"Write all copy in {product.language}."
    )

    raw = llm.generate(SYSTEM_PROMPT, prompt, max_tokens=1200)
    variants_data = _parse_json_array(raw)

    creatives: list[AdCreativeCopy] = []
    for item in variants_data[:n] or _fallback_variants(product):
        creatives.append(
            AdCreativeCopy(
                variant_id=new_variant_id(),
                primary_text=item.get("primary_text", product.description),
                headline=item.get("headline", product.name)[:40],
                description=item.get("description", "")[:30],
                call_to_action=item.get("call_to_action", "SHOP_NOW"),
                image_prompt=item.get(
                    "image_prompt", f"Studio product photo of {product.name}"
                ),
            )
        )
    return creatives


def _fallback_variants(product: ProductInput) -> list[dict]:
    return [
        {
            "primary_text": product.description,
            "headline": product.name,
            "description": "Shop now",
            "call_to_action": "SHOP_NOW",
            "image_prompt": f"Studio product photo of {product.name}",
        }
    ]


def _parse_json_array(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = data.get("variants", [])
    return data if isinstance(data, list) else []
