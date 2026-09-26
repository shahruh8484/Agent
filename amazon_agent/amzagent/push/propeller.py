"""PropellerAds SSP API v5 client for push-notification campaigns.

Auth: a bearer token from the SSP dashboard (Profile -> API token).
PropellerAds only enables API access for advertisers with >= $1000 total
spend/deposit — request it from the Profile tab.

Official reference (Swagger): https://ssp-api.propellerads.com/v5/docs/
Every path and payload field this module sends is defined in the block
of constants and in `build_campaign_payload` below, so if PropellerAds
renames something, it is a one-place fix. Run with PUSH_LIVE=false first:
the agent then only stores the payloads it *would* send, and the
dashboard shows them for review.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://ssp-api.propellerads.com/v5"
CAMPAIGNS_PATH = "/adv/campaigns"
PLAY_PATH = "/adv/campaigns/play"
STOP_PATH = "/adv/campaigns/stop"
STATISTICS_PATH = "/adv/statistics"
EXCLUDE_ZONES_PATH = "/adv/campaigns/{id}/targeting/exclude/zone"
BALANCE_PATH = "/adv/balance"

# "nativeads" is the SSP direction for classic (web) push notifications.
PUSH_DIRECTION = "nativeads"
RATE_MODEL = "cpc"

# Macros PropellerAds substitutes in the target URL on every click: the
# zone (publisher placement) and the click id. Used for per-zone stats.
ZONE_MACRO = "${ZONEID}"
CLICK_MACRO = "${SUBID}"


def _today() -> date:
    return datetime.now(timezone.utc).date()


class PropellerError(RuntimeError):
    pass


def build_campaign_payload(
    name: str,
    target_url: str,
    title: str,
    text: str,
    images: list[tuple[str, str]],
    countries: list[str],
    bid_cpc: float,
    daily_budget: float,
) -> dict[str, Any]:
    return {
        "name": name[:100],
        "direction": PUSH_DIRECTION,
        "rate_model": RATE_MODEL,
        "target_url": target_url,
        "status": 1,
        "started_at": _today().strftime("%d/%m/%Y"),
        "expired_at": (_today() + timedelta(days=365)).strftime("%d/%m/%Y"),
        "daily_amount": round(daily_budget, 2),
        # Spec: integer 0/1 (not a boolean); spread the daily budget evenly.
        "evenly_limits_usage": 1,
        "frequency": 1,
        "capping": 86400,
        "timezone": 0,
        "targeting": {
            "country": {"list": countries, "is_excluded": False},
        },
        "rates": [{"countries": countries, "amount": round(bid_cpc, 4)}],
        # One creative per image variant (same copy): the network rotates
        # them and shows the better performer more.
        "creatives": [
            {
                "title": title[:30],
                "description": text[:60],
                "icon": icon_url,
                "image": image_url,
            }
            for icon_url, image_url in images
        ],
    }


class PropellerClient:
    def __init__(self, api_token: str, base_url: str = BASE_URL, timeout: int = 30):
        if not api_token:
            raise PropellerError("PROPELLER_API_TOKEN is not set.")
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            resp = self._session.request(
                method, self._base + path, timeout=self._timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise PropellerError(f"{method} {path} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise PropellerError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:500]}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    def create_campaign(self, payload: dict[str, Any]) -> str:
        data = self._request("POST", CAMPAIGNS_PATH, json=payload)
        campaign_id = data.get("id") or (data.get("data") or {}).get("id")
        if not campaign_id:
            raise PropellerError(f"No campaign id in response: {data}")
        return str(campaign_id)

    def start(self, campaign_ids: list[str]) -> None:
        self._request("PUT", PLAY_PATH, json={"campaign_ids": [int(i) for i in campaign_ids]})

    def stop(self, campaign_ids: list[str]) -> None:
        self._request("PUT", STOP_PATH, json={"campaign_ids": [int(i) for i in campaign_ids]})

    def exclude_zones(self, campaign_id: str, zones: list[str]) -> None:
        self._request(
            "PATCH", EXCLUDE_ZONES_PATH.format(id=campaign_id), json={"zone": zones}
        )

    def balance(self) -> float | None:
        data = self._request("GET", BALANCE_PATH)
        value = data if isinstance(data, (int, float)) else data.get("balance", data.get("data"))
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def spend(self, campaign_ids: list[str], days: int = 30, by_zone: bool = False) -> list[dict]:
        """Rows of {"campaign_id", "zone_id"?, "spent"} for the last `days`."""
        if not campaign_ids:
            return []
        group_by = ["campaign_id", "zone_id"] if by_zone else ["campaign_id"]
        params = {
            "day_from": f"{_today() - timedelta(days=days)} 00:00:00",
            "day_to": f"{_today()} 23:59:59",
            "tz": "+0000",
            "group_by[]": group_by,
            "campaign_id[]": [int(i) for i in campaign_ids],
        }
        data = self._request("GET", STATISTICS_PATH, params=params)
        rows = data.get("data", data) if isinstance(data, dict) else data
        out = []
        for row in rows or []:
            spent = row.get("spent", row.get("money", row.get("cost", 0)))
            out.append(
                {
                    "campaign_id": str(row.get("campaign_id", "")),
                    "zone_id": str(row.get("zone_id", "")) if by_zone else "",
                    "spent": float(spent or 0),
                }
            )
        return out
