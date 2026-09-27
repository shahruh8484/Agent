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

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from amzagent.amazon.catalog import Catalog, CatalogError, CreatorsApiCatalog
from amzagent.amazon.creator_connections import accepted_link, marketplace_host, offline_products
from amzagent.config import Settings
from amzagent.content.llm import LLM, LLMError, get_llm
from amzagent.content.writer import uses_amazon_marks, write_product_copy, write_site_copy
from amzagent.content.sections import OTHER as SECTIONS_OTHER
from amzagent.content.sections import PLAN_VERSION, group_products, write_article, write_guide
from amzagent.models import COPY_VERSION, Niche, SitePlan
from amzagent.panel_settings import effective, save_overrides
from amzagent.push import propeller
from amzagent.push.ai_creatives import (
    IMAGE_RULES,
    CreativeError,
    OpenAIImages,
    describe_scenes,
    make_ai_creatives,
    save_site_illustration,
)
from amzagent.push.creatives import render_creatives
from amzagent.push.propeller import PropellerClient, PropellerError
from amzagent.selection.niches import discover_niches
from amzagent.selection.selector import score, select_products
from amzagent.store import ACTIVE, DRY_RUN, ERROR, STOPPED, Store, now_iso

logger = logging.getLogger(__name__)

KILLED = "killed"
CREATING = "creating"  # row exists, creatives/API call still in progress
CAPPED = "capped"  # paused by the agent: 24h spend limit reached; resumes by itself
PACED = "paced"  # paused by the agent: ahead of its daily budget schedule; resumes by itself
PACE_LEAD_MINUTES = 60  # a campaign may run this far ahead of an even schedule
RESUME_HEADROOM = 1.0  # $ left under the 24h limit before capped campaigns resume
STUCK_CREATING_MINUTES = 30
STATS_BLIND_MINUTES = 30
# Share of paid push clicks that reach our page (measured: 298 of 338).
VISITS_PER_PAID_CLICK = 0.85
STATS_OK_FLAG = "stats_ok_at"
STATS_ERROR_FLAG = "stats_error"
ZONE_STATS_FLAG = "zone_stats_at"
STATS_KEEP_DAYS = 30  # stopped campaigns keep getting their late spend synced this long
# Per-zone stats are the heaviest statistics call; totals are enough for
# the 3-minute kill/limit checks, so zones are pulled less often.
ZONE_STATS_EVERY_MINUTES = 15
TODAY_SPEND_FLAG = "spent_24h"
TODAY_SPEND_MAX_AGE_MINUTES = 20  # stopped by the kill rule — never relaunched for that product
PAUSE_FLAG = "paused_all"
NEXT_CYCLE_FLAG = "next_cycle_at"
# A campaign we run that PropellerAds itself paused, usually "Daily
# impressions": near its daily budget it waits for late clicks and then
# restarts by itself.
NETWORK_PAUSED_NOTE = "PropellerAds: paused"  # when the background loop runs the next full cycle
# "1" = manual mode: the agent keeps sites, stats and money safety going but
# makes no campaign decisions (no launches, no kills by results, no zone
# exclusions, no new niches) — the owner does that from the panel or chat.
MANUAL_FLAG = "manual_mode"
SEARCH_PAGES = 2  # 10 items per page


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

    def say_once(self, key: str, msg: str, every: timedelta = timedelta(hours=1)) -> None:
        """Like say, but a message that keeps repeating (e.g. "launch paused"
        every 3 minutes) reaches the journal at most once per `every`."""
        flag = f"said:{key}"
        try:
            last = datetime.fromisoformat(self.store.get_flag(flag))
        except ValueError:
            last = None
        now = datetime.now(timezone.utc)
        if last and now - last < every:
            logger.info(msg)
            return
        self.store.set_flag(flag, now.isoformat(timespec="seconds"))
        self.say(msg)

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
            host = marketplace_host(deps.settings.amazon_country)
            for p in products:
                meta = niche.asin_meta.get(p.asin) or {}
                p.epc = niche.asins.get(p.asin)
                p.cc_budget = meta.get("budget") or ""
                p.hint_rating = meta.get("rating")
                p.hint_reviews = int(meta.get("reviews") or 0)
                if deps.settings.amazon_partner_tag:
                    # Same link as "Get associate link" on the accepted campaign.
                    p.url = accepted_link(host, p.asin, deps.settings.amazon_partner_tag)
            return products, False
    return [], True


