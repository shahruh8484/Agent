"""Free plan vs FamilyChef Plus.

FC_LIMITS=1 turns the limits on (off by default until in-app purchase is available).
Free plan: a menu for FC_FREE_DAYS day(s) (default 1), once every FC_FREE_PERIOD_DAYS days
(default 7). Everything else — the full week, replacing dishes, another variant, your own
store prices, menu history — is Plus.
"""
from __future__ import annotations

import os
import time

from familychef.models import PlanRequest


def limits_on() -> bool:
    return os.environ.get("FC_LIMITS", "").strip() in ("1", "true", "yes")


def free_days() -> int:
    return max(1, int(os.environ.get("FC_FREE_DAYS", "1") or 1))


def free_period_days() -> int:
    return max(1, int(os.environ.get("FC_FREE_PERIOD_DAYS", "7") or 7))


class LimitError(Exception):
    def __init__(self, code: str, **extra):
        super().__init__(code)
        self.code = code
        self.extra = extra


def entitlements(user: dict | None, last_free_at: int | None) -> dict:
    """What this person may do; sent to the page so it can show the right buttons."""
    plus = bool(user and user.get("plus"))
    unlimited = plus or not limits_on()
    next_free = None
    if not unlimited and last_free_at:
        nxt = last_free_at + free_period_days() * 86400
        next_free = nxt if nxt > time.time() else None
    return {
        "limits": limits_on(),
        "plus": plus,
        "plus_until": (user or {}).get("plus_until") or 0,
        "unlimited": unlimited,
        "max_days": 7 if unlimited else free_days(),
        "free_days": free_days(),
        "free_period_days": free_period_days(),
        "next_free_at": next_free,
    }


def check(req: PlanRequest, ent: dict) -> PlanRequest:
    """Raise LimitError when a free user asks for a Plus feature; return the request to run."""
    if ent["unlimited"]:
        return req
    if req.days > ent["free_days"]:
        raise LimitError("plus_required", feature="week")
    if req.fixed or req.avoid:
        raise LimitError("plus_required", feature="replace")
    if req.seed:
        raise LimitError("plus_required", feature="another")
    if ent["next_free_at"]:
        raise LimitError("free_used", next_free_at=ent["next_free_at"])
    # Own store prices are Plus: a free menu uses the regional prices.
    return req.model_copy(update={"price_overrides": {}})
