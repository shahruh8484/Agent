"""The autonomous agent's core loop: for one product, research
competitors, write ad creatives, generate a landing page, publish it, and
create a (always PAUSED) Facebook campaign pointed at it.

Every external call is wrapped so a single failure produces a clear
AgentRunResult instead of crashing the caller — this runs both from a
button click and from an unattended background loop.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fbadsagent.config import Settings
from fbadsagent.creatives.image_generator import ImageGenerationError, generate_images
from fbadsagent.facebook.ad_library import AdLibraryClient, AdLibraryError
from fbadsagent.facebook.ads_client import FacebookAdsClient
from fbadsagent.landing.generator import generate_landing_copy
from fbadsagent.llm.competitor_analysis import analyze_competitor_ads
from fbadsagent.llm.copywriter import generate_ad_variants
from fbadsagent.llm.provider import LLMError, get_llm_provider
from fbadsagent.models import AgentProduct, AgentRunResult, LandingPageConfig, SavedCreativeSet
from fbadsagent.web.creative_store import CreativeStore
from fbadsagent.web.landing_store import LandingPageStore, slugify

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def run_agent_for_product(
    product: AgentProduct,
    settings: Settings,
    landing_store: LandingPageStore,
    creative_store: CreativeStore,
    triggered_by: str = "manual",
) -> AgentRunResult:
    product_input = product.to_product_input()

    try:
        llm = get_llm_provider(settings)
    except LLMError as exc:
        return _error_result(product, f"No LLM configured: {exc}", triggered_by)

    # 1. Competitor research (best-effort — proceed with empty insights if
    # it fails, same as the CLI pipeline).
    ads = []
    try:
        ad_library = AdLibraryClient(settings)
        ads = ad_library.search_competitor_ads(product_input.search_query())
    except AdLibraryError as exc:
        logger.info("Competitor research skipped for %s: %s", product.name, exc)

    try:
        insights = analyze_competitor_ads(llm, product.name, ads)
    except LLMError as exc:
        return _error_result(product, f"Competitor analysis failed: {exc}", triggered_by)

    # 2. Ad creatives (copy + images)
    try:
        creatives = generate_ad_variants(llm, product_input, insights, n=3)
    except LLMError as exc:
        return _error_result(product, f"Ad copy generation failed: {exc}", triggered_by)

    try:
        images = generate_images(settings, creatives)
    except ImageGenerationError as exc:
        return _error_result(product, f"Image generation failed: {exc}", triggered_by)

    creative_set_id = uuid.uuid4().hex[:12]
    creative_store.add_set(
        SavedCreativeSet(
            id=creative_set_id,
            product_name=product.name,
            created_at=_now(),
            creatives=creatives,
            images=images,
        )
    )

    # 3. Landing page — generate copy, publish as a public /lp/{slug} page
    # with a lead form wired to the product's CPA network.
    try:
        copy = generate_landing_copy(llm, product_input, insights)
    except LLMError as exc:
        return _error_result(
            product, f"Landing page generation failed: {exc}", triggered_by, creative_set_id
        )

    slug = f"{slugify(product.name)}-{uuid.uuid4().hex[:6]}"
    landing_store.add_page(
        LandingPageConfig(
            slug=slug,
            title=product.name,
            headline=copy["headline"],
            subheadline=copy["subheadline"],
            benefits=copy["benefits"],
            cta_text=copy["cta_text"],
            cpa_network=product.cpa_network,
            campaign_hash=product.campaign_hash,
        )
    )

    domain = settings.domain.strip()
    landing_url = f"https://{domain}/lp/{slug}" if domain else f"/lp/{slug}"

    # 4. Facebook campaign — always PAUSED (see FacebookAdsClient), created
    # for real (dry_run=False) so it actually shows up in Ads Manager for
    # review. Targets whichever ad account this product is assigned to.
    if not product.fb_ad_account_id:
        return _error_result(
            product,
            "No Facebook ad account assigned to this product.",
            triggered_by,
            creative_set_id,
            slug,
        )

    account_settings = settings.model_copy(update={"fb_ad_account_id": product.fb_ad_account_id})
    ads_client = FacebookAdsClient(account_settings)
    try:
        campaign = ads_client.create_campaign(
            product_input, creatives, images, landing_url, dry_run=False
        )
    except Exception as exc:  # noqa: BLE001 - surface any FB API failure as a run result
        return _error_result(
            product, f"Facebook campaign creation failed: {exc}", triggered_by, creative_set_id, slug
        )

    return AgentRunResult(
        product_id=product.id,
        product_name=product.name,
        created_at=_now(),
        status="success",
        message=f"Campaign created (PAUSED): {campaign.campaign_name}",
        creative_set_id=creative_set_id,
        landing_page_slug=slug,
        fb_campaign_id=campaign.campaign_id,
        triggered_by=triggered_by,
    )


def _error_result(
    product: AgentProduct,
    message: str,
    triggered_by: str,
    creative_set_id: str | None = None,
    landing_page_slug: str | None = None,
) -> AgentRunResult:
    return AgentRunResult(
        product_id=product.id,
        product_name=product.name,
        created_at=_now(),
        status="error",
        message=message,
        creative_set_id=creative_set_id,
        landing_page_slug=landing_page_slug,
        triggered_by=triggered_by,
    )
