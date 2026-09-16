"""Ties every stage of the agent together: competitor research -> analysis
-> ad copy -> creative images -> landing page -> Facebook campaign.
"""
from __future__ import annotations

import logging

from fbadsagent.config import Settings
from fbadsagent.creatives.image_generator import generate_images
from fbadsagent.facebook.ad_library import AdLibraryClient, AdLibraryError
from fbadsagent.facebook.ads_client import FacebookAdsClient
from fbadsagent.landing.generator import generate_landing_page
from fbadsagent.llm.competitor_analysis import analyze_competitor_ads
from fbadsagent.llm.copywriter import generate_ad_variants
from fbadsagent.llm.provider import LLMProvider, get_llm_provider
from fbadsagent.models import PipelineResult, ProductInput

logger = logging.getLogger(__name__)


class Pipeline:
    """Runs the full ads-agent workflow for a single product."""

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMProvider | None = None,
        ad_library: AdLibraryClient | None = None,
        ads_client: FacebookAdsClient | None = None,
    ):
        self.settings = settings or Settings()
        self.llm = llm or get_llm_provider(self.settings)
        self.ad_library = ad_library or AdLibraryClient(self.settings)
        self.ads_client = ads_client or FacebookAdsClient(self.settings)

    def run(self, product: ProductInput, dry_run: bool = True) -> PipelineResult:
        result = PipelineResult(product=product)

        result.competitor_ads = self._research_competitors(product)
        result.insights = analyze_competitor_ads(
            self.llm, product.name, result.competitor_ads
        )

        result.creatives = generate_ad_variants(self.llm, product, result.insights)
        result.images = generate_images(self.settings, result.creatives)

        result.landing_page = generate_landing_page(
            self.llm, self.settings, product, result.insights
        )

        landing_url = product.landing_url or f"file://{result.landing_page.html_path}"
        result.campaign = self.ads_client.create_campaign(
            product, result.creatives, result.images, landing_url, dry_run=dry_run
        )

        return result

    def _research_competitors(self, product: ProductInput):
        try:
            return self.ad_library.search_competitor_ads(product.search_query())
        except AdLibraryError as exc:
            logger.warning("Competitor research skipped: %s", exc)
            return []