def refresh_niche(deps: Deps, niche: Niche) -> bool:
    s, store = deps.settings, deps.store
    if deps.catalog is None and not niche.asins:
        deps.say(f"[{niche.slug}] skipped: Amazon API not configured")
        return False

    # An imported list was already hand-picked, so it gets its own (bigger) shelf.
    limit = s.import_site_size if niche.asins else s.products_per_site
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
        # bullets: rewrite it now that real data is here — keeping the old
        # copy (and so the page) until the new one is written.
        was_offline = {p.asin for p, _ in store.list_products(niche.id, active_only=False)
                       if p.offline}
        for p in selected:
            if p.asin in was_offline:
                store.mark_copy_stale(niche.id, p.asin)
    elif any(not p.offline for p, _ in store.list_products(niche.id)):
        # Never downgrade a site that already has full API pages.
        deps.say(f"[{niche.slug}] keeping the existing full pages from the last API fetch")
        return True
    # Keep illustrations already drawn for these products.
    drawn = {p.asin: p.illustration_url
             for p, _ in store.list_products(niche.id, active_only=False) if p.illustration_url}
    for p in selected:
        if not p.image_url and not p.illustration_url:
            p.illustration_url = drawn.get(p.asin, "")
    store.replace_products(niche.id, [(p, score(p)) for p in selected])

    if deps.llm is None:
        deps.say(f"[{niche.slug}] copy skipped: LLM not configured")
        return True
    try:
        site = store.get_site_copy(niche.id)
        if site is None or uses_amazon_marks(site):  # e.g. an old "Prime …" name
            store.set_site_copy(niche.id, write_site_copy(deps.llm, niche.keywords, niche.language))
            if site is not None:
                deps.say(f"[{niche.slug}] renamed the site: {site.site_title!r} used an Amazon "
                         f"trademark")
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
    update_site_plan(deps, niche)
    illustrate_products(deps, niche)
    return True


CHECK_LOG_DAYS = 3  # 3-minute check entries are kept this long in the journal
SITE_PLAN_MIN_HOURS = 12  # rebuild sections/guides at most this often


def update_site_plan(deps: Deps, niche: Niche) -> None:
    """Group the site's products into sections with buying guides. Rebuilt
    when the product set changed (at most every SITE_PLAN_MIN_HOURS; until
    then new products show under "More Picks")."""
    if deps.llm is None:
        return
    items = [(p, c) for p, c in deps.store.list_products(niche.id) if c]
    if len(items) < 4:
        return  # too few to be worth sections
    signature = hashlib.sha1(",".join(sorted(p.asin for p, _ in items)).encode()).hexdigest()
    plan = deps.store.get_site_plan(niche.id)
    outdated = plan is not None and plan.version < PLAN_VERSION  # e.g. guides without FAQ
    if plan and plan.signature == signature and not outdated:
        return
    if plan and plan.built_at and not outdated:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(plan.built_at)
        if age < timedelta(hours=SITE_PLAN_MIN_HOURS):
            return
    try:
        sections = group_products(deps.llm, items, niche.language)
    except LLMError as exc:
        deps.say(f"[{niche.slug}] sections failed: {exc}")
        return
    by_asin = {p.asin: (p, c) for p, c in items}
    guides = 0
    for section in sections:
        if section.name == SECTIONS_OTHER:
            continue
        try:
            write_guide(deps.llm, section, by_asin, niche.language)
            guides += 1
        except LLMError as exc:
            deps.say(f"[{niche.slug}] guide for {section.name!r} failed: {exc}")
            continue
        try:
            section.article = write_article(deps.llm, section, niche.language)
        except LLMError as exc:
            deps.say(f"[{niche.slug}] advice article for {section.name!r} failed: {exc}")
    deps.store.set_site_plan(niche.id, SitePlan(version=PLAN_VERSION, signature=signature,
                                                built_at=now_iso(), sections=sections))
    deps.say(f"[{niche.slug}] organised {len(items)} products into {len(sections)} sections, "
             f"{guides} buying guides")


def illustrate_products(deps: Deps, niche: Niche) -> int:
    """Draw a labelled illustration for products without an Amazon photo."""
    s = deps.settings
    if not s.site_illustrations or deps.painter is None or deps.llm is None:
        return 0
    todo = [(p, c) for p, c in deps.store.list_products(niche.id)
            if c is not None and not p.image_url and not p.illustration_url]
    out_dir = Path(s.data_dir) / "media" / niche.slug
    base = f"{s.public_base_url()}/media/{niche.slug}"
    done = 0
    for product, copy in todo[: max(0, s.illustrations_per_run)]:
        try:
            scene = describe_scenes(deps.llm, product, copy, 1)[0]
            path = save_site_illustration(deps.painter.draw(scene + IMAGE_RULES), out_dir,
                                          f"site-{product.asin}")
        except (CreativeError, LLMError, OSError, ValueError) as exc:
            deps.say(f"[{niche.slug}] illustration for {product.asin} failed: {exc}")
            continue
        product.illustration_url = f"{base}/{path.name}"
        deps.store.update_product_data(niche.id, product)
        done += 1
    if done:
        deps.say(f"[{niche.slug}] drew {done} product illustration(s)"
                 + (f", {len(todo) - done} left for later" if len(todo) > done else ""))
    return done


