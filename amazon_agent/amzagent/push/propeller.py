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

STATUS_MODERATION = 2
MAX_STATS_PAGES = 20  # x 1000 rows per page
# After HTTP 429 on statistics, don't call it again for this long (or the
# Retry-After the API sent), so the agent and the dashboard stop piling on.
RATE_LIMIT_COOLDOWN_SECONDS = 120
_stats_blocked_until: datetime | None = None
# Body key for targeting/exclude/zone (see propeller_check section 8).
ZONE_LIST_KEY = "zone"
# Campaign statuses the API reports (GET /adv/campaigns/{id})
API_STATUS_NAMES = {1: "draft", 2: "moderation", 3: "rejected", 6: "working", 7: "paused",
                    8: "stopped"}
API_STATUS_REJECTED = 3
API_STATUS_PAUSED = 7
API_STATUSES_NOT_RUNNING = (1, 3, 7, 8)  # draft, rejected, paused, stopped
TRAFFIC_CATEGORIES = ["propeller"]
ALL_HOURS = [f"{d}{h:02d}" for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
             for h in range(24)]
MIN_DAILY_AMOUNT = 10.0
# Platform targeting (targeting.os_type). The values come from the API's
# targeting reference; these paths are tried in turn, and the fallback
# values are used only when none answers.
OS_TYPE_COLLECTION_PATHS = ("/collections/targeting/os_type",
                            "/adv/collections/targeting/os_type")
PLATFORM_WORDS = {"mobile": ("mobile", "tablet", "phone"),
                  "desktop": ("desktop", "computer", "pc")}
OS_TYPE_FALLBACK = {"mobile": ["mobile"], "desktop": ["desktop"]}

# "nativeads" is the SSP direction for classic (web) push notifications.
PUSH_DIRECTION = "nativeads"
RATE_MODEL = "cpc"

# Macros PropellerAds substitutes in the target URL on every click: the
# zone (publisher placement) and the click id. Used for per-zone stats.
# ${SUBID} is substituted (seen in live traffic); ${ZONEID} arrived
# literally, so the zone uses PropellerAds' {zoneid} token instead.
ZONE_MACRO = "{zoneid}"
CLICK_MACRO = "${SUBID}"
OLD_ZONE_MACROS = ("${ZONEID}",)
URL_PATH = "/adv/campaigns/{id}/url/"


def _network_tz():
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:  # no tz database: fixed EST
        return timezone(timedelta(hours=-5))


NETWORK_TZ = _network_tz()


def _today() -> date:
    return datetime.now(timezone.utc).date()


def inline_images(payload: dict[str, Any], resolve_file) -> dict[str, Any]:
    """The API doesn't fetch creative images by URL: it wants the image
    data inline as a data URI. Stored payloads keep URLs (small, and the
    dashboard previews them); this returns the copy to send, with every
    icon/image URL that `resolve_file` maps to a local file replaced by a
    JPEG data URI."""
    import base64
    import io

    from PIL import Image

    def encode(url: str) -> str:
        path = resolve_file(url)
        if path is None:
            raise PropellerError(f"creative image not found locally: {url}")
        img = Image.open(path).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    out = dict(payload)
    out["creatives"] = [
        {**c, "icon": encode(c["icon"]), "image": encode(c["image"])}
        for c in payload.get("creatives", [])
    ]
    return out


def _first(row: dict, *keys: str):
    for key in keys:
        if row.get(key) not in (None, ""):
            return row[key]
    return None


class PropellerError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _retry_after(resp: requests.Response) -> int:
    try:
        return min(max(int(getattr(resp, "headers", {}).get("Retry-After", "")), 1), 900)
    except ValueError:
        return RATE_LIMIT_COOLDOWN_SECONDS


def stats_rate_limited() -> bool:
    """True while statistics calls are held back after an HTTP 429."""
    return _stats_blocked_until is not None and datetime.now(timezone.utc) < _stats_blocked_until


def match_os_types(data: Any, platform: str) -> list:
    """Values of the os_type reference entries that belong to `platform`.
    Entries may be {"value"/"id": ..., "title"/"name"/"text": ...} or strings,
    possibly wrapped in "result"/"data"/"items"."""
    words = PLATFORM_WORDS.get(platform, ())
    if isinstance(data, dict):
        for key in ("result", "data", "items", "list"):
            if isinstance(data.get(key), (list, dict)):
                return match_os_types(data[key], platform)
        entries = [{"value": k, "title": v} for k, v in data.items()]
    elif isinstance(data, list):
        entries = data
    else:
        return []
    out = []
    for e in entries:
        if isinstance(e, dict):
            value = next((e[k] for k in ("value", "id", "code", "key") if k in e), None)
            text = " ".join(str(e.get(k, "")) for k in ("title", "name", "text", "label"))
            text = f"{text} {value}".lower()
        else:
            value, text = e, str(e).lower()
        if value is not None and any(w in text for w in words):
            out.append(value)
    return out


