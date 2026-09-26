"""The autonomous cycle, run every AGENT_INTERVAL_HOURS (or on "Run now"):

1. For every enabled niche: search Amazon, select the best products,
   write missing site/product copy. The site itself is rendered live from
   the store by the web app, so saving products *is* publishing them.
2. Campaign management across all niches:
   a. sync spend from PropellerAds,
   b. stop campaigns that fail the kill rule or whose product left the site,
   c. blacklist zones that spent money without a single click to Amazon,
   d. launch new push campaigns for top products, within MAX_DAILY_SPEND.

Every stage is best-effort: a failure is logged into the run and the
cycle moves on, since this runs unattended.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from amzagent.amazon.catalog import Catalog, CatalogError, CreatorsApiCatalog
from amzagent.amazon.creator_connections import offline_products
from amzagent.config import Settings
from amzagent.content.llm import LLM, LLMError, get_llm
from amzagent.content.writer import write_product_copy, write_site_copy
from amzagent.models import COPY_VERSION, Niche
from amzagent.panel_settings import effective
from amzagent.push import propeller
from amzagent.push.ai_creatives import CreativeError, OpenAIImages, make_ai_creatives
from amzagent.push.creatives import render_creatives
from amzagent.push.propeller import PropellerClient, PropellerError
from amzagent.selection.niches import discover_niches
from amzagent.selection.selector import score, select_products
from amzagent.store import ACTIVE, DRY_RUN, ERROR, STOPPED, Store, now_iso

logger = logging.getLogger(__name__)

KILLED = "killed"  # stopped by the kill rule — never relaunched for that product
PAUSE_FLAG = "paused_all"
SEARCH_PAGES = 2  # 10 items per page
CURATED_SITE_SIZE = 30


@dataclass
class Deps:
    settings: Settings
    store: Store
    catalog: Catalog | None = None
    llm: LLM | None = None
    push: PropellerClient | None = None
    painter: object | None = None  # draws AI push images (OpenAIImages)
    log: list[str] = field(default_factory=list)
    run_id: int | None = None

    def say(self, msg: str) -> None:
        logger.info(msg)
        self.log.append(msg)
        if self.run_id is not None:
            # Written as it happens so the dashboard shows a long run live.
            self.store.append_run_log(self.run_id, msg)


def build_deps(settings: Settings, store: Store) -> Deps:
    # Values saved in the dashboard override .env.
    settings = effective(settings, store)
    deps = Deps(settings=settings, store=store)
    try:
        deps.catalog = CreatorsApiCatalog(settings)
    except CatalogError as exc:
        deps.say(f"Amazon: not configured ({exc})")
    try:
        deps.llm = get_llm(settings)
    except LLMError as exc:
        deps.say(f"LLM: not configured ({exc})")
    if settings.push_creatives == "ai":
        try:
            deps.painter = OpenAIImages(settings)
        except CreativeError as exc:
            deps.say(f"AI push images: off ({exc}), using simple ones")
    if settings.push_live:
        try:
            deps.push = PropellerClient(settings.propeller_api_token)
        except PropellerError as exc:
            deps.say(f"PropellerAds: not configured ({exc})")
    return deps


# --- 1. products + content ------------------------------------------------


def _curated_candidates(deps: Deps, niche: Niche) -> tuple[list, bool]:
    """Products for an imported (ASIN-list) site: live from the Creators API,
    or — if the API refuses — built from the pasted page. Returns
    (products, offline)."""
    if deps.catalog is not None:
        deps.say(f"[{niche.slug}] fetching {len(niche.asins)} imported products from Amazon")
        try:
            products = deps.catalog.get(list(niche.asins))
        except CatalogError as exc:
            deps.say(f"[{niche.slug}] Amazon API unavailable: {exc}")
        else:
            for p in products:
                p.epc = niche.asins.get(p.asin)
            return products, False
    return [], True


def refresh_niche(deps: Deps, niche: Niche) -> bool:
    s, store = deps.settings, deps.store
    if deps.catalog is None and not niche.asins:
        deps.say(f"[{niche.slug}] skipped: Amazon API not configured")
        return False

    # An imported list was already hand-picked, so it gets a bigger shelf.
    limit = max(s.products_per_site, CURATED_SITE_SIZE) if niche.asins else s.products_per_site
    candidates: list = []
    offline = False
    if niche.asins:
        candidates, offline = _curated_candidates(deps, niche)
    else:
        deps.say(f"[{niche.slug}] searching Amazon for {niche.keywords!r}")
        for page in range(1, SEARCH_PAGES + 1):
            try:
                found = deps.catalog.search(
                    niche.keywords, niche.search_index, niche.max_price, page
                )
            except CatalogError as exc:
                deps.say(f"[{niche.slug}] search page {page} failed: {exc}")
                break
            candidates.extend(found)
            if len(found) < 10:
                break

    if offline:
        if not s.amazon_partner_tag:
            deps.say(f"[{niche.slug}] fallback skipped: AMAZON_PARTNER_TAG is not set")
            return False
        selected, rejected = offline_products(
            niche.asins, niche.asin_meta, s.amazon_partner_tag, s.amazon_country,
            s.min_rating, s.min_reviews, limit,
        )
        deps.say(
            f"[{niche.slug}] fallback mode (no API): site built from the pasted page, "
            f"without photos and prices — {len(selected)} selected, {len(rejected)} rejected"
        )
    else:
        selected, rejected = select_products(candidates, limit, s.min_rating, s.min_reviews)
        deps.say(
            f"[{niche.slug}] {len(candidates)} found, {len(selected)} selected, "
            f"{len(rejected)} rejected"
        )
    for asin, reason in list(rejected.items())[:10]:
        deps.say(f"[{niche.slug}]   rejected {asin}: {reason}")
    if not selected:
        # Keep the current site as it is rather than emptying it.
        return False

    if not offline:
        # Copy written from a fallback card lacks the API's feature
        # bullets: rewrite it now that real data is here.
        was_offline = {p.asin for p, _ in store.list_products(niche.id, active_only=False)
                       if p.offline}
        for p in selected:
            if p.asin in was_offline:
                store.clear_product_copy(niche.id, p.asin)
    elif any(not p.offline for p, _ in store.list_products(niche.id)):
        # Never downgrade a site that already has full API pages.
        deps.say(f"[{niche.slug}] keeping the existing full pages from the last API fetch")
        return True
    store.replace_products(niche.id, [(p, score(p)) for p in selected])

    if deps.llm is None:
        deps.say(f"[{niche.slug}] copy skipped: LLM not configured")
        return True
    try:
        if store.get_site_copy(niche.id) is None:
            store.set_site_copy(niche.id, write_site_copy(deps.llm, niche.keywords, niche.language))
        # New products, plus copy written before the current format
        # (e.g. without buying tips) — rewritten once.
        missing = [p for p, copy in store.list_products(niche.id)
                   if copy is None or copy.version < COPY_VERSION]
        if missing:
            copies = write_product_copy(deps.llm, missing, niche.language)
            for asin, copy in copies.items():
                store.set_product_copy(niche.id, asin, copy)
            deps.say(f"[{niche.slug}] wrote copy for {len(copies)}/{len(missing)} products")
    except LLMError as exc:
        deps.say(f"[{niche.slug}] copy generation failed: {exc}")
    return True


# --- 2. campaigns ---------------------------------------------------------


def stop_campaign(deps: Deps, campaign: dict, status: str, reason: str) -> None:
    if campaign["status"] == ACTIVE and deps.push and campaign["external_id"]:
        try:
            deps.push.stop([campaign["external_id"]])
        except PropellerError as exc:
            deps.say(f"campaign #{campaign['id']}: stop failed, will retry: {exc}")
            return
    deps.store.update_campaign(campaign["id"], status=status, note=reason)
    deps.say(f"campaign #{campaign['id']} ({campaign['asin']}) stopped: {reason}")


def enforce_budget_cap(deps: Deps) -> None:
    """If the daily cap was lowered below what's running, stop the newest
    campaigns until the total fits again."""
    cap = deps.settings.max_daily_spend
    for c in deps.store.list_campaigns(statuses=(ACTIVE,)):  # newest first
        if deps.store.running_daily_budget() <= cap:
            return
        stop_campaign(deps, c, STOPPED, f"over the daily cap of ${cap:.2f}")


def apply_daily_budget(deps: Deps, budget: float) -> int:
    """Set the daily budget of every running campaign (after it was changed
    in the dashboard). Returns how many were updated."""
    updated = 0
    for c in deps.store.list_campaigns(statuses=(ACTIVE, DRY_RUN)):
        if c["status"] == ACTIVE and deps.push and c["external_id"]:
            try:
                deps.push.update_campaign(c["external_id"], {"daily_amount": round(budget, 2)})
            except PropellerError as exc:
                deps.say(f"campaign #{c['id']}: budget update failed: {exc}")
                continue
        deps.store.update_campaign(c["id"], daily_budget=budget)
        updated += 1
    return updated


def fix_tracking_urls(deps: Deps) -> None:
    """Campaigns launched with a zone macro PropellerAds doesn't substitute
    get their target URL switched to the working one (once)."""
    if deps.push is None:
        return
    for c in deps.store.list_campaigns(statuses=(ACTIVE,)):
        payload = json.loads(c["payload"] or "{}")
        url = payload.get("target_url", "")
        if not c["external_id"] or not any(m in url for m in propeller.OLD_ZONE_MACROS):
            continue
        new_url = url
        for old in propeller.OLD_ZONE_MACROS:
            new_url = new_url.replace(old, propeller.ZONE_MACRO)
        try:
            deps.push.update_target_url(c["external_id"], new_url)
        except PropellerError as exc:
            deps.say(f"campaign #{c['id']}: tracking URL update failed: {exc}")
            continue
        deps.store.update_campaign(c["id"], payload={**payload, "target_url": new_url})
        deps.say(f"campaign #{c['id']}: tracking URL updated to report zone ids")


def sync_moderation(deps: Deps) -> None:
    """Mirror PropellerAds' own status: a campaign rejected by moderation
    frees its slot and its product is not tried again."""
    if deps.push is None:
        return
    for c in deps.store.list_campaigns(statuses=(ACTIVE,)):
        if not c["external_id"]:
            continue
        try:
            status = deps.push.campaign_status(c["external_id"])
        except PropellerError as exc:
            deps.say(f"campaign #{c['id']}: status check failed: {exc}")
            continue
        name = propeller.API_STATUS_NAMES.get(status, str(status))
        if status == propeller.API_STATUS_REJECTED:
            deps.store.update_campaign(c["id"], status=KILLED,
                                       note="rejected by PropellerAds moderation")
            deps.say(f"campaign #{c['id']} ({c['asin']}) was rejected by moderation")
        elif status is not None and f"PropellerAds: {name}" != c["note"]:
            deps.store.update_campaign(c["id"], note=f"PropellerAds: {name}")


def sync_stats(deps: Deps) -> None:
    """Pull impressions / clicks / spend per campaign and per zone from
    PropellerAds into the store (what the dashboard shows and the kill and
    zone rules read)."""
    active = [c for c in deps.store.list_campaigns(statuses=(ACTIVE,)) if c["external_id"]]
    if not active or deps.push is None:
        return
    by_external = {c["external_id"]: c for c in active}
    try:
        totals = deps.push.spend(list(by_external), days=365)
        zones = deps.push.spend(list(by_external), days=365, by_zone=True)
    except PropellerError as exc:
        deps.say(f"stats sync failed: {exc}")
        return
    for row in totals:
        c = by_external.get(row["campaign_id"])
        if c:
            deps.store.update_campaign(c["id"], spend=row["spent"], impressions=row["impressions"],
                                       ad_clicks=row["clicks"], stats_at=now_iso())
    for row in zones:
        c = by_external.get(row["campaign_id"])
        if c and row["zone_id"]:
            deps.store.upsert_zone_stats(c["id"], row["zone_id"], row["impressions"],
                                         row["clicks"], row["spent"])


# kept for callers/tests that still use the old name
sync_spend = sync_stats


def exclude_zone(deps: Deps, campaign_id: int, zone: str, reason: str = "manual") -> str | None:
    """Stop showing a campaign in one zone. Returns an error message or None."""
    c = deps.store.get_campaign(campaign_id)
    if c is None:
        return "campaign not found"
    if c["status"] == ACTIVE and c["external_id"]:
        if deps.push is None:
            return "PropellerAds is not configured"
        try:
            deps.push.exclude_zones(c["external_id"], [zone])
        except PropellerError as exc:
            return str(exc)
    deps.store.blacklist_zone(campaign_id, zone)
    deps.say(f"campaign #{campaign_id}: zone {zone} excluded ({reason})")
    return None


def include_zone(deps: Deps, campaign_id: int, zone: str) -> str | None:
    """Undo exclude_zone: re-send the exclude list without this zone."""
    c = deps.store.get_campaign(campaign_id)
    if c is None:
        return "campaign not found"
    remaining = sorted(deps.store.blacklisted_zones(campaign_id) - {zone})
    if c["status"] == ACTIVE and c["external_id"]:
        if deps.push is None:
            return "PropellerAds is not configured"
        try:
            deps.push.set_excluded_zones(c["external_id"], remaining)
        except PropellerError as exc:
            return str(exc)
    deps.store.unblacklist_zone(campaign_id, zone)
    deps.say(f"campaign #{campaign_id}: zone {zone} re-enabled")
    return None


def apply_kill_rules(deps: Deps) -> None:
    s, store = deps.settings, deps.store
    for c in store.list_campaigns(statuses=(ACTIVE,)):
        if store.get_product(c["niche_id"], c["asin"]) is None:
            stop_campaign(deps, c, STOPPED, "product removed")
            continue
        niche = store.get_niche(c["niche_id"])
        if niche is None or not niche.enabled:
            stop_campaign(deps, c, STOPPED, "site disabled")
            continue
        active_asins = {p.asin for p, _ in store.list_products(c["niche_id"])}
        if c["asin"] not in active_asins:
            stop_campaign(deps, c, STOPPED, "product no longer selected")
            continue
        if c["spend"] < s.kill_min_spend:
            continue
        clicks = store.count_events(c["id"], "click")
        cost = c["spend"] / clicks if clicks else None
        if cost is None or cost > s.max_cost_per_amazon_click:
            shown = f"${cost:.2f}" if cost is not None else "no clicks"
            stop_campaign(deps, c, KILLED,
                  f"spent ${c['spend']:.2f}, cost per Amazon click {shown} "
                  f"> ${s.max_cost_per_amazon_click:.2f}")


def blacklist_bad_zones(deps: Deps) -> None:
    """Exclude zones that spent zone_min_spend without a single click
    through to Amazon (reads the zone stats sync_stats stored)."""
    if deps.push is None:
        return
    s, store = deps.settings, deps.store
    active = [c for c in store.list_campaigns(statuses=(ACTIVE,)) if c["external_id"]]
    # Only judge zones where our own visit log carries zone ids: if the
    # zone macro in the target URL isn't substituted, every zone would look
    # like it had zero clicks and all of them would get blacklisted.
    tracked = [c for c in active if store.events_by_zone(c["id"], "visit")]
    for c in tracked:
        clicks = store.events_by_zone(c["id"], "click")
        excluded = store.blacklisted_zones(c["id"])
        for z in store.zone_stats(c["id"]):
            if z["spent"] < s.zone_min_spend or z["zone"] in excluded:
                continue
            if clicks.get(z["zone"], 0) == 0:
                error = exclude_zone(deps, c["id"], z["zone"],
                                     f"spent ${z['spent']:.2f}, no Amazon clicks")
                if error:
                    deps.say(f"campaign #{c['id']}: zone blacklist failed: {error}")


def launch_campaigns(deps: Deps) -> None:
    s, store = deps.settings, deps.store
    live = s.push_live
    if live and deps.push is None:
        deps.say("launch skipped: PUSH_LIVE=true but PropellerAds is not configured")
        return
    running_status = ACTIVE if live else DRY_RUN

    for niche in store.list_niches():
        if not niche.enabled:
            continue
        site_copy = store.get_site_copy(niche.id)
        if site_copy is None:
            continue
        campaigns = store.list_campaigns(niche_id=niche.id)
        running = [c for c in campaigns if c["status"] == running_status]
        taken = {c["asin"] for c in campaigns if c["status"] in (running_status, KILLED)}
        slots = s.campaigns_per_site - len(running)

        for product, copy in store.list_products(niche.id):
            if slots <= 0:
                break
            if product.asin in taken or copy is None:
                continue
            if live and store.running_daily_budget() + s.campaign_daily_budget > s.max_daily_spend:
                deps.say(
                    f"launch paused: daily budget cap ${s.max_daily_spend:.2f} reached "
                    f"(running ${store.running_daily_budget():.2f})"
                )
                return

            cid = store.add_campaign(niche.id, product.asin, "creating", s.campaign_daily_budget)
            payload = build_payload(deps, niche, site_copy, product, copy, cid)

            if not live:
                store.update_campaign(cid, status=DRY_RUN, payload=payload,
                                      note="dry run: not sent (PUSH_LIVE=false)")
                deps.say(f"[{niche.slug}] dry-run campaign #{cid} for {product.asin}")
            else:
                try:
                    # Created straight into moderation; it starts once approved.
                    external_id = deps.push.create_campaign(
                        propeller.inline_images(payload, lambda url: media_file(deps, url))
                    )
                except PropellerError as exc:
                    store.update_campaign(cid, status=ERROR, payload=payload, note=str(exc)[:500])
                    deps.say(f"[{niche.slug}] campaign #{cid} failed: {exc}")
                    continue
                store.update_campaign(cid, status=ACTIVE, external_id=external_id,
                                      payload=payload, note="sent to PropellerAds moderation")
                deps.say(
                    f"[{niche.slug}] launched campaign #{cid} (PropellerAds {external_id}) "
                    f"for {product.asin}, ${s.campaign_daily_budget:.2f}/day"
                )
            slots -= 1


def push_images(deps: Deps, niche: Niche, site_title: str, product, copy, cid: int
                ) -> list[tuple[str, str]]:
    """Public (icon URL, image URL) pairs for a campaign's creatives: AI
    drawings when available, else the simple generated ones."""
    s = deps.settings
    out_dir = Path(s.data_dir) / "media" / niche.slug
    base = f"{s.public_base_url()}/media/{niche.slug}"
    name = f"{product.asin}-c{cid}"
    if deps.painter is not None and deps.llm is not None:
        try:
            files = make_ai_creatives(deps.llm, deps.painter, product, copy, out_dir, name,
                                      max(1, s.push_creative_variants))
        except (CreativeError, LLMError) as exc:
            deps.say(f"[{niche.slug}] AI images failed for #{cid}, using simple ones: {exc}")
        else:
            deps.say(f"[{niche.slug}] drew {len(files)} AI image variant(s) for #{cid}")
            return [(f"{base}/{icon}", f"{base}/{image}") for icon, image in files]
    render_creatives(out_dir, name, site_title, copy.push_title)
    return [(f"{base}/{name}-icon.png", f"{base}/{name}-image.png")]


def media_file(deps: Deps, url: str) -> Path | None:
    """Local file behind one of our public /media/ URLs."""
    prefix = f"{deps.settings.public_base_url()}/media/"
    if not url.startswith(prefix):
        return None
    root = (Path(deps.settings.data_dir) / "media").resolve()
    path = (root / url[len(prefix):]).resolve()
    return path if root in path.parents and path.is_file() else None


def build_payload(deps: Deps, niche: Niche, site_copy, product, copy, cid: int) -> dict:
    s = deps.settings
    return propeller.build_campaign_payload(
        name=f"{niche.slug} {product.asin} #{cid}",
        target_url=(
            f"{s.public_base_url()}/s/{niche.slug}/p/{product.asin}"
            f"?c={cid}&z={propeller.ZONE_MACRO}&k={propeller.CLICK_MACRO}"
        ),
        title=copy.push_title,
        text=copy.push_text,
        images=push_images(deps, niche, site_copy.site_title, product, copy, cid),
        countries=s.push_countries_list(),
        bid_cpc=s.push_bid_cpc,
        daily_budget=s.campaign_daily_budget,
    )


def redraw_campaign(deps: Deps, campaign_id: int) -> bool:
    """Re-make the creatives of a dry-run campaign (e.g. after switching on
    AI images). Live campaigns are left alone: their creatives are already
    under review at the ad network."""
    c = deps.store.get_campaign(campaign_id)
    if c is None or c["status"] != DRY_RUN:
        return False
    niche = deps.store.get_niche(c["niche_id"])
    site_copy = deps.store.get_site_copy(c["niche_id"]) if niche else None
    found = deps.store.get_product(c["niche_id"], c["asin"]) if niche else None
    if not (niche and site_copy and found and found[1]):
        return False
    product, copy = found
    deps.store.update_campaign(
        campaign_id, payload=build_payload(deps, niche, site_copy, product, copy, campaign_id)
    )
    return True


def stop_all(deps: Deps, reason: str = "stopped by kill switch") -> None:
    for c in deps.store.list_campaigns(statuses=(ACTIVE, DRY_RUN)):
        stop_campaign(deps, c, STOPPED, reason)


def manage_campaigns(deps: Deps) -> None:
    if deps.store.get_flag(PAUSE_FLAG) == "1":
        deps.say("kill switch is on: stopping everything, launching nothing")
        stop_all(deps)
        return
    if deps.settings.push_live:
        # Dry-run rows never reached PropellerAds; retire them so their
        # products get real campaigns.
        for c in deps.store.list_campaigns(statuses=(DRY_RUN,)):
            deps.store.update_campaign(c["id"], status=STOPPED, note="dry run (never sent)")
    enforce_budget_cap(deps)
    fix_tracking_urls(deps)
    sync_moderation(deps)
    sync_stats(deps)
    apply_kill_rules(deps)
    blacklist_bad_zones(deps)
    launch_campaigns(deps)


# --- entrypoints ----------------------------------------------------------

_run_lock = threading.Lock()


def add_discovered_niches(deps: Deps, count: int) -> int:
    """Let the agent pick `count` new niches itself. Returns how many it added."""
    if count <= 0:
        return 0
    if deps.llm is None or deps.catalog is None:
        deps.say("niche discovery skipped: needs both the Amazon API and an LLM")
        return 0
    s = deps.settings
    # One cheap probe before spending an LLM call on ideas: while Amazon
    # refuses access (e.g. AssociateNotEligible), every idea would fail.
    try:
        deps.catalog.search("gift ideas", "All")
    except CatalogError as exc:
        deps.say(f"niche discovery paused until the Amazon API answers: {exc}")
        return 0
    existing = [n.keywords for n in deps.store.list_niches()]
    try:
        winners = discover_niches(
            deps.llm, deps.catalog, count, existing, s.amazon_country,
            s.min_rating, s.min_reviews, deps.say,
        )
    except LLMError as exc:
        deps.say(f"niche discovery failed: {exc}")
        return 0
    for c in winners:
        deps.store.add_niche(c.keywords, c.search_index)
        deps.say(f"niche discovery: added {c.keywords!r} ({c.search_index})")
    if not winners:
        deps.say("niche discovery: no idea passed the Amazon checks this time")
    return len(winners)


def quick_check(deps: Deps) -> bool:
    """Campaign management only (stats, kill rules, zones, refill freed
    slots) — run every few minutes so a campaign is judged soon after it
    reaches KILL_MIN_SPEND instead of at the next full cycle. Skipped while
    a full cycle runs. The journal only gets an entry if something happened."""
    if not _run_lock.acquire(blocking=False):
        return False
    try:
        manage_campaigns(deps)
    except Exception as exc:
        logger.exception("quick check failed")
        deps.say(f"quick check: unexpected error: {exc}")
    finally:
        _run_lock.release()
    if deps.log:
        run_id = deps.store.start_run(None)
        for line in deps.log:
            deps.store.append_run_log(run_id, line)
        deps.store.finish_run(run_id, True)
    return True


def run_cycle(
    deps: Deps, niche_id: int | None = None, discover: int = 0, wait: bool = False
) -> bool:
    """One full cycle. Returns False if another cycle was running and
    `wait` is off (with `wait`, queue behind it for up to an hour).

    `discover` asks the agent to find that many new niches first. Without
    it, the agent still tops the site count up to AUTO_NICHES by itself.
    """
    acquired = _run_lock.acquire(timeout=3600) if wait else _run_lock.acquire(blocking=False)
    if not acquired:
        return False
    ok = True
    deps.run_id = deps.store.start_run(niche_id)
    for line in deps.log:  # setup messages from build_deps
        deps.store.append_run_log(deps.run_id, line)
    try:
        if niche_id is None:
            enabled = sum(1 for n in deps.store.list_niches() if n.enabled)
            missing = max(discover, deps.settings.auto_niches - enabled)
            try:
                add_discovered_niches(deps, missing)
            except Exception as exc:
                ok = False
                logger.exception("niche discovery failed")
                deps.say(f"niche discovery: unexpected error: {exc}")
        niches = deps.store.list_niches()
        if niche_id is not None:
            niches = [n for n in niches if n.id == niche_id]
        for niche in niches:
            if not niche.enabled:
                continue
            try:
                refresh_niche(deps, niche)
            except Exception as exc:  # never let one niche kill the loop
                ok = False
                logger.exception("niche %s failed", niche.slug)
                deps.say(f"[{niche.slug}] unexpected error: {exc}")
        try:
            manage_campaigns(deps)
        except Exception as exc:
            ok = False
            logger.exception("campaign management failed")
            deps.say(f"campaign management: unexpected error: {exc}")
    finally:
        deps.store.finish_run(deps.run_id, ok)
        _run_lock.release()
    return True
