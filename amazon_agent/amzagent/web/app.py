"""FastAPI app: the public affiliate sites and the admin dashboard.

Public (no login):
  /s/{slug}/                 site home — the niche's selected products
  /s/{slug}/p/{asin}         product page (push ads land here)
  /about /contact /privacy /terms /affiliate-disclosure
                             site-wide pages Amazon Associates reviewers expect
  /robots.txt /sitemap.xml /favicon.svg
  /go/{slug}/{asin}          logs a click, redirects to Amazon
  /media/{slug}/{file}       push creative images

Admin (login): /, /login, /logout and the POST actions below.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import bcrypt
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from amzagent.agent.runner import (
    committed_24h,
    CAPPED,
    NEXT_CYCLE_FLAG,
    resume_eta,
    MANUAL_FLAG,
    is_manual,
    launch_product,
    change_settings,
    KILLED,
    PAUSE_FLAG,
    VISITS_PER_PAID_CLICK,
    build_deps,
    exclude_zone,
    add_whitelist_zones,
    include_zone,
    launch_whitelist,
    quick_check,
    redraw_campaign,
    resume_campaign,
    run_cycle,
    spent_today,
    stop_all,
    stop_campaign,
    sync_moderation,
    sync_stats,
)
from amzagent.agent.advice import ADVICE_PREFIX
from amzagent.agent.igaming import is_ig
from amzagent.amazon.creator_connections import (
    marketplace_host,
    parse_opportunities,
    parse_opportunity_details,
)
from amzagent.config import Settings, get_settings
from amzagent.models import Product
from amzagent.panel_settings import effective, load_overrides, parse_form
from amzagent.push.propeller import PropellerClient, PropellerError
from amzagent.store import ACTIVE, STOPPED, Store
from amzagent.content.sections import OTHER as SECTIONS_OTHER
from amzagent.models import SiteSection
from amzagent.web.chat import ChatAgent
from amzagent.web.fb_routes import register_fb_routes
from amzagent.web.ig_routes import register_ig_routes, norm_domain
from amzagent.web.period import PRESETS, Period, parse_period

logger = logging.getLogger(__name__)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
SITE_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "site" / "templates")
)
SITE_TEMPLATES.env.filters["hue"] = lambda text: int(
    hashlib.md5((text or "").encode()).hexdigest(), 16
) % 360  # stable per-brand color for image placeholders
PRICE_MAX_AGE = timedelta(hours=24)
# "Last updated" on the legal pages: change it when their text changes.
LEGAL_PAGES_UPDATED = "September 26, 2026"
CONTACT_HOURLY_LIMIT = 20
# Flash messages that report a failure ("… не создан: …") are shown in red.
FLASH_FAILED_RE = re.compile(
    r"\bне (найдено|возвращена|сохранены|создан|удалось|запущена|запущен|добавлены)\b")
# Shown instead of the redirect when a click looks automated. The button is a
# plain Amazon link (no tag); in a real browser the script swaps in our
# tagged link, so people still get through with one more tap.
CONTINUE_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Continue to Amazon</title><style>body{{font-family:system-ui,sans-serif;background:#f6f5f2;
margin:0;display:grid;place-items:center;min-height:100vh}}.box{{background:#fff;border:1px solid #e3e0d8;
border-radius:14px;padding:28px;max-width:420px;margin:16px;text-align:center}}a.btn{{display:block;
background:#f0a020;color:#111;font-weight:700;padding:14px;border-radius:10px;text-decoration:none;
margin-top:18px}}p{{color:#555}}</style></head><body><div class="box"><h1 style="font-size:1.2rem">{title}</h1>
<p>You are leaving our site for Amazon.</p><a class="btn" id="go" href="{plain}" rel="nofollow noopener">
Continue to Amazon &rarr;</a></div><script>(function(){{var r="{retry}";if(r&&!navigator.webdriver)
document.getElementById("go").href=r+"&js=1";}})();</script></body></html>"""
CHAT_SEEN_FLAG = "chat_seen_id"
MAX_TIME_ON_PAGE = 1800  # seconds; longer reports are capped (tab left open)
STATS_INTERVAL_SECONDS = 3 * 60  # site-wide, keeps a spam bot from flooding the inbox
SAFE_PARAM = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def price_is_fresh(product: Product) -> bool:
    """Amazon only allows showing a price fetched within the last 24h."""
    if product.price is None or not product.fetched_at:
        return False
    try:
        fetched = datetime.fromisoformat(product.fetched_at)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - fetched < PRICE_MAX_AGE


MOBILE_UA = re.compile(r"Mobi|Android|iPhone|iPad|iPod|Silk|Kindle|Opera Mini|IEMobile|"
                       r"BlackBerry|webOS", re.IGNORECASE)


def device_of(request: Request) -> str:
    """"mobile" (phones and tablets) or "desktop", from the User-Agent."""
    ua = request.headers.get("user-agent", "")
    # iPadOS reports a desktop Mac user agent; its touch support gives it away
    # only in JS, so an iPad may count as desktop.
    return "mobile" if MOBILE_UA.search(ua) else "desktop"


# --- bot filter on the way to Amazon ------------------------------------
# Clicks that look automated are not sent to Amazon with our tag (Amazon
# pays only for qualified clicks and may act on invalid ones). A person who
# trips a soft check gets a "Continue to Amazon" page instead.
BOT_UA = re.compile(
    r"bot|crawl|spider|slurp|headless|phantom|puppeteer|playwright|selenium|webdriver|"
    r"curl|wget|python|httpclient|okhttp|java/|go-http|libwww|scrapy|axios|node-fetch|"
    r"facebookexternalhit|preview|lighthouse|pingdom|uptime|monitor", re.IGNORECASE)
FAST_CLICK_SECONDS = 2  # clicked sooner after the page opened than a person can
IP_CLICKS_PER_HOUR = 5  # more clicks to Amazon than this from one visitor per hour
HARD_BOT_REASONS = {"bot-ua", "webdriver", "repeat-ip"}  # never passed on
BOT_REASON_NAMES = {"bot-ua": "бот по User-Agent", "webdriver": "автоматический браузер",
                    "no-js": "без JavaScript", "fast": "клик быстрее 2 с",
                    "repeat-ip": "много кликов с одного адреса"}