# --- 2. campaigns ---------------------------------------------------------


def stop_campaign(deps: Deps, campaign: dict, status: str, reason: str) -> None:
    """Stop at PropellerAds and record why. Clears the manual-keep mark."""
    if campaign["status"] == ACTIVE and deps.push and campaign["external_id"]:
        try:
            deps.push.stop([campaign["external_id"]])
        except PropellerError as exc:
            # The API refuses to stop a campaign it already paused/stopped
            # itself (or rejected); that's as good as stopped.
            try:
                api_status = deps.push.campaign_status(campaign["external_id"])
            except PropellerError:
                api_status = None
            if api_status not in propeller.API_STATUSES_NOT_RUNNING:
                deps.say(f"campaign #{campaign['id']}: stop failed, will retry: {exc}")
                return
    deps.store.update_campaign(campaign["id"], status=status, note=reason, manual_keep=0)
    deps.say(f"campaign #{campaign['id']} ({campaign['asin']}) stopped: {reason}")


def enforce_spend_cap(deps: Deps) -> None:
    """The 24h limit applies to money actually spent: once it's reached,
    pause every running campaign (status "capped"); when enough of the
    window has rolled off, resume them."""
    if deps.push is None:
        return
    spent = spent_today(deps.store)
    if spent is None:
        return
    cap = deps.settings.max_daily_spend
    if spent >= cap:
        for c in deps.store.list_campaigns(statuses=(ACTIVE,)):
            if not c["external_id"]:
                continue
            try:
                deps.push.stop([c["external_id"]])
            except PropellerError as exc:
                try:
                    api_status = deps.push.campaign_status(c["external_id"])
                except PropellerError:
                    api_status = None
                if api_status not in propeller.API_STATUSES_NOT_RUNNING:
                    deps.say(f"campaign #{c['id']}: pause at limit failed, will retry: {exc}")
                    continue
            deps.store.update_campaign(
                c["id"], status=CAPPED,
                note=f"paused: ${spent:.2f} spent in 24h >= limit ${cap:.2f}")
            deps.say(f"campaign #{c['id']} paused: 24h limit ${cap:.2f} reached "
                     f"(${spent:.2f} spent)")
    elif cap - spent >= RESUME_HEADROOM:
        for c in deps.store.list_campaigns(statuses=(CAPPED,)):
            try:
                deps.push.start([c["external_id"]])
            except PropellerError as exc:
                deps.say(f"campaign #{c['id']}: resume failed, will retry: {exc}")
                continue
            deps.store.update_campaign(c["id"], status=ACTIVE,
                                       note="resumed: back under the 24h limit")
            deps.say(f"campaign #{c['id']} resumed: ${spent:.2f} of ${cap:.2f} spent in 24h")


def resume_eta(store: Store, cap: float) -> datetime | None:
    """When campaigns paused at the 24h limit should run again: the moment
    enough of the last 24h's spend rolls out of the window. Spend is spread
    over time like our own ad visits were (PropellerAds only gives a 24h
    total). None if nothing is paused at the limit or it can't be told."""
    if not store.list_campaigns(statuses=(CAPPED,)):
        return None
    spent = spent_today(store)
    if spent is None:
        return None
    target = cap - RESUME_HEADROOM
    now = datetime.now(timezone.utc)
    if spent <= target:
        return now  # the next 3-minute check resumes them
    times = store.campaign_visit_times((now - timedelta(hours=24)).isoformat(timespec="seconds"))
    if not times:
        return None
    per_visit = spent / len(times)
    remaining = spent
    for ts in times:
        remaining -= per_visit
        if remaining <= target + 1e-9:
            return datetime.fromisoformat(ts) + timedelta(hours=24)
    return datetime.fromisoformat(times[-1]) + timedelta(hours=24)