def build_campaign_payload(
    name: str,
    target_url: str,
    title: str,
    text: str,
    images: list[tuple[str, str]],
    countries: list[str],
    bid_cpc: float,
    daily_budget: float,
    os_types: list | None = None,
) -> dict[str, Any]:
    payload = {
        "name": name[:100],
        "direction": PUSH_DIRECTION,
        "rate_model": RATE_MODEL,
        "target_url": target_url,
        # 1 = draft, 2 = submit for moderation; approved campaigns start on
        # their own, so no separate "play" call is needed.
        "status": STATUS_MODERATION,
        "started_at": _today().strftime("%d/%m/%Y"),
        "expired_at": (_today() + timedelta(days=365)).strftime("%d/%m/%Y"),
        # Required for push CPC; the API's minimum is $10.
        "daily_amount": round(max(daily_budget, MIN_DAILY_AMOUNT), 2),
        "timezone": 0,
        "targeting": {
            "country": {"list": countries, "is_excluded": False},
            # Required, as included hours "Mon00".."Sun23": all 168 = 24/7.
            "time_table": {"list": ALL_HOURS, "is_excluded": False},
            # Required in practice (the API rejects a body without it);
            # "propeller" = the network's own publisher traffic.
            "traffic_categories": TRAFFIC_CATEGORIES,
            # Required non-empty: all subscriber activity levels.
            "user_activity": {"list": [1, 2, 3], "is_excluded": False},
        },
        "rates": [{"countries": countries, "amount": round(bid_cpc, 4)}],
        # One creative per image variant (same copy): the network rotates
        # them and shows the better performer more.
        "creatives": [
            {
                "title": title[:30],
                "description": text[:60],
                "status": 1,  # 1 = active; the API refuses all-disabled creatives
                "icon": icon_url,
                "image": image_url,
            }
            for icon_url, image_url in images
        ],
    }
    if os_types:  # only phones/tablets or only computers
        payload["targeting"]["os_type"] = {"list": list(os_types), "is_excluded": False}
    return payload


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
        global _stats_blocked_until
        if path == STATISTICS_PATH and stats_rate_limited():
            raise PropellerError(
                "PropellerAds rate limit: statistics paused until "
                f"{_stats_blocked_until:%H:%M:%S} UTC", status=429)
        try:
            resp = self._session.request(
                method, self._base + path, timeout=self._timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise PropellerError(f"{method} {path} failed: {exc}") from exc
        if resp.status_code == 429 and path == STATISTICS_PATH:
            _stats_blocked_until = datetime.now(timezone.utc) + timedelta(
                seconds=_retry_after(resp))
        if resp.status_code >= 400:
            raise PropellerError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:500]}",
                                 status=resp.status_code)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    def os_type_values(self, platform: str) -> list:
        """os_type values for "mobile" or "desktop" from the API reference."""
        errors = []
        for path in OS_TYPE_COLLECTION_PATHS:
            try:
                data = self._request("GET", path)
            except PropellerError as exc:
                errors.append(str(exc))
                continue
            values = match_os_types(data, platform)
            if values:
                return values
            errors.append(f"GET {path}: no {platform} entry in {str(data)[:300]}")
        raise PropellerError("; ".join(errors))

    def create_campaign(self, payload: dict[str, Any]) -> str:
        data = self._request("POST", CAMPAIGNS_PATH, json=payload)
        # Responses wrap objects in "result" (seen on GET /adv/campaigns).
        inner = data.get("result") or data.get("data") or {}
        campaign_id = data.get("id") or (inner.get("id") if isinstance(inner, dict) else None)
        if not campaign_id:
            raise PropellerError(f"No campaign id in response: {data}")
        return str(campaign_id)

    def start(self, campaign_ids: list[str]) -> None:
        self._request("PUT", PLAY_PATH, json={"campaign_ids": [int(i) for i in campaign_ids]})

    def stop(self, campaign_ids: list[str]) -> None:
        self._request("PUT", STOP_PATH, json={"campaign_ids": [int(i) for i in campaign_ids]})

    def exclude_zones(self, campaign_id: str, zones: list[str]) -> None:
        """Add zones to the campaign's exclude list (PATCH appends)."""
        self._request(
            "PATCH", EXCLUDE_ZONES_PATH.format(id=campaign_id),
            json={ZONE_LIST_KEY: [int(z) for z in zones]},
        )

    def set_excluded_zones(self, campaign_id: str, zones: list[str]) -> None:
        """Replace the whole exclude list (PUT), e.g. to re-enable a zone."""
        self._request(
            "PUT", EXCLUDE_ZONES_PATH.format(id=campaign_id),
            json={ZONE_LIST_KEY: [int(z) for z in zones]},
        )

    def update_target_url(self, campaign_id: str, url: str) -> None:
        self._request("PUT", URL_PATH.format(id=campaign_id), json={"target_url": url})

    def update_campaign(self, campaign_id: str, fields: dict[str, Any]) -> None:
        self._request("PATCH", f"{CAMPAIGNS_PATH}/{campaign_id}", json=fields)

    def campaign_status(self, campaign_id: str) -> int | None:
        data = self._request("GET", f"{CAMPAIGNS_PATH}/{campaign_id}")
        inner = data.get("result", data) if isinstance(data, dict) else {}
        try:
            return int(inner.get("status"))
        except (TypeError, ValueError, AttributeError):
            return None

    def balance(self) -> float | None:
        data = self._request("GET", BALANCE_PATH)
        value = data if isinstance(data, (int, float)) else data.get("balance", data.get("data"))
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def spend(self, campaign_ids: list[str], days: int = 30, by_zone: bool = False) -> list[dict]:
        """Rows of {"campaign_id", "zone_id", "impressions", "clicks",
        "spent"} for the last `days` (zone_id is "" unless by_zone)."""
        return self._stats(
            campaign_ids,
            f"{_today() - timedelta(days=days)} 00:00:00",
            f"{_today() + timedelta(days=1)} 23:59:59",
            by_zone,
        )

    def spend_last_hours(self, campaign_ids: list[str], hours: int = 24) -> list[dict]:
        """Spend over a rolling window ending now. The API takes times in
        its own zone (US Eastern), so the window is computed there."""
        now = datetime.now(NETWORK_TZ)
        return self._stats(
            campaign_ids,
            (now - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S"),
            (now + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
            by_zone=False,
        )

    def stats_between(self, campaign_ids: list[str], start: datetime, end: datetime,
                      by_zone: bool = False) -> list[dict]:
        """Stats for [start, end) given as aware datetimes (converted to the
        network's US Eastern time the API expects)."""
        fmt = "%Y-%m-%d %H:%M:%S"
        return self._stats(
            campaign_ids,
            start.astimezone(NETWORK_TZ).strftime(fmt),
            (end.astimezone(NETWORK_TZ) - timedelta(seconds=1)).strftime(fmt),
            by_zone,
        )

    def _stats(self, campaign_ids: list[str], day_from: str, day_to: str,
               by_zone: bool) -> list[dict]:
        if not campaign_ids:
            return []
        group_by = ["campaign_id", "zone_id"] if by_zone else ["campaign_id"]
        params = {
            "day_from": day_from,
            "day_to": day_to,
            # No "tz": the API only accepts it for ranges of up to a week;
            # without it times are in the network's US Eastern zone.
            "group_by[]": group_by,
            "campaign_id[]": [int(i) for i in campaign_ids],
            "per_page": 1000,
        }
        rows: list = []
        for page in range(1, MAX_STATS_PAGES + 1):
            data = self._request("GET", STATISTICS_PATH, params={**params, "page": page})
            if isinstance(data, dict):
                chunk = data.get("items", data.get("result", data.get("data", [])))
                pages = int(data.get("total_pages") or 1)
            else:
                chunk, pages = data, 1
            rows.extend(chunk if isinstance(chunk, list) else [])
            if page >= pages:
                break
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            spent = _first(row, "spent", "money", "cost")
            out.append(
                {
                    "campaign_id": str(row.get("campaign_id", "")),
                    "zone_id": str(row.get("zone_id", "")) if by_zone else "",
                    "impressions": int(float(_first(row, "impressions", "shows") or 0)),
                    "clicks": int(float(_first(row, "clicks") or 0)),
                    "spent": float(spent or 0),
                }
            )
        return out