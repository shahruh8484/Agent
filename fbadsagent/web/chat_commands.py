"""Lets the Chat page double as a command line for the autonomous agent:
"launch 2 campaigns for Glycofort at $5/day" triggers real
run_agent_for_product() calls instead of just talking about it.

Parsing is LLM-based (free-form phrasing, typos, any language) rather than
regex — a strict JSON system prompt keeps it a plain classifier, not
something that can be prompt-injected into taking other actions.
"""
from __future__ import annotations

import json

from fbadsagent.llm.provider import LLMProvider

MAX_CAMPAIGNS_PER_COMMAND = 5

LAUNCH_COMMAND_SYSTEM_PROMPT = (
    "You detect whether a chat message is a request to launch Facebook ad "
    "campaign(s) for an already-configured product/offer, as opposed to a "
    "normal question or conversation. Respond with STRICT JSON only "
    'matching {"is_launch_command": boolean, "product_name": string, '
    '"count": integer, "daily_budget": number or null}. product_name is '
    "the offer/product name mentioned, as close to verbatim as possible. "
    "count is how many separate campaigns to launch (default 1 if "
    "unspecified). daily_budget is the daily budget in dollars if "
    "mentioned, otherwise null. If the message is not a launch request, "
    'respond with {"is_launch_command": false, "product_name": "", '
    '"count": 1, "daily_budget": null}. No prose outside the JSON.'
)


class LaunchCommand:
    def __init__(self, product_name: str, count: int, daily_budget: float | None):
        self.product_name = product_name
        self.count = max(1, min(count, MAX_CAMPAIGNS_PER_COMMAND))
        self.daily_budget = daily_budget


def parse_launch_command(llm: LLMProvider, message: str) -> LaunchCommand | None:
    """None if the message isn't a launch request, or couldn't be parsed —
    callers should fall back to the normal chat reply in that case."""
    raw = llm.generate(LAUNCH_COMMAND_SYSTEM_PROMPT, message, max_tokens=200)
    try:
        data = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError:
        return None

    if not data.get("is_launch_command") or not data.get("product_name"):
        return None

    return LaunchCommand(
        product_name=str(data["product_name"]),
        count=int(data.get("count") or 1),
        daily_budget=data.get("daily_budget"),
    )


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return text
