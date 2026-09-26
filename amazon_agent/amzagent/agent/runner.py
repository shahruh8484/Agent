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
from amzagent.push import propeller
from amzagent.push.creatives import render_creatives
from amzagent.push.propeller import PropellerClient, PropellerError
from amzagent.selection.niches import discover_niches
from amzagent.selection.selector import score, select_products
from amzagent.store import ACTIVE, DRY_RUN, ERROR, STOPPED, Store

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
    log: list[str] = field(default_factory=list)
    run_id: int | None = None

    def say(self, msg: str) -> None:
        logger.info(msg)
        self.log.append(msg)
        if self.run_id is not None:
            # Written as it happens so the dashboard shows a long run live.
            self.store.append_run_log(self.run_id, msg)


def build_deps(settings: Settings, store: Store) -> Deps:
    deps = Deps(settings=settings, store=store)
    try:
        deps.catalog = CreatorsApiCatalog(settings)
    except CatalogError as exc:
        deps.say(f"Amazon: not configured ({exc})")
    try:
        deps.llm = get_llm(settings)
    except LLMError as exc:
        deps.say(f"LLM: not configured ({exc})")
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


def sync_spend(deps: Deps) -> None:
    active = deps.store.list_campaigns(statuses=(ACTIVE,))
    if not active or deps.push is None:
        return
    by_external = {c["external_id"]: c for c in active if c["external_id"]}
    try:
        rows = deps.push.spend(list(by_external), days=365)
    except PropellerError as exc:
        deps.say(f"spend sync failed: {exc}")
        return
    for row in rows:
        campaign = by_external.get(row["campaign_id"])
        if campaign:
            deps.store.update_campaign(campaign["id"], spend=row["spent"])


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
    if deps.push is None:
        return
    s, store = deps.settings, deps.store
    active = [c for c in store.list_campaigns(statuses=(ACTIVE,)) if c["external_id"]]
    if not active:
        return
    try:
        rows = deps.push.spend([c["external_id"] for c in active], days=30, by_zone=True)
    except PropellerError as exc:
        deps.say(f"zone stats failed: {exc}")
        return
    by_external = {c["external_id"]: c for c in active}
    bad: dict[int, list[str]] = {}
    for row in rows:
        c = by_external.get(row["campaign_id"])
        if not c or not row["zone_id"] or row["spent"] < s.zone_min_spend:
            continue
        no_clicks = store.events_by_zone(c["id"], "click").get(row["zone_id"], 0) == 0
        if no_clicks and row["zone_id"] not in store.blacklisted_zones(c["id"]):
            bad.setdefault(c["id"], []).append(row["zone_id"])
    for campaign_id, zones in bad.items():
        c = store.get_campaign(campaign_id)
        try:
            deps.push.exclude_zones(c["external_id"], zones)
        except PropellerError as exc:
            deps.say(f"campaign #{campaign_id}: zone blacklist failed: {exc}")
            continue
        for z in zones:
            store.blacklist_zone(campaign_id, z)
        deps.say(f"campaign #{campaign_id}: blacklisted {len(zones)} zones with spend and no clicks")


def launch_campaigns(deps: Deps) -> None:
    s, store = deps.settings, deps.store
    live = s.push_live
    if live and deps.push is None:
        deps.say("launch skipped: PUSH_LIVE=true but PropellerAds is not configured")
        return
    running_status = ACTIVE if live else DRY_RUN
    media_root = Path(s.data_dir) / "media"
    base = s.public_base_url()

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
            name = f"{product.asin}-c{cid}"
            render_creatives(media_root / niche.slug, name, site_copy.site_title, copy.push_title)
            payload = propeller.build_campaign_payload(
                name=f"{niche.slug} {product.asin} #{cid}",
                target_url=(
                    f"{base}/s/{niche.slug}/p/{product.asin}"
                    f"?c={cid}&z={propeller.ZONE_MACRO}&k={propeller.CLICK_MACRO}"
                ),
                title=copy.push_title,
                text=copy.push_text,
                icon_url=f"{base}/media/{niche.slug}/{name}-icon.png",
                image_url=f"{base}/media/{niche.slug}/{name}-image.png",
                countries=s.push_countries_list(),
                bid_cpc=s.push_bid_cpc,
                daily_budget=s.campaign_daily_budget,
            )

            if not live:
                store.update_campaign(cid, status=DRY_RUN, payload=payload,
                                      note="dry run: not sent (PUSH_LIVE=false)")
                deps.say(f"[{niche.slug}] dry-run campaign #{cid} for {product.asin}")
            else:
                try:
                    external_id = deps.push.create_campaign(payload)
                    deps.push.start([external_id])
                except PropellerError as exc:
                    store.update_campaign(cid, status=ERROR, payload=payload, note=str(exc)[:500])
                    deps.say(f"[{niche.slug}] campaign #{cid} failed: {exc}")
                    continue
                store.update_campaign(cid, status=ACTIVE, external_id=external_id,
                                      payload=payload, note="")
                deps.say(
                    f"[{niche.slug}] launched campaign #{cid} (PropellerAds {external_id}) "
                    f"for {product.asin}, ${s.campaign_daily_budget:.2f}/day"
                )
            slots -= 1


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
    sync_spend(deps)
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