def client_ip(request: Request) -> str:
    """The visitor's address: Caddy appends it as the last X-Forwarded-For entry."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else ""


# Where a visitor who didn't come from our ads found the site (Referer host).
REFERRER_SOURCES = (
    ("google.", "Google"), ("bing.", "Bing"), ("duckduckgo.", "DuckDuckGo"),
    ("yahoo.", "Yahoo"), ("yandex.", "Yandex"), ("baidu.", "Baidu"),
    ("ecosia.", "Ecosia"), ("brave.", "Brave Search"), ("chatgpt.", "ChatGPT"),
    ("perplexity.", "Perplexity"), ("facebook.", "Facebook"), ("fb.", "Facebook"),
    ("instagram.", "Instagram"), ("t.co", "X / Twitter"), ("x.com", "X / Twitter"),
    ("twitter.", "X / Twitter"), ("reddit.", "Reddit"), ("pinterest.", "Pinterest"),
    ("youtube.", "YouTube"), ("tiktok.", "TikTok"), ("quora.", "Quora"),
)


def referrer_source(referrer: str, own_host: str) -> tuple[str, bool]:
    """(source name, whether this view is an entry from outside the site)."""
    host = urlparse(referrer).hostname or "" if referrer else ""
    host = host.lower().removeprefix("www.")
    own = own_host.lower().removeprefix("www.")
    if not host:
        return "Прямой заход", True
    if own and (host == own or host.endswith("." + own)):
        return "внутри сайта", False
    for needle, name in REFERRER_SOURCES:
        # "google." matches a whole label (www.google.co.uk); "t.co" the domain
        if (host.startswith(needle) or f".{needle}" in host if needle.endswith(".")
                else host == needle or host.endswith(f".{needle}")):
            return name, True
    return host[:60], True


def _clean(value: str | None) -> str | None:
    return value if value and SAFE_PARAM.match(value) else None


STATUS_ORDER = {"active": 0, "paced": 1, "capped": 1, "dry_run": 2, "creating": 3, "error": 4, "killed": 5,
                "stopped": 6}


def _ratio(num: float, den: float) -> float | None:
    return num / den if den else None


DEVICES = ("mobile", "desktop")


def _device_split(visits: dict[str, int], clicks: dict[str, int]) -> dict:
    """{"mobile": {"visits", "amazon", "rate"}, "desktop": {...}} or {} if no
    visit recorded a device yet."""
    if not any(visits.get(d) for d in DEVICES):
        return {}
    return {d: {"visits": visits.get(d, 0), "amazon": clicks.get(d, 0),
                "rate": _ratio(clicks.get(d, 0), visits.get(d, 0))} for d in DEVICES}


def _campaign_rows(store: Store, bid: float = 0.0, period: Period | None = None,
                   net: dict | None = None, net_zones: dict | None = None) -> list[dict]:
    """Campaigns with ad-network and on-site stats, and their zones, for the
    dashboard: running first, then by spend. `spend_est` is the real-time
    estimate from site visits (x bid) the agent also judges on, since the
    network's reported spend lags.

    With a `period`, network numbers come from `net` / `net_zones` (fetched
    for that window: {external_id: row} / {external_id: [rows]}) and site
    numbers from events inside the window."""
    niches = {n.id: n for n in store.list_niches()}
    slugs = {nid: n.slug for nid, n in niches.items()}
    since = period.since if period else None
    until = period.until if period else None
    rows = []
    for c in store.list_campaigns()[:300]:
        if is_ig(c):
            continue  # shown on the iGaming tab
        found = store.get_product(c["niche_id"], c["asin"])
        niche = niches.get(c["niche_id"])
        # Creator Connections' "Estimated EPC: up to $X" = the most a click
        # to Amazon can earn; revenue/profit below are that upper bound.
        epc = (found[0].epc if found else None) or (niche.asins.get(c["asin"]) if niche else None)
        visits = store.count_events(c["id"], "visit", since, until)
        times = store.time_on_site(c["id"], since, until)
        amazon = store.count_events(c["id"], "click", since, until)
        bot_reasons = store.bot_reasons(c["id"], since, until)
        if since is not None:
            n = (net or {}).get(c["external_id"] or "", {})
            c.update(impressions=n.get("impressions", 0), ad_clicks=n.get("clicks", 0),
                     spend=n.get("spent", 0.0))
        c.update(
            slug=slugs.get(c["niche_id"], "?"),
            title=found[0].title if found else c["asin"],
            visits=visits,
            clicks=amazon,
            ctr=_ratio(c["ad_clicks"], c["impressions"]),
            cpc=_ratio(c["spend"], c["ad_clicks"]),
            to_amazon=_ratio(amazon, visits),
            spend_est=(visits * bid / VISITS_PER_PAID_CLICK
                       if since is None and c["status"] == ACTIVE and c["external_id"] else 0.0),
            cost_per_click=_ratio(c["spend"], amazon),
            devices=_device_split(store.events_by_device(c["id"], "visit", since, until),
                                  store.events_by_device(c["id"], "click", since, until)),
            epc=epc,
            revenue=amazon * epc if epc else None,
            profit=amazon * epc - c["spend"] if epc else None,
            images=_payload_images(c["payload"]),
            zones=_zone_rows(store, c["id"], since, until,
                             (net_zones or {}).get(c["external_id"] or "", [])
                             if since is not None else None, epc, times),
            time=times.get(None),
            bots=store.count_events(c["id"], "bot", since, until),
            bot_reasons=", ".join(f"{BOT_REASON_NAMES.get(k, k)}: {n}"
                                  for k, n in sorted(bot_reasons.items(), key=lambda x: -x[1])),
        )
        rows.append(c)
    rows.sort(key=lambda c: (STATUS_ORDER.get(c["status"], 9), -c["spend"], -c["id"]))
    # For each whitelist: zones that sent people to Amazon in the product's
    # other campaigns and aren't in it yet — candidates to add.
    for c in rows:
        if not c.get("zones_only"):
            continue
        have = set(c["zones_only"].split(","))
        found: dict[str, list[int]] = {}
        for other in rows:
            if other["asin"] != c["asin"] or other["id"] == c["id"]:
                continue
            for z in other["zones"]:
                if z["amazon"] and z["zone"] not in have:
                    acc = found.setdefault(z["zone"], [0, 0])
                    acc[0] += z["amazon"]
                    acc[1] += z["visits"]
        c["wl_suggest"] = sorted(((z, a, v) for z, (a, v) in found.items()),
                                 key=lambda x: (-x[1], x[2]))[:12]
    return rows


def _zone_rows(store: Store, campaign_id: int, since: str | None = None,
               until: str | None = None, net_rows: list | None = None,
               epc: float | None = None, times: dict | None = None) -> list[dict]:
    visits = store.events_by_zone(campaign_id, "visit", since, until)
    clicks = store.events_by_zone(campaign_id, "click", since, until)
    bots = store.events_by_zone(campaign_id, "bot", since, until)
    dev_visits = store.events_by_zone_device(campaign_id, "visit", since, until)
    dev_clicks = store.events_by_zone_device(campaign_id, "click", since, until)
    excluded = store.blacklisted_zones(campaign_id)
    if net_rows is None:
        zones = {z["zone"]: z for z in store.zone_stats(campaign_id)}
    else:
        zones = {r["zone_id"]: {"zone": r["zone_id"], "impressions": r["impressions"],
                                "clicks": r["clicks"], "spent": r["spent"]}
                 for r in net_rows if r["zone_id"]}
    for zone in set(visits) | excluded:
        zones.setdefault(zone, {"zone": zone, "impressions": 0, "clicks": 0, "spent": 0.0})
    out = []
    for z in zones.values():
        amazon = clicks.get(z["zone"], 0)
        out.append({
            **z,
            "ctr": _ratio(z["clicks"], z["impressions"]),
            "visits": visits.get(z["zone"], 0),
            "amazon": amazon,
            "cost_per_amazon": _ratio(z["spent"], amazon),
            "profit": amazon * epc - z["spent"] if epc else None,
            "devices": _device_split(
                {d: dev_visits.get((z["zone"], d), 0) for d in DEVICES},
                {d: dev_clicks.get((z["zone"], d), 0) for d in DEVICES}),
            "excluded": z["zone"] in excluded,
            "time": (times or {}).get(z["zone"]),
            "bots": bots.get(z["zone"], 0),
        })
    out.sort(key=lambda z: (z["excluded"], -z["spent"], -z["impressions"]))
    return out


# Period stats shown on the dashboard, reused for a few minutes so that
# reloading the page doesn't hit PropellerAds' rate limit.
NETWORK_STATS_TTL = timedelta(minutes=5)


def _network_stats(settings: Settings, store: Store, period: Period,
                   cache: dict | None = None) -> tuple[dict, dict, str]:
    """PropellerAds numbers for a period: ({ext_id: row}, {ext_id: [zone rows]}, error)."""
    ids = [c["external_id"] for c in store.list_campaigns() if c["external_id"]]
    if not ids:
        return {}, {}, ""
    key = (period.start, period.end, tuple(sorted(ids)))
    now = datetime.now(timezone.utc)
    cache = {} if cache is None else cache
    cached = cache.get(key)
    if cached and now - cached[0] < NETWORK_STATS_TTL:
        return cached[1], cached[2], ""
    try:
        client = PropellerClient(settings.propeller_api_token)
        totals = client.stats_between(ids, period.start, period.end)
        zones = client.stats_between(ids, period.start, period.end, by_zone=True)
    except PropellerError as exc:
        if cached:  # stale numbers beat none
            age = int((now - cached[0]).total_seconds() // 60)
            return cached[1], cached[2], f"показаны данные {age} мин назад: {str(exc)[:200]}"
        return {}, {}, str(exc)[:300]
    by_zone: dict[str, list] = {}
    for r in zones:
        by_zone.setdefault(r["campaign_id"], []).append(r)
    by_campaign = {r["campaign_id"]: r for r in totals}
    cache.clear()  # one period at a time is plenty
    cache[key] = (now, by_campaign, by_zone)
    return by_campaign, by_zone, ""


def _campaign_totals(rows: list[dict]) -> dict:
    t = {k: sum(r[k] for r in rows) for k in
         ("impressions", "ad_clicks", "spend", "visits", "clicks")}
    t["ctr"] = _ratio(t["ad_clicks"], t["impressions"])
    t["cpc"] = _ratio(t["spend"], t["ad_clicks"])
    t["cost_per_click"] = _ratio(t["spend"], t["clicks"])
    split = [r["devices"] for r in rows if r.get("devices")]
    t["devices"] = _device_split(
        {d: sum(s[d]["visits"] for s in split) for d in DEVICES},
        {d: sum(s[d]["amazon"] for s in split) for d in DEVICES}) if split else {}
    with_epc = [r for r in rows if r.get("revenue") is not None]
    t["revenue"] = sum(r["revenue"] for r in with_epc) if with_epc else None
    t["profit"] = t["revenue"] - t["spend"] if with_epc else None
    return t


def _payload_images(payload: str | None) -> list[str]:
    """Main-image URLs of a campaign's creatives, for dashboard previews."""
    try:
        return [c["image"] for c in json.loads(payload or "{}").get("creatives", [])
                if c.get("image")]
    except (ValueError, AttributeError, TypeError):
        return []