def spent_since_budget_day(deps: Deps, c: dict, now: datetime | None = None) -> float:
    """Real-time estimate of what a campaign spent since 00:00 UTC (its
    budget day: campaigns run in UTC), from its site visits x bid;
    PropellerAds' own numbers lag up to an hour."""
    now = now or datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    visits = deps.store.count_events(c["id"], "visit", midnight.isoformat(timespec="seconds"))
    return visits * deps.settings.push_bid_cpc / VISITS_PER_PAID_CLICK


def pace_allowance(budget: float, now: datetime) -> float:
    """How much of a daily budget may be spent by `now`: an even share of
    the UTC day so far, plus PACE_LEAD_MINUTES of head start."""
    minutes = now.hour * 60 + now.minute + PACE_LEAD_MINUTES
    return budget * min(1.0, minutes / 1440)


def pace_campaigns(deps: Deps) -> None:
    """Spread each campaign's daily budget over the day: pause one that is
    ahead of an even schedule (status paced), resume it once time catches
    up — never above the 24h limit or with the kill switch on."""
    if deps.push is None:
        return
    s, store = deps.settings, deps.store
    now = datetime.now(timezone.utc)
    if s.pace_daily_budget:
        for c in store.list_campaigns(statuses=(ACTIVE,)):
            if not c["external_id"]:
                continue
            spent = spent_since_budget_day(deps, c, now)
            allowed = pace_allowance(c["daily_budget"], now)
            if spent <= allowed:
                continue
            try:
                deps.push.stop([c["external_id"]])
            except PropellerError as exc:
                deps.say(f"campaign #{c['id']}: pacing pause failed, will retry: {exc}")
                continue
            store.update_campaign(c["id"], status=PACED,
                                  note=f"paced: ${spent:.2f} of ${c['daily_budget']:.0f} spent by "
                                       f"{now:%H:%M} UTC, ahead of schedule")
            deps.say(f"campaign #{c['id']} paced: ${spent:.2f} spent today by {now:%H:%M} UTC "
                     f"(schedule allows ${allowed:.2f})")
    spent_24h = spent_today(store)
    room = spent_24h is not None and s.max_daily_spend - spent_24h >= RESUME_HEADROOM
    for c in store.list_campaigns(statuses=(PACED,)):
        spent = spent_since_budget_day(deps, c, now)
        # Resume a little below the line so it doesn't flap every check.
        behind = spent <= pace_allowance(c["daily_budget"], now) - c["daily_budget"] / 48
        if s.pace_daily_budget and not behind:
            continue
        if not room or store.get_flag(PAUSE_FLAG) == "1":
            continue
        try:
            deps.push.start([c["external_id"]])
        except PropellerError as exc:
            deps.say(f"campaign #{c['id']}: pacing resume failed, will retry: {exc}")
            continue
        store.update_campaign(c["id"], status=ACTIVE, note="resumed: back on its daily schedule")
        deps.say(f"campaign #{c['id']} resumed: ${spent:.2f} spent today, back on schedule")


def enforce_budget_cap(deps: Deps) -> None:
    """If the daily cap was lowered below what's running, stop the newest
    campaigns until the total fits again."""
    cap = deps.settings.max_daily_spend
    for c in deps.store.list_campaigns(statuses=(ACTIVE,)):  # newest first
        if deps.store.running_daily_budget() <= cap:
            return
        stop_campaign(deps, c, STOPPED, f"over the daily cap of ${cap:.2f}")


def change_settings(settings: Settings, store: Store, values: dict) -> str:
    """Save validated panel overrides and apply what must follow at once
    (new budget on running campaigns, stop campaigns over a lowered cap).
    Returns a note in Russian for the owner."""
    before = effective(settings, store)
    save_overrides(store, values)
    after = effective(settings, store)
    note = "Настройки сохранены."
    if after.campaign_daily_budget != before.campaign_daily_budget:
        n = apply_daily_budget(build_deps(settings, store), after.campaign_daily_budget)
        note += f" Бюджет ${after.campaign_daily_budget:.2f}/день применён к {n} кампаниям."
    if after.max_daily_spend < store.running_daily_budget():
        enforce_budget_cap(build_deps(settings, store))
        note += " Лишние кампании остановлены, чтобы уложиться в новый лимит."
    if before.push_live and not after.push_live:
        note += (" Новые кампании больше не запускаются; уже работающие продолжают — "
                 "остановить их можно кнопкой «Стоп всё».")
    return note


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
        elif status == propeller.API_STATUS_PAUSED:
            # PropellerAds pauses a campaign by itself near its daily budget
            # ("Daily impressions": waiting for late clicks) and restarts it
            # on its own, so there's nothing to do but say so.
            if c["note"] != NETWORK_PAUSED_NOTE:
                deps.store.update_campaign(c["id"], note=NETWORK_PAUSED_NOTE)
        elif status is not None and f"PropellerAds: {name}" != c["note"]:
            deps.store.update_campaign(c["id"], note=f"PropellerAds: {name}")


