"""Ad texts for a Facebook project, written by the LLM within Meta's rules
for health/supplement-adjacent products (no cure claims, no personal
attributes, no before/after, no fake urgency)."""
from __future__ import annotations

from amzagent.content.llm import LLM, LLMError, parse_json

LANGUAGES = {"uz": "Uzbek (Latin script)", "ru": "Russian"}

SYSTEM = ("You write Facebook ad copy that passes Meta's advertising policies and is honest "
          "with the reader.")


def write_ad_texts(llm: LLM, product: str, language: str, count: int = 3) -> list[dict]:
    """`count` variants: primary_text, headline, description."""
    prompt = (
        f"TASK: write facebook ads.\nLanguage: {LANGUAGES.get(language, language)}\n"
        f"Product / offer (from the owner):\n{product.strip()[:2000]}\n\n"
        f"Write {count} different ad variants. Rules (Meta policy and honesty):\n"
        "- no claims that it cures, treats or prevents a disease; no guaranteed results;\n"
        "- never assert or imply the reader's personal attributes (health, weight, age, "
        "finances), e.g. not \"Are you overweight?\" or \"Your joints hurt?\";\n"
        "- no before/after, no doctors or celebrities, no fake reviews or scarcity, "
        "no ALL CAPS, no excessive emojis (at most 2);\n"
        "- say what the product is, who it may suit, the real price/delivery terms if given.\n"
        "primary_text: 1-3 short sentences (max 250 chars); headline: max 40 chars; "
        "description: max 30 chars.\n"
        'Return a JSON array: [{"primary_text": "...", "headline": "...", '
        '"description": "..."}, ...]'
    )
    data = parse_json(llm.generate(SYSTEM, prompt, max_tokens=1500))
    if not isinstance(data, list):
        raise LLMError("Expected a JSON array of ads")
    out = []
    for item in data[:count]:
        if isinstance(item, dict) and item.get("primary_text"):
            out.append({"primary_text": str(item["primary_text"])[:500],
                        "headline": str(item.get("headline", ""))[:60],
                        "description": str(item.get("description", ""))[:60]})
    if not out:
        raise LLMError("No ad texts in the reply")
    return out