def when(moment: datetime | None, tz_name: str) -> str | None:
    """"15:40 (через 3 ч 5 мин)" in the panel's time zone, or None."""
    if moment is None:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc
    minutes = max(0, int((moment - datetime.now(timezone.utc)).total_seconds() // 60))
    if minutes < 3:
        return "в ближайшие минуты"
    left = f"{minutes // 60} ч {minutes % 60} мин" if minutes >= 60 else f"{minutes} мин"
    day = "" if moment.astimezone(tz).date() == datetime.now(tz).date() else " завтра"
    return f"{moment.astimezone(tz):%H:%M}{day} (через {left})"


DEAL_MIN_SAVING = 10  # % off shown as a deal


def collection_items(col, by_asin: dict) -> list:
    """(item, product, copy) of a gift list still on the site — and, for a
    budget list, still under its budget at today's (fresh) price."""
    out = []
    for item in col.items:
        if item.asin not in by_asin:
            continue
        p, c = by_asin[item.asin]
        if col.max_price and not (p.price and price_is_fresh(p) and p.price <= col.max_price):
            continue
        out.append((item, p, c))
    return out


def pick_labels(picks: list) -> dict[str, str]:
    """Wirecutter-style labels for a guide's picks ((pick, product, copy),
    best first): Our pick, Runner-up, and — from current Amazon prices —
    Budget pick (much cheaper than our pick) / Upgrade pick (much pricier)."""
    labels: dict[str, str] = {}
    if not picks:
        return labels
    top = picks[0][1]
    labels[top.asin] = "Our pick"
    priced = [p for _, p, _ in picks[1:] if p.price and price_is_fresh(p)]
    if top.price and price_is_fresh(top) and priced:
        cheap = min(priced, key=lambda p: p.price)
        if cheap.price <= 0.7 * top.price:
            labels[cheap.asin] = "Budget pick"
        dear = max(priced, key=lambda p: p.price)
        if dear.price >= 1.3 * top.price and dear.asin not in labels:
            labels[dear.asin] = "Upgrade pick"
    runner = next((p for _, p, _ in picks[1:] if p.asin not in labels), None)
    if runner is not None:
        labels[runner.asin] = "Runner-up"
    return labels


def _flag_time(store: Store, key: str) -> datetime | None:
    try:
        return datetime.fromisoformat(store.get_flag(key))
    except ValueError:
        return None


def make_localtime(tz_name: str):
    """Jinja filter: stored UTC ISO timestamp -> "26.09.2026 21:04" local."""
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc

    def localtime(value: str | None) -> str:
        if not value:
            return ""
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return value
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(tz).strftime("%d.%m.%Y %H:%M")

    return localtime


def create_app(settings: Settings | None = None, store: Store | None = None,
               start_loop: bool = True) -> FastAPI:
    settings = settings or get_settings()
    TEMPLATES.env.filters["localtime"] = make_localtime(settings.panel_timezone)
    TEMPLATES.env.globals["app_version"] = os.environ.get("APP_VERSION", "dev")
    store = store or Store(settings.data_dir)
    media_root = (Path(settings.data_dir) / "media").resolve()
    network_cache: dict = {}
    time_key = (settings.secret_key or "dev-only-insecure-key").encode()

    def visit_sig(visit_id: int | str) -> str:
        """Signs the time-on-page URL so only our own page can report for a visit."""
        return hmac.new(time_key, str(visit_id).encode(), hashlib.sha256).hexdigest()[:16]

    app = FastAPI(title="Amazon affiliate agent")
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key or "dev-only-insecure-key",
        https_only=settings.session_https_only,
        same_site="lax",
    )
    main_host = norm_domain(settings.domain)

    @app.middleware("http")
    async def ig_domains(request: Request, call_next):
        """On an iGaming project's own domain serve only its lander: "/" and
        "/go" map to the project's routes, everything else is not found."""
        host = norm_domain(request.headers.get("host", ""))
        if host and host != main_host and "." in host:
            project = await run_in_threadpool(store.get_ig_project_by_domain, host)
            if project:
                path = request.scope["path"]
                mapped = {"/": f"/l/{project['id']}/", "/go": f"/l/{project['id']}/go"}.get(path)
                if mapped is None:
                    if path == "/robots.txt":
                        return PlainTextResponse("User-agent: *\nDisallow: /\n")
                    return Response("Not found", status_code=404)
                request.scope["path"] = mapped
                request.scope["raw_path"] = mapped.encode()
        return await call_next(request)

    app.state.settings = settings
    app.state.store = store
    store.close_interrupted_runs()

    def run_in_background(niche_id: int | None = None, discover: int = 0) -> None:
        def target():
            # Button presses queue behind a running cycle instead of being dropped.
            run_cycle(build_deps(settings, store), niche_id, discover, wait=True)

        threading.Thread(target=target, daemon=True).start()

    if start_loop and settings.agent_interval_hours > 0:
        def loop():
            def plan(seconds: float) -> None:
                at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
                store.set_flag(NEXT_CYCLE_FLAG, at.isoformat(timespec="seconds"))
                time.sleep(seconds)

            plan(30)  # let the server come up first
            while True:
                try:
                    run_cycle(build_deps(settings, store))
                except Exception:
                    logger.exception("agent cycle crashed")
                plan(max(effective(settings, store).agent_interval_hours, 1) * 3600)

        threading.Thread(target=loop, daemon=True).start()

    if start_loop:
        def stats_loop():
            # Between full cycles: fresh stats, kill/zone rules and refilling
            # freed slots, so a $1 test budget is enforced within minutes.
            while True:
                time.sleep(STATS_INTERVAL_SECONDS)
                try:
                    deps = build_deps(settings, store)
                    if deps.push is not None:
                        quick_check(deps)
                except Exception:
                    logger.exception("quick check crashed")

        threading.Thread(target=stats_loop, daemon=True).start()

    # --- public sites -----------------------------------------------------

    def site_or_404(slug: str):
        niche = store.get_niche_by_slug(slug)
        if niche is None or not niche.enabled:
            raise HTTPException(404)
        copy = store.get_site_copy(niche.id)
        if copy is None:
            raise HTTPException(404)
        return niche, copy

    domain_brand = settings.site_name or (
        settings.domain.split(".")[0].title() if settings.domain else "Top Picks"
    )
    domain = settings.domain or "this site"

    def current_brand() -> str:
        """One name for the whole domain: SITE_NAME if set, else — with a
        single live site — that site's own title, so the home page, legal
        pages and the site itself don't carry two different names."""
        if settings.site_name:
            return settings.site_name
        titles = [c.site_title for n in store.list_niches() if n.enabled
                  for c in [store.get_site_copy(n.id)] if c]
        return titles[0] if len(titles) == 1 else domain_brand

    def public_ctx(request: Request, **extra) -> dict:
        return {
            "request": request,
            "brand": current_brand(),
            "domain": domain,
            "contact_email": settings.contact_email,
            "updated": LEGAL_PAGES_UPDATED,
            "year": datetime.now(timezone.utc).year,
            **extra,
        }

    def site_ctx(request: Request, niche, site_copy, **extra) -> dict:
        return public_ctx(request, niche=niche, site=site_copy,
                          price_is_fresh=price_is_fresh, **extra)

    def site_sections(niche_id: int, products: list) -> list[dict]:
        """The site's sections with their live products (best first); products
        not in the saved plan yet go to "More Picks". [] if there's no plan."""
        plan = store.get_site_plan(niche_id)
        if plan is None:
            return []
        by_asin = {p.asin: (p, c) for p, c in products}
        out, placed = [], set()
        for s in plan.sections:
            items = [by_asin[a] for a in s.asins if a in by_asin]
            placed.update(p.asin for p, _ in items)
            if items:
                out.append({"section": s, "entries": items})
        rest = [(p, c) for p, c in products if p.asin not in placed]
        if rest:
            other = next((x for x in out if x["section"].name == SECTIONS_OTHER), None)
            if other is None:
                other = {"section": SiteSection(slug="more-picks", name=SECTIONS_OTHER),
                         "entries": []}
                out.append(other)
            other["entries"] += rest
        return out

    @app.get("/s/{slug}/", response_class=HTMLResponse)
    def site_home(request: Request, slug: str):
        niche, copy = site_or_404(slug)
        products = [(p, c) for p, c in store.list_products(niche.id) if c]
        return SITE_TEMPLATES.TemplateResponse(
            request, "index.html", site_ctx(request, niche, copy, products=products,
                                            sections=site_sections(niche.id, products))
        )

    @app.get("/s/{slug}/c/{section_slug}", response_class=HTMLResponse)
    def guide_page(request: Request, slug: str, section_slug: str):
        niche, copy = site_or_404(slug)
        products = [(p, c) for p, c in store.list_products(niche.id) if c]
        sections = site_sections(niche.id, products)
        found = next((x for x in sections if x["section"].slug == section_slug), None)
        if found is None:
            raise HTTPException(404)
        by_asin = {p.asin: (p, c) for p, c in found["entries"]}
        picks = [(pick, *by_asin[pick.asin]) for pick in found["section"].picks
                 if pick.asin in by_asin]
        plan = store.get_site_plan(niche.id)
        try:
            updated_on = datetime.fromisoformat(plan.built_at).strftime("%B %Y")
        except (AttributeError, ValueError):
            updated_on = ""
        picked = {pick.asin for pick, _, _ in picks}
        considered = [(p, c) for p, c in found["entries"] if p.asin not in picked]
        return SITE_TEMPLATES.TemplateResponse(
            request, "guide.html",
            site_ctx(request, niche, copy, section=found["section"], entries=found["entries"],
                     picks=picks, sections=sections, updated_on=updated_on,
                     labels=pick_labels(picks), considered=considered),
        )

    @app.get("/s/{slug}/a/{article_slug}", response_class=HTMLResponse)
    def article_page(request: Request, slug: str, article_slug: str):
        niche, copy = site_or_404(slug)
        products = [(p, c) for p, c in store.list_products(niche.id) if c]
        sections = site_sections(niche.id, products)
        found = next(((x, a) for x in sections for a in x["section"].all_articles
                      if a.slug == article_slug), None)
        if found is None:
            raise HTTPException(404)
        x, article = found
        return SITE_TEMPLATES.TemplateResponse(
            request, "article.html",
            site_ctx(request, niche, copy, section=x["section"], article=article,
                     top=x["entries"][:4], sections=sections),
        )

    @app.get("/s/{slug}/vs/{vs_slug}", response_class=HTMLResponse)
    def versus_page(request: Request, slug: str, vs_slug: str):
        niche, copy = site_or_404(slug)
        products = [(p, c) for p, c in store.list_products(niche.id) if c]
        by_asin = {p.asin: (p, c) for p, c in products}
        sections = site_sections(niche.id, products)
        found = next((x["section"] for x in sections
                      if x["section"].versus and x["section"].versus.slug == vs_slug), None)
        if found is None or found.versus.a not in by_asin or found.versus.b not in by_asin:
            raise HTTPException(404)
        return SITE_TEMPLATES.TemplateResponse(
            request, "versus.html",
            site_ctx(request, niche, copy, section=found, vs=found.versus,
                     a=by_asin[found.versus.a], b=by_asin[found.versus.b]),
        )

    @app.get("/s/{slug}/g/{collection_slug}", response_class=HTMLResponse)
    def collection_page(request: Request, slug: str, collection_slug: str):
        niche, copy = site_or_404(slug)
        plan = store.get_site_plan(niche.id)
        found = next((c for c in (plan.collections if plan else [])
                      if c.slug == collection_slug), None)
        if found is None:
            raise HTTPException(404)
        by_asin = {p.asin: (p, c) for p, c in store.list_products(niche.id) if c}
        items = collection_items(found, by_asin)
        return SITE_TEMPLATES.TemplateResponse(
            request, "collection.html",
            site_ctx(request, niche, copy, collection=found, items=items))

    def all_sites() -> list[dict]:
        """Every live site with its products and sections (best first)."""
        out = []
        for n in store.list_niches():
            site_copy = store.get_site_copy(n.id)
            if not n.enabled or site_copy is None:
                continue
            listed = [(p, c) for p, c in store.list_products(n.id) if c]
            if listed:
                out.append({"niche": n, "site": site_copy, "listed": listed,
                            "sections": site_sections(n.id, listed)})
        return out

    def deals_of(sites: list[dict], limit: int) -> list[dict]:
        """Products Amazon currently shows a discount for (fresh prices only)."""
        deals = [{"niche": s["niche"], "product": p, "copy": c}
                 for s in sites for p, c in s["listed"]
                 if (p.savings_percent or 0) >= DEAL_MIN_SAVING and price_is_fresh(p)]
        deals.sort(key=lambda d: -(d["product"].savings_percent or 0))
        return deals[:limit]

    def articles_of(sites: list[dict], mixed: bool = False) -> list[dict]:
        """Advice cards; each article of a section shows a different product.
        `mixed` puts one article per section first (for the hub)."""
        rows = []
        for s in sites:
            for x in s["sections"]:
                images = [p.image_url for p, _ in x["entries"] if p.image_url] or [""]
                rows.append([{"niche": s["niche"], "section": x["section"], "article": a,
                              "image": images[i % len(images)]}
                             for i, a in enumerate(x["section"].all_articles)])
        if not mixed:
            return [a for row in rows for a in row]
        depth = max((len(r) for r in rows), default=0)
        return [r[i] for i in range(depth) for r in rows if i < len(r)]

    def versus_of(sites: list[dict]) -> list[dict]:
        out = []
        for s in sites:
            by_asin = {p.asin: p for p, _ in s["listed"]}
            for x in s["sections"]:
                v = x["section"].versus
                if v and v.a in by_asin and v.b in by_asin:
                    out.append({"niche": s["niche"], "vs": v, "a": by_asin[v.a],
                                "b": by_asin[v.b]})
        return out

    def collections_of(sites: list[dict]) -> list[dict]:
        out = []
        for s in sites:
            plan = store.get_site_plan(s["niche"].id)
            by_asin = {p.asin: (p, c) for p, c in s["listed"]}
            for col in (plan.collections if plan else []):
                items = collection_items(col, by_asin)
                if len(items) >= 3:
                    out.append({"niche": s["niche"], "collection": col, "count": len(items),
                                "image": next((p.image_url for _, p, _ in items if p.image_url),
                                              "")})
        return out

    @app.get("/gifts", response_class=HTMLResponse)
    def gifts_page(request: Request):
        return SITE_TEMPLATES.TemplateResponse(
            request, "gifts.html", public_ctx(request, collections=collections_of(all_sites())))

    @app.get("/deals", response_class=HTMLResponse)
    def deals_page(request: Request):
        return SITE_TEMPLATES.TemplateResponse(
            request, "deals.html",
            public_ctx(request, deals=deals_of(all_sites(), 120), price_is_fresh=price_is_fresh))

    @app.get("/advice", response_class=HTMLResponse)
    def advice_index(request: Request):
        return SITE_TEMPLATES.TemplateResponse(
            request, "advice_index.html", public_ctx(request, articles=articles_of(all_sites())))

    @app.get("/s/{slug}")
    def site_home_redirect(slug: str):
        return RedirectResponse(f"/s/{slug}/", status_code=301)

    @app.get("/s/{slug}/p/{asin}", response_class=HTMLResponse)
    def product_page(request: Request, slug: str, asin: str,
                     c: str | None = None, z: str | None = None):
        niche, copy = site_or_404(slug)
        found = store.get_product(niche.id, asin)
        if found is None or found[1] is None:
            raise HTTPException(404)
        product, product_copy = found
        campaign_id = int(c) if c and c.isdigit() else None
        zone = _clean(z)
        visit_id = store.log_event("visit", niche.id, asin, campaign_id, zone,
                                   device_of(request))
        go = f"/go/{slug}/{asin}?v={visit_id}"
        if campaign_id:
            go += f"&c={campaign_id}" + (f"&z={zone}" if zone else "")
        listed_products = [(p, pc) for p, pc in store.list_products(niche.id) if pc]
        listed = [p.asin for p, _ in listed_products]
        rank = listed.index(asin) + 1 if asin in listed else None
        section = next((x for x in site_sections(niche.id, listed_products)
                        if any(p.asin == asin for p, _ in x["entries"])), None)
        pool = section["entries"] if section else listed_products
        others = [(p, pc) for p, pc in pool if p.asin != asin][:4]
        if len(others) < 4:  # top up from the rest of the site
            others += [(p, pc) for p, pc in listed_products
                       if p.asin != asin and (p, pc) not in others][:4 - len(others)]
        return SITE_TEMPLATES.TemplateResponse(
            request, "product.html",
            site_ctx(request, niche, copy, product=product, copy=product_copy,
                     go_url=go, others=others, rank=rank,
                     time_url=f"/t/{visit_id}/{visit_sig(visit_id)}",
                     section=section["section"] if section else None),
        )

    @app.post("/pv")
    async def pageview(request: Request):
        """Page script beacon for visitors who didn't come from our ads. Only
        real browsers run it; bots and the logged-in owner aren't counted."""
        if logged_in(request) or BOT_UA.search(request.headers.get("user-agent", "") or "bot"):
            return Response(status_code=204)
        try:
            data = json.loads((await request.body())[:2000] or b"{}")
        except ValueError:
            return Response(status_code=204)
        path = str(data.get("p") or "")[:200]
        if not path.startswith("/") or path.startswith(("/admin", "/go/", "/t/", "/login")):
            return Response(status_code=204)
        source, entry = referrer_source(str(data.get("r") or "")[:500], settings.domain)
        ip = client_ip(request)
        store.log_pageview(path, source, entry, device_of(request),
                           visit_sig(f"ip:{ip}") if ip else None)
        return Response(status_code=204)

    @app.post("/t/{visit_id}/{sig}")
    def time_on_page(visit_id: int, sig: str, s: int = 0):
        """The product page reports how long it was visible (sendBeacon)."""
        if hmac.compare_digest(sig, visit_sig(visit_id)):
            store.set_visit_seconds(visit_id, max(0, min(s, MAX_TIME_ON_PAGE)))
        return Response(status_code=204)

    @app.get("/s/{slug}/about")
    def old_about_page(slug: str):
        return RedirectResponse("/affiliate-disclosure", status_code=301)

    # --- site-wide pages Amazon Associates reviewers look for -------------

    for path, template in (
        ("/about", "about_site.html"),
        ("/privacy", "privacy.html"),
        ("/terms", "terms.html"),
        ("/affiliate-disclosure", "disclosure.html"),
        ("/how-we-choose", "how_we_choose.html"),
    ):
        def page(request: Request, _template: str = template):
            return SITE_TEMPLATES.TemplateResponse(request, _template, public_ctx(request))

        app.add_api_route(path, page, methods=["GET"], response_class=HTMLResponse)

    @app.get("/contact", response_class=HTMLResponse)
    def contact_form(request: Request):
        return SITE_TEMPLATES.TemplateResponse(request, "contact.html", public_ctx(request))

    @app.post("/contact", response_class=HTMLResponse)
    def contact_send(request: Request, name: str = Form(""), email: str = Form(""),
                     message: str = Form(""), website: str = Form("")):
        error = None
        if website:  # honeypot field, invisible to people
            return SITE_TEMPLATES.TemplateResponse(
                request, "contact.html", public_ctx(request, sent=True))
        if not (name.strip() and "@" in email and message.strip()):
            error = "Please fill in your name, a valid email and a message."
        elif store.count_recent_messages(hours=1) >= CONTACT_HOURLY_LIMIT:
            error = "Too many messages right now — please try again later."
        if error:
            return SITE_TEMPLATES.TemplateResponse(
                request, "contact.html", public_ctx(request, error=error), status_code=400)
        store.add_message(name.strip()[:100], email.strip()[:200], message.strip()[:5000])
        return SITE_TEMPLATES.TemplateResponse(
            request, "contact.html", public_ctx(request, sent=True))

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        return (
            "User-agent: *\nDisallow: /admin\nDisallow: /login\nDisallow: /go/\nDisallow: /t/\nDisallow: /pv\n"
            f"Sitemap: {settings.public_base_url()}/sitemap.xml\n"
        )

    @app.get("/sitemap.xml")
    def sitemap():
        base = settings.public_base_url()
        urls = [f"{base}/"] + [f"{base}{p}" for p in (
            "/deals", "/advice", "/gifts", "/about", "/how-we-choose", "/contact", "/privacy", "/terms",
            "/affiliate-disclosure")]
        for n in store.list_niches():
            if not n.enabled or store.get_site_copy(n.id) is None:
                continue
            urls.append(f"{base}/s/{n.slug}/")
            plan = store.get_site_plan(n.id)
            urls += [f"{base}/s/{n.slug}/c/{s.slug}" for s in (plan.sections if plan else [])
                     if s.picks]
            for s in (plan.sections if plan else []):
                urls += [f"{base}/s/{n.slug}/a/{a.slug}" for a in s.all_articles]
                if s.versus:
                    urls.append(f"{base}/s/{n.slug}/vs/{s.versus.slug}")
            urls += [f"{base}/s/{n.slug}/g/{c.slug}" for c in (plan.collections if plan else [])]
            urls += [f"{base}/s/{n.slug}/p/{p.asin}"
                     for p, c in store.list_products(n.id) if c]
        body = "".join(f"<url><loc>{escape(u)}</loc></url>" for u in urls)
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
            media_type="application/xml",
        )

    @app.get("/favicon.svg")
    def favicon():
        return Response(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#e8590c"/><stop offset="1" stop-color="#f59f00"/>'
            '</linearGradient></defs><rect width="64" height="64" rx="14" fill="url(#g)"/>'
            '<text x="32" y="44" font-family="Arial,sans-serif" font-size="36" '
            f'font-weight="700" fill="#fff" text-anchor="middle">{escape(current_brand()[:1])}</text></svg>',
            media_type="image/svg+xml",
        )

    @app.get("/admin-favicon.svg")
    def admin_favicon():
        # Blue, with an "A" for agent, so the dashboard tab is easy to tell
        # apart from the public site's orange one.
        return Response(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#1f5fbf"/><stop offset="1" stop-color="#4c8dff"/>'
            '</linearGradient></defs><rect width="64" height="64" rx="14" fill="url(#g)"/>'
            '<text x="32" y="44" font-family="Arial,sans-serif" font-size="36" '
            'font-weight="700" fill="#fff" text-anchor="middle">A</text></svg>',
            media_type="image/svg+xml",
        )

    @app.get("/favicon.ico")
    def favicon_ico():
        return RedirectResponse("/favicon.svg", status_code=301)

    def bot_reasons_of(request: Request, visit: str | None, js: str | None,
                       wd: str | None, ip_key: str) -> list[str]:
        reasons = []
        ua = request.headers.get("user-agent", "")
        if not ua or BOT_UA.search(ua):
            reasons.append("bot-ua")
        if wd == "1":
            reasons.append("webdriver")
        if js != "1":  # our page script marks links a real browser clicked
            reasons.append("no-js")
        opened = store.visit_time(int(visit)) if visit and visit.isdigit() else None
        if opened and (datetime.now(timezone.utc) - opened).total_seconds() < FAST_CLICK_SECONDS:
            reasons.append("fast")
        hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
        if ip_key and store.clicks_from_ip(ip_key, hour_ago) >= IP_CLICKS_PER_HOUR:
            reasons.append("repeat-ip")
        return reasons

    @app.get("/go/{slug}/{asin}")
    def outbound(request: Request, slug: str, asin: str, c: str | None = None,
                 z: str | None = None, v: str | None = None, js: str | None = None,
                 wd: str | None = None, ok: str | None = None, o: str | None = None):
        niche = store.get_niche_by_slug(slug)
        found = store.get_product(niche.id, asin) if niche else None
        if found is None:
            raise HTTPException(404)
        campaign_id = int(c) if c and c.isdigit() else None
        zone, device = _clean(z), device_of(request)
        ip_key = visit_sig(f"ip:{client_ip(request)}") if client_ip(request) else ""
        reasons = bot_reasons_of(request, v, js, wd, ip_key)
        confirmed = bool(ok) and hmac.compare_digest(ok, visit_sig(f"ok:{slug}:{asin}"))
        if confirmed and not HARD_BOT_REASONS & set(reasons):
            reasons = []  # a person pressed "Continue" on the check page
        if not reasons:
            # "organic": a visitor who didn't come from our ads (page script mark)
            organic = campaign_id is None and o == "1" and not logged_in(request)
            store.log_event("click", niche.id, asin, campaign_id, zone, device, ip=ip_key,
                            reason="organic" if organic else None)
            return RedirectResponse(found[0].url, status_code=302)
        store.log_event("bot", niche.id, asin, campaign_id, zone, device,
                        reason=",".join(reasons), ip=ip_key)
        plain = f"https://{marketplace_host(settings.amazon_country)}/dp/{asin}"
        params = {k: val for k, val in (("c", c), ("z", zone)) if val}
        retry = f"/go/{slug}/{asin}?" + urlencode(
            {**params, "ok": visit_sig(f"ok:{slug}:{asin}")})
        soft = not HARD_BOT_REASONS & set(reasons)
        return HTMLResponse(CONTINUE_PAGE.format(
            title=escape(found[0].title[:120]), plain=escape(plain),
            retry=escape(retry) if soft else ""), status_code=200,
            headers={"X-Robots-Tag": "noindex, nofollow"})

    @app.get("/media/{slug}/{filename}")
    def media(slug: str, filename: str):
        path = (media_root / slug / filename).resolve()
        if media_root not in path.parents or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    # --- admin ------------------------------------------------------------

    def logged_in(request: Request) -> bool:
        return request.session.get("user") == settings.admin_username

    def to_login() -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    register_fb_routes(app, TEMPLATES, settings, store, logged_in, to_login)
    register_ig_routes(app, TEMPLATES, settings, store, logged_in, to_login, bot_ua=BOT_UA,
                       device_of=device_of, client_ip=client_ip,
                       ip_sig=lambda ip: visit_sig(f"ip:{ip}"))

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return TEMPLATES.TemplateResponse(request, "login.html", {"request": request, "error": None})

    @app.post("/login", response_class=HTMLResponse)
    def login(request: Request, username: str = Form(...), password: str = Form(...)):
        ok = (
            settings.admin_password_hash
            and username == settings.admin_username
            and bcrypt.checkpw(password.encode(), settings.admin_password_hash.encode())
        )
        if not ok:
            return TEMPLATES.TemplateResponse(
                request, "login.html", {"request": request, "error": "Неверный логин или пароль"},
                status_code=401,
            )
        request.session["user"] = username
        return RedirectResponse("/admin", status_code=303)

    @app.get("/logout")
    def logout(request: Request):
        request.session.clear()
        return to_login()

    @app.get("/", response_class=HTMLResponse)
    def hub(request: Request):
        """Public home page: guides (most read first), deals, advice, top picks."""
        sites = all_sites()
        week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
        for site in sites:
            visits = store.events_by_asin(site["niche"].id, "visit", week)
            guides = []
            for x in site["sections"]:
                s = x["section"]
                if s.picks:
                    lead = next((p for p, _ in x["entries"] if p.image_url), x["entries"][0][0])
                    guides.append({"section": s, "count": len(x["entries"]),
                                   "compared": len(s.picks), "image": lead.image_url,
                                   "reads": sum(visits.get(p.asin, 0) for p, _ in x["entries"])})
            guides.sort(key=lambda g: -g["reads"])  # most read first
            site.update(top=site["listed"][:8], guides=guides)
        return SITE_TEMPLATES.TemplateResponse(
            request, "hub.html",
            public_ctx(request, sites=sites, deals=deals_of(sites, 8),
                       articles=articles_of(sites, mixed=True)[:6], versus=versus_of(sites)[:6],
                       collections=collections_of(sites), price_is_fresh=price_is_fresh))

    @app.get("/admin", response_class=HTMLResponse)
    def dashboard(request: Request, period: str | None = None, date_from: str | None = None,
                  date_to: str | None = None):
        if not logged_in(request):
            return to_login()
        eff = effective(settings, store)
        # The chosen period sticks (in the session) across button presses,
        # which all redirect back to a bare /admin.
        if period or date_from or date_to:
            request.session["period"] = [period, date_from, date_to]
        else:
            period, date_from, date_to = request.session.get("period") or [None, None, None]
        chosen = parse_period(period, date_from, date_to, eff.panel_timezone)
        net, net_zones, period_error = ({}, {}, "")
        if not chosen.is_all:
            net, net_zones, period_error = _network_stats(eff, store, chosen, network_cache)
        niches = []
        for n in store.list_niches():
            latest: dict[str, dict] = {}
            for c in store.list_campaigns(niche_id=n.id):  # newest first
                latest.setdefault(c["asin"], c)
            items = [{"asin": p.asin, "title": p.title, "epc": p.epc, "ready": copy is not None,
                      "budget": p.cc_budget,
                      "campaign": latest.get(p.asin)}
                     for p, copy in store.list_products(n.id)]
            niches.append({
                "niche": n,
                "site": store.get_site_copy(n.id),
                "products": len(items),
                "shelf": items,
                "stats": store.niche_stats(n.id),
            })
        campaigns = _campaign_rows(store, eff.push_bid_cpc, chosen, net, net_zones)
        totals = _campaign_totals(campaigns)
        return TEMPLATES.TemplateResponse(
            request, "dashboard.html",
            {
                "request": request,
                "settings": effective(settings, store),
                "overrides": load_overrides(store),
                "niches": niches,
                "campaigns": campaigns,
                "runs": store.list_runs(15, kinds=("cycle", "chat")),
                "checks": store.list_runs(40, kinds=("check",)),
                "paused": store.get_flag(PAUSE_FLAG) == "1",
                "manual": is_manual(store),
                "flash": (flash := request.session.pop("flash", None)),
                "flash_bad": bool(flash and FLASH_FAILED_RE.search(flash)),
                "running": store.run_in_progress(),
                "stats_error": store.get_flag("stats_error"),
                "totals": totals,
                "chat_unread": store.count_chat_after(
                    int(store.get_flag(CHAT_SEEN_FLAG, "0") or 0), ADVICE_PREFIX),
                "organic": [(label, store.organic_stats(
                    (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
                        timespec="seconds")))
                    for label, days in (("за 24 ч", 1), ("7 дней", 7), ("30 дней", 30))],
                "period": chosen,
                "presets": PRESETS,
                "period_error": period_error,
                "messages": store.list_messages(20),
                "running_budget": store.running_daily_budget(),
                "spent_today": spent_today(store),
                "committed_today": committed_24h(store) or 0.0,
                "active_count": len(store.list_campaigns(statuses=(ACTIVE,))),
                "capped_count": len(store.list_campaigns(statuses=(CAPPED,))),
                "resume_at": when(resume_eta(store, eff.max_daily_spend), eff.panel_timezone),
                "next_cycle": when(_flag_time(store, NEXT_CYCLE_FLAG), eff.panel_timezone),
            },
        )

    @app.post("/niches")
    def add_niche(request: Request, keywords: str = Form(...), search_index: str = Form("All"),
                  language: str = Form("English"), max_price: str = Form("")):
        if not logged_in(request):
            return to_login()
        try:
            price = float(max_price) if max_price.strip() else None
        except ValueError:
            price = None
        niche = store.add_niche(keywords.strip(), search_index.strip(), language.strip(), price)
        run_in_background(niche.id)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/import")
    def import_opportunities(request: Request, text: str = Form(...),
                             name: str = Form("Top Deals"), language: str = Form("English")):
        if not logged_in(request):
            return to_login()
        asins = parse_opportunities(text)
        if not asins:
            request.session["flash"] = "В тексте не найдено ни одного ASIN."
            return RedirectResponse("/admin", status_code=303)
        name = name.strip() or "Top Deals"
        existing = next(
            (n for n in store.list_niches() if n.asins and n.keywords.lower() == name.lower()),
            None,
        )
        details = parse_opportunity_details(text)
        if existing:
            total = store.merge_niche_asins(existing.id, asins, details)
            niche_id = existing.id
        else:
            niche_id = store.add_niche(name, language=language.strip() or "English",
                                       asins=asins, asin_meta=details).id
            total = len(asins)
        with_epc = sum(1 for v in asins.values() if v is not None)
        low = sum(1 for a in asins if (details.get(a) or {}).get("budget") == "low")
        request.session["flash"] = (
            f"Импортировано {len(asins)} товаров ({with_epc} с EPC"
            + (f", {low} с бюджетом Low — их агент сам не рекламирует" if low else "")
            + "), всего на сайте "
            f"«{name}»: {total}. Агент проверяет их на Amazon — обновите страницу через минуту."
        )
        run_in_background(niche_id)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/niches/{niche_id}/run")
    def run_niche(request: Request, niche_id: int):
        if not logged_in(request):
            return to_login()
        run_in_background(niche_id)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/niches/{niche_id}/toggle")
    def toggle_niche(request: Request, niche_id: int):
        if not logged_in(request):
            return to_login()
        niche = store.get_niche(niche_id)
        if niche:
            store.set_niche_enabled(niche_id, not niche.enabled)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/niches/{niche_id}/delete")
    def delete_niche(request: Request, niche_id: int):
        if not logged_in(request):
            return to_login()
        # Stop its campaigns first so nothing keeps spending on a dead site.
        deps = build_deps(settings, store)
        for c in store.list_campaigns(niche_id=niche_id, statuses=(ACTIVE,)):
            stop_campaign(deps, c, STOPPED, "site deleted")
        store.delete_niche(niche_id)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/discover")
    def discover(request: Request, count: int = Form(1)):
        if not logged_in(request):
            return to_login()
        run_in_background(discover=max(1, min(count, 5)))
        return RedirectResponse("/admin", status_code=303)

    @app.post("/run")
    def run_all(request: Request):
        if not logged_in(request):
            return to_login()
        run_in_background()
        return RedirectResponse("/admin", status_code=303)

    @app.post("/campaigns/{campaign_id}/stop")
    def stop_campaign_route(request: Request, campaign_id: int):
        if not logged_in(request):
            return to_login()
        c = store.get_campaign(campaign_id)
        if c:
            # Manual stop counts as a verdict on the product: don't relaunch.
            stop_campaign(build_deps(settings, store), c, KILLED, "stopped manually")
        return RedirectResponse("/admin", status_code=303)

    @app.post("/campaigns/{campaign_id}/resume")
    def resume_campaign_route(request: Request, campaign_id: int):
        if not logged_in(request):
            return to_login()
        error = resume_campaign(build_deps(settings, store), campaign_id)
        queued = not error and store.get_campaign(campaign_id)["status"] == CAPPED
        request.session["flash"] = (
            f"Кампания #{campaign_id} не возвращена: {error}" if error else
            f"Кампания #{campaign_id} вернута и ждёт места под лимитом за 24 ч: включится "
            "сама, как только оно освободится (раньше новых запусков). Передумали — "
            "кнопка «Стоп»." if queued else
            f"Кампания #{campaign_id} снова работает. Агент не будет отключать её по "
            "результатам (остановить можно кнопкой «Стоп»), но будет отключать её зоны "
            "без переходов на Amazon."
        )
        return RedirectResponse(f"/admin#c{campaign_id}", status_code=303)

    @app.post("/campaigns/{campaign_id}/redraw")
    def redraw(request: Request, campaign_id: int):
        if not logged_in(request):
            return to_login()

        def target():
            deps = build_deps(settings, store)
            if redraw_campaign(deps, campaign_id):
                logger.info("redrew creatives of campaign #%s", campaign_id)

        threading.Thread(target=target, daemon=True).start()
        request.session["flash"] = (
            f"Кампания #{campaign_id}: агент перерисовывает картинки — обновите страницу через минуту."
        )
        return RedirectResponse("/admin", status_code=303)

    @app.post("/settings")
    async def save_settings(request: Request):
        if not logged_in(request):
            return to_login()
        form = {k: str(v) for k, v in (await request.form()).items()}
        values, errors = parse_form(form)
        if errors:
            request.session["flash"] = "Настройки не сохранены: " + "; ".join(errors)
            return RedirectResponse("/admin#settings", status_code=303)
        note = change_settings(settings, store, values)
        request.session["flash"] = note
        return RedirectResponse("/admin#settings", status_code=303)

    ZONE_RE = re.compile(r"^\d{1,12}$")

    @app.post("/campaigns/{campaign_id}/whitelist")
    async def whitelist_campaign(request: Request, campaign_id: int):
        if not logged_in(request):
            return to_login()
        form = await request.form()
        zones = [str(z) for z in form.getlist("zones") if ZONE_RE.match(str(z))]
        error = launch_whitelist(build_deps(settings, store), campaign_id, zones)
        request.session["flash"] = (
            f"Вайт-лист не создан: {error}" if error else
            f"Создана новая кампания на тот же товар только с зонами: {', '.join(zones)}. "
            "Она появится в списке после модерации PropellerAds; кампания "
            f"#{campaign_id} продолжает работать."
        )
        return RedirectResponse("/admin#campaigns", status_code=303)

    @app.post("/campaigns/{campaign_id}/whitelist/add")
    async def whitelist_add(request: Request, campaign_id: int):
        if not logged_in(request):
            return to_login()
        form = await request.form()
        zones = [z for z in re.split(r"[\s,;]+", str(form.get("zones") or "")) if z]
        zones += [str(z) for z in form.getlist("pick")]
        zones = [z for z in zones if ZONE_RE.match(z)]
        error = add_whitelist_zones(build_deps(settings, store), campaign_id, zones)
        request.session["flash"] = (
            f"Зоны не добавлены: {error}" if error else
            f"Кампания #{campaign_id}: в вайт-лист добавлены зоны {', '.join(zones)}.")
        return RedirectResponse(f"/admin#c{campaign_id}", status_code=303)

    @app.post("/campaigns/{campaign_id}/zones/{zone}/{action}")
    def zone_action(request: Request, campaign_id: int, zone: str, action: str):
        if not logged_in(request):
            return to_login()
        if not ZONE_RE.match(zone) or action not in ("exclude", "include"):
            raise HTTPException(400)
        deps = build_deps(settings, store)
        error = (exclude_zone(deps, campaign_id, zone) if action == "exclude"
                 else include_zone(deps, campaign_id, zone))
        if request.headers.get("x-requested-with") == "fetch":  # the button, no reload
            return JSONResponse({"ok": not error, "error": error})
        verb = "отключена" if action == "exclude" else "снова включена"
        request.session["flash"] = (
            f"Кампания #{campaign_id}: не удалось изменить зону {zone} — {error}" if error
            else f"Кампания #{campaign_id}: зона {zone} {verb}."
        )
        return RedirectResponse(f"/admin#c{campaign_id}", status_code=303)

    chat_agent = ChatAgent(settings, store,
                           run_cycle=lambda discover: run_in_background(discover=discover))

    @app.get("/chat/history")
    def chat_history(request: Request):
        if not logged_in(request):
            return JSONResponse({"error": "login"}, status_code=401)
        messages = store.list_chat(50)
        if messages:  # opening the chat marks the agent's advice as read
            store.set_flag(CHAT_SEEN_FLAG, str(messages[-1]["id"]))
        return {"messages": messages}

    @app.post("/chat")
    async def chat(request: Request):
        if not logged_in(request):
            return JSONResponse({"error": "login"}, status_code=401)
        try:
            message = str((await request.json()).get("message", "")).strip()[:2000]
        except ValueError:
            message = ""
        if not message:
            raise HTTPException(400)
        return await run_in_threadpool(chat_agent.reply, message)

    @app.post("/chat/clear")
    def chat_clear(request: Request):
        if not logged_in(request):
            return JSONResponse({"error": "login"}, status_code=401)
        store.clear_chat()
        return {"ok": True}

    @app.post("/stats/refresh")
    def refresh_stats(request: Request):
        if not logged_in(request):
            return to_login()

        def target():
            deps = build_deps(settings, store)
            sync_moderation(deps)
            sync_stats(deps)

        threading.Thread(target=target, daemon=True).start()
        request.session["flash"] = "Статистика обновляется из PropellerAds — обновите страницу через полминуты."
        return RedirectResponse("/admin#campaigns", status_code=303)

    @app.post("/mode")
    def set_mode(request: Request, mode: str = Form(...)):
        if not logged_in(request):
            return to_login()
        manual = mode == "manual"
        store.set_flag(MANUAL_FLAG, "1" if manual else "0")
        request.session["flash"] = (
            "Ручной режим: агент больше сам не запускает и не отключает кампании и не "
            "трогает зоны — управляйте кнопками или через чат. Лимит расхода и аварийные "
            "защиты работают." if manual else
            "Автоматический режим: агент снова сам запускает, тестирует и отключает кампании "
            "и чистит зоны."
        )
        return RedirectResponse("/admin", status_code=303)

    @app.post("/niches/{niche_id}/products/{asin}/launch")
    def launch_product_route(request: Request, niche_id: int, asin: str):
        if not logged_in(request):
            return to_login()
        error = launch_product(build_deps(settings, store), niche_id, asin)
        request.session["flash"] = (f"Реклама на {asin} не запущена: {error}" if error else
                                    f"Кампания на {asin} создана и отправлена на модерацию.")
        return RedirectResponse("/admin#campaigns", status_code=303)

    @app.post("/killswitch")
    def killswitch(request: Request, on: str = Form(...)):
        if not logged_in(request):
            return to_login()
        store.set_flag(PAUSE_FLAG, "1" if on == "1" else "0")
        if on == "1":
            stop_all(build_deps(settings, store))
        return RedirectResponse("/admin", status_code=303)

    return app