def sync_stats(deps: Deps) -> bool:
    """Pull impressions / clicks / spend per campaign and per zone from
    PropellerAds into the store (what the dashboard shows and the kill and
    zone rules read). Returns False if the stats could not be read.

    Stopped campaigns from the last STATS_KEEP_DAYS are included: PropellerAds
    books late clicks for up to an hour after a stop, and a campaign paused
    at the limit still has spend to catch up on."""
    if deps.push is None:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=STATS_KEEP_DAYS)
    tracked = [c for c in deps.store.list_campaigns()
               if c["external_id"] and c["status"] != DRY_RUN
               and datetime.fromisoformat(c["created_at"]) > cutoff]
    if not tracked:
        return True
    by_external = {c["external_id"]: c for c in tracked}
    try:
        totals = deps.push.spend(list(by_external), days=365)
    except PropellerError as exc:
        deps.say(f"stats sync failed: {exc}")
        deps.store.set_flag(STATS_ERROR_FLAG, str(exc)[:300])
        return False
    deps.store.set_flag(STATS_OK_FLAG, now_iso())
    deps.store.set_flag(STATS_ERROR_FLAG, "")
    for row in totals:
        c = by_external.get(row["campaign_id"])
        if c:
            deps.store.update_campaign(c["id"], spend=row["spent"], impressions=row["impressions"],
                                       ad_clicks=row["clicks"], stats_at=now_iso())
    if not zone_stats_due(deps.store):
        return True
    try:
        zones = deps.push.spend(list(by_external), days=365, by_zone=True)
    except PropellerError as exc:
        deps.say(f"zone stats sync failed: {exc}")
        return True  # totals are in; zones are retried next check
    deps.store.set_flag(ZONE_STATS_FLAG, now_iso())
    for row in zones:
        c = by_external.get(row["campaign_id"])
        if c and row["zone_id"]:
            deps.store.upsert_zone_stats(c["id"], row["zone_id"], row["impressions"],
                                         row["clicks"], row["spent"])
    return True


def zone_stats_due(store: Store) -> bool:
    try:
        last = datetime.fromisoformat(store.get_flag(ZONE_STATS_FLAG, ""))
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last >= timedelta(minutes=ZONE_STATS_EVERY_MINUTES)


def sync_today_spend(deps: Deps) -> None:
    """Spend over the last 24 hours per campaign (including ones already
    stopped), so the daily cap counts money actually spent."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    recent = [c for c in deps.store.list_campaigns()
              if c["external_id"] and datetime.fromisoformat(c["created_at"]) > cutoff]
    if deps.push is None:
        return
    try:
        rows = deps.push.spend_last_hours([c["external_id"] for c in recent], hours=24)
    except PropellerError as exc:
        deps.say(f"24h spend sync failed: {exc}")
        return
    by_external = {r["campaign_id"]: r["spent"] for r in rows}
    spent = {str(c["id"]): by_external.get(c["external_id"], 0.0) for c in recent}
    deps.store.set_flag(TODAY_SPEND_FLAG, json.dumps({"at": now_iso(), "by_campaign": spent}))


def _spend_24h_by_campaign(store: Store) -> dict[str, float] | None:
    """{campaign id: spend over the last 24h}, or None if not read recently."""
    try:
        data = json.loads(store.get_flag(TODAY_SPEND_FLAG, "{}"))
        at = datetime.fromisoformat(data["at"])
    except (ValueError, KeyError, TypeError):
        return None
    if datetime.now(timezone.utc) - at > timedelta(minutes=TODAY_SPEND_MAX_AGE_MINUTES):
        return None
    return {k: float(v) for k, v in data.get("by_campaign", {}).items()}


def committed_24h(store: Store) -> float | None:
    """What the 24h limit must count as taken: last-24h spend of campaigns
    no longer running, plus for each running one the larger of its
    last-24h spend and its daily budget (a 24h window can span two of its
    budget days). None if the 24h spend isn't known."""
    by_campaign = _spend_24h_by_campaign(store)
    if by_campaign is None:
        return None
    running = store.list_campaigns(statuses=(ACTIVE, PACED))  # paced ones resume today
    ids = {str(c["id"]) for c in running}
    stopped = sum(v for k, v in by_campaign.items() if k not in ids)
    return stopped + sum(max(c["daily_budget"], by_campaign.get(str(c["id"]), 0.0))
                         for c in running)


def spent_today(store: Store, only_stopped: bool = False) -> float | None:
    """Spend over the last 24h (optionally only by campaigns no longer
    running). None if it hasn't been read recently: callers must then treat
    the budget as unknown, never as zero."""
    by_campaign = _spend_24h_by_campaign(store)
    if by_campaign is None:
        return None
    if only_stopped:
        active = {str(c["id"]) for c in store.list_campaigns(statuses=(ACTIVE,))}
        by_campaign = {k: v for k, v in by_campaign.items() if k not in active}
    return float(sum(by_campaign.values()))


def stop_if_flying_blind(deps: Deps) -> bool:
    """Kill rules need spend numbers. If PropellerAds stats have been
    unreadable for STATS_BLIND_MINUTES while campaigns run, stop them all:
    better paused than spending unchecked. Returns True if it stopped any."""
    active = [c for c in deps.store.list_campaigns(statuses=(ACTIVE,)) if c["external_id"]]
    if not active:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STATS_BLIND_MINUTES)
    last_ok = deps.store.get_flag(STATS_OK_FLAG)
    try:
        blind_since = datetime.fromisoformat(last_ok) if last_ok else None
    except ValueError:
        blind_since = None
    if blind_since is None:
        # Never synced: count from the oldest running campaign's launch.
        blind_since = min(datetime.fromisoformat(c["created_at"]) for c in active)
    if blind_since > cutoff:
        return False
    for c in active:
        stop_campaign(deps, c, STOPPED,
                      f"no spend data from PropellerAds for {STATS_BLIND_MINUTES}+ min")
    deps.say("safety stop: PropellerAds stats unavailable, running campaigns stopped")
    return True


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


def estimated_spend(deps: Deps, campaign: dict) -> float:
    """PropellerAds' spend figure lags (up to about an hour), while a push
    campaign can burn several dollars in that time. Every visit logged on our
    site is a paid click, so visits x CPC bid (scaled up for clicks that never
    load the page) is a real-time lower bound; judge on whichever is higher."""
    visits = deps.store.count_events(campaign["id"], "visit")
    from_visits = visits * deps.settings.push_bid_cpc / VISITS_PER_PAID_CLICK
    return max(campaign["spend"], from_visits)


def resume_campaign(deps: Deps, campaign_id: int) -> str | None:
    """Bring a stopped/killed campaign back by hand. It's marked manual_keep
    so the kill rules don't stop it again (limits and safety stops still
    apply). Returns an error message or None."""
    c = deps.store.get_campaign(campaign_id)
    if c is None:
        return "кампания не найдена"
    if c["status"] not in (KILLED, STOPPED):
        return "кампания не остановлена"
    if not c["external_id"]:
        return "эта кампания не была отправлена в PropellerAds (тестовый режим)"
    if deps.push is None:
        return "PropellerAds не подключён или реклама выключена в настройках"
    # Same rule as launching: spent in 24h by stopped campaigns + running
    # budgets + this one's budget must fit under the limit.
    committed = committed_24h(deps.store) or 0.0
    if committed + c["daily_budget"] > deps.settings.max_daily_spend:
        return (f"не хватает дневного лимита: занято ${committed:.2f} из "
                f"${deps.settings.max_daily_spend:.2f} (поднимите лимит в настройках)")
    try:
        deps.push.start([c["external_id"]])
    except PropellerError as exc:
        return f"PropellerAds не запустил кампанию: {exc}"
    deps.store.update_campaign(campaign_id, status=ACTIVE, manual_keep=1,
                               note="resumed manually: agent won't kill it")
    deps.say(f"campaign #{campaign_id} resumed manually")
    return None


def is_manual(store: Store) -> bool:
    return store.get_flag(MANUAL_FLAG) == "1"


def apply_kill_rules(deps: Deps, by_results: bool = True) -> None:
    """Stop campaigns whose product or site is gone and, with `by_results`,
    the ones that failed the product test."""
    s, store = deps.settings, deps.store
    for c in store.list_campaigns(statuses=(ACTIVE,)):
        if c.get("manual_keep"):
            continue  # resumed by hand: the owner decides
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
        if not by_results:
            continue
        spend = estimated_spend(deps, c)
        if spend < s.kill_min_spend:
            continue
        clicks = store.count_events(c["id"], "click")
        visits = store.count_events(c["id"], "visit")
        rate = 100 * clicks / visits if visits else 0.0
        cost = spend / clicks if clicks else None
        if s.min_amazon_rate > 0 and rate < s.min_amazon_rate:
            stop_campaign(deps, c, KILLED,
                          f"spent ${spend:.2f}: {clicks} of {visits} visitors went to Amazon "
                          f"({rate:.2f}% < {s.min_amazon_rate:g}%)")
        elif s.max_cost_per_amazon_click > 0 and (cost is None
                                                   or cost > s.max_cost_per_amazon_click):
            shown = f"${cost:.2f}" if cost is not None else "no clicks"
            stop_campaign(deps, c, KILLED,
                          f"spent ${spend:.2f}, cost per Amazon click {shown} "
                          f"> ${s.max_cost_per_amazon_click:.2f}")


def blacklist_bad_zones(deps: Deps) -> None:
    """For campaigns that passed the product test (spent KILL_MIN_SPEND and
    >= MIN_AMAZON_RATE % of visitors went to Amazon) or were resumed by hand,
    exclude the zones that
    sent no one to Amazon once they've had a fair sample (ZONE_MIN_VISITS
    visits or ZONE_MIN_SPEND spent); zones with a click to Amazon stay."""
    if deps.push is None:
        return
    s, store = deps.settings, deps.store
    for c in store.list_campaigns(statuses=(ACTIVE,)):
        if not c["external_id"]:
            continue
        visits_by_zone = store.events_by_zone(c["id"], "visit")
        # Only judge zones where our own visit log carries zone ids: if the
        # zone macro in the target URL isn't substituted, every zone would
        # look like it had zero clicks and all would get excluded.
        if not visits_by_zone:
            continue
        visits = store.count_events(c["id"], "visit")
        rate = 100 * store.count_events(c["id"], "click") / visits if visits else 0.0
        passed = estimated_spend(deps, c) >= s.kill_min_spend and rate >= s.min_amazon_rate
        # A campaign resumed by hand is kept whatever its overall rate, so
        # trimming its dead zones is exactly what's left to optimize.
        if not (passed or c.get("manual_keep")):
            continue  # still in its test (or about to be killed): leave zones alone
        clicks = store.events_by_zone(c["id"], "click")
        excluded = store.blacklisted_zones(c["id"])
        spent = {z["zone"]: z["spent"] for z in store.zone_stats(c["id"])}
        for zone in set(visits_by_zone) | set(spent):
            if zone in excluded or clicks.get(zone, 0) > 0:
                continue
            zone_visits, zone_spent = visits_by_zone.get(zone, 0), spent.get(zone, 0.0)
            if zone_visits < s.zone_min_visits and zone_spent < s.zone_min_spend:
                continue  # not enough data on this zone yet
            error = exclude_zone(deps, c["id"], zone,
                                 f"{zone_visits} visits, ${zone_spent:.2f}, no Amazon clicks")
            if error:
                deps.say(f"campaign #{c['id']}: zone exclude failed: {error}")


def expire_stuck_creations(deps: Deps) -> None:
    """A launch interrupted mid-way (e.g. a restart while images were being
    drawn) leaves a row in "creating" forever; after STUCK_CREATING_MINUTES
    mark it as an error so its slot and product are free again."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_CREATING_MINUTES)
    for c in deps.store.list_campaigns(statuses=(CREATING,)):
        try:
            started = datetime.fromisoformat(c["created_at"])
        except ValueError:
            continue
        if started < cutoff:
            deps.store.update_campaign(c["id"], status=ERROR,
                                       note="creation interrupted (e.g. server restart)")
            deps.say(f"campaign #{c['id']}: creation was interrupted, slot freed")


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
        # A campaign still being created holds its slot and its product.
        # Campaigns being created or paused at the limit keep their slot.
        running = [c for c in campaigns
                   if c["status"] in (running_status, CREATING, CAPPED, PACED)]
        taken = {c["asin"] for c in campaigns
                 if c["status"] in (running_status, KILLED, CREATING, CAPPED, PACED)}
        slots = s.campaigns_per_site - len(running)

        for product, copy in store.list_products(niche.id):
            if slots <= 0:
                break
            if product.asin in taken or copy is None:
                continue
            if product.cc_budget == "low":
                continue  # bonus budget nearly gone: not worth paid traffic
            # Cap = money spent in the last 24h by campaigns that were stopped
            # + budgets of the running ones + the new one. Unknown spend
            # never counts as zero.
            committed = committed_24h(store)
            if live and committed is None:
                deps.say_once("launch-unknown",
                              "launch paused: last-24h spend unknown (PropellerAds stats not read)")
                return
            committed = committed or 0.0
            if live and committed + s.campaign_daily_budget > s.max_daily_spend:
                deps.say_once(
                    "launch-cap",
                    f"launch paused: no room under the daily cap — ${committed:.2f} of "
                    f"${s.max_daily_spend:.2f} taken in 24h, a new campaign needs "
                    f"${s.campaign_daily_budget:.2f}"
                )
                return

            if create_campaign(deps, niche, site_copy, product, copy) is None:
                slots -= 1


def create_campaign(deps: Deps, niche: Niche, site_copy, product, copy) -> str | None:
    """Create one campaign (or a dry-run row). Returns an error or None."""
    s, store = deps.settings, deps.store
    cid = store.add_campaign(niche.id, product.asin, CREATING, s.campaign_daily_budget)
    payload = build_payload(deps, niche, site_copy, product, copy, cid)
    if not s.push_live:
        store.update_campaign(cid, status=DRY_RUN, payload=payload,
                              note="dry run: not sent (PUSH_LIVE=false)")
        deps.say(f"[{niche.slug}] dry-run campaign #{cid} for {product.asin}")
        return None
    try:
        # Created straight into moderation; it starts once approved.
        external_id = deps.push.create_campaign(
            propeller.inline_images(payload, lambda url: media_file(deps, url))
        )
    except PropellerError as exc:
        store.update_campaign(cid, status=ERROR, payload=payload, note=str(exc)[:500])
        deps.say(f"[{niche.slug}] campaign #{cid} failed: {exc}")
        return f"PropellerAds не создал кампанию: {exc}"
    store.update_campaign(cid, status=ACTIVE, external_id=external_id,
                          payload=payload, note="sent to PropellerAds moderation")
    deps.say(
        f"[{niche.slug}] launched campaign #{cid} (PropellerAds {external_id}) "
        f"for {product.asin}, ${s.campaign_daily_budget:.2f}/day"
    )
    return None


def launch_product(deps: Deps, niche_id: int, asin: str) -> str | None:
    """Launch a campaign for one product by hand (any mode). The daily
    limit still applies; the per-site slot count and "already killed"
    history don't — that's the owner's call. Returns an error or None."""
    s, store = deps.settings, deps.store
    niche = store.get_niche(niche_id)
    found = store.get_product(niche_id, asin) if niche else None
    if found is None:
        return "товар не найден на сайте"
    product, copy = found
    site_copy = store.get_site_copy(niche_id)
    if copy is None or site_copy is None:
        return "у товара ещё нет текстов — дождитесь окончания цикла"
    running = (ACTIVE, CREATING, CAPPED, PACED, DRY_RUN)
    if any(c["asin"] == asin for c in store.list_campaigns(niche_id=niche_id, statuses=running)):
        return "на этот товар уже есть работающая кампания"
    if s.push_live:
        if deps.push is None:
            return "PropellerAds не подключён"
        committed = committed_24h(store)
        if committed is None:
            return "расход за 24 ч ещё не получен из PropellerAds — попробуйте через пару минут"
        if committed + s.campaign_daily_budget > s.max_daily_spend:
            return (f"не хватает дневного лимита: занято ${committed:.2f} из "
                    f"${s.max_daily_spend:.2f} (поднимите лимит в настройках)")
    return create_campaign(deps, niche, site_copy, product, copy)


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
    for c in deps.store.list_campaigns(statuses=(ACTIVE, DRY_RUN, CAPPED, PACED)):
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
    expire_stuck_creations(deps)
    fix_tracking_urls(deps)
    sync_moderation(deps)
    stats_ok = sync_stats(deps)
    if not stats_ok:
        # Can't judge spend: never launch more, and stop what runs once the
        # outage outlasts STATS_BLIND_MINUTES.
        stop_if_flying_blind(deps)
        deps.say_once("launch-no-stats", "launch skipped: PropellerAds stats unavailable",
                      timedelta(minutes=30))
        return
    manual = is_manual(deps.store)
    apply_kill_rules(deps, by_results=not manual)
    if not manual:
        blacklist_bad_zones(deps)
    sync_today_spend(deps)  # right before launching: the cap needs fresh numbers
    enforce_spend_cap(deps)
    pace_campaigns(deps)
    if not manual:
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
        run_id = deps.store.start_run(None, kind="check")
        for line in deps.log:
            deps.store.append_run_log(run_id, line)
        deps.store.finish_run(run_id, True)
        deps.store.prune_runs("check", CHECK_LOG_DAYS)
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
            auto = 0 if is_manual(deps.store) else deps.settings.auto_niches - enabled
            missing = max(discover, auto)  # a "find niches" press works in either mode
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
