from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ProductInput(BaseModel):
    """What the user gives us about the product to advertise."""

    name: str
    description: str
    landing_url: Optional[str] = None
    price: Optional[float] = None
    currency: str = "USD"
    target_countries: list[str] = Field(default_factory=lambda: ["US"])
    daily_budget: float = 20.0
    keywords: list[str] = Field(default_factory=list)

    def search_query(self) -> str:
        return " ".join(self.keywords) if self.keywords else self.name


class CompetitorAd(BaseModel):
    """A single ad pulled from the Meta Ad Library for a competitor/keyword."""

    page_name: str
    ad_creative_body: str = ""
    ad_creative_link_title: str = ""
    ad_snapshot_url: str = ""
    ad_delivery_start_time: Optional[str] = None
    publisher_platforms: list[str] = Field(default_factory=list)


class CompetitorInsights(BaseModel):
    """LLM-derived summary of what competitors are doing."""

    common_hooks: list[str] = Field(default_factory=list)
    common_offers: list[str] = Field(default_factory=list)
    tone_observations: str = ""
    recommended_angle: str = ""
    raw_ads_analyzed: int = 0


class AdCreativeCopy(BaseModel):
    """One ad copy variant, Facebook-style (primary text / headline / description)."""

    variant_id: str
    primary_text: str
    headline: str
    description: str
    call_to_action: str = "SHOP_NOW"
    image_prompt: str = ""


class GeneratedImage(BaseModel):
    variant_id: str
    path: str
    prompt: str
    provider: str


class LandingPage(BaseModel):
    product_name: str
    html_path: str
    headline: str
    subheadline: str
    benefits: list[str] = Field(default_factory=list)
    cta_text: str


class CampaignPlan(BaseModel):
    """What would be / was sent to the Facebook Marketing API."""

    campaign_name: str
    objective: str
    daily_budget: float
    status: str
    dry_run: bool
    campaign_id: Optional[str] = None
    adset_id: Optional[str] = None
    ad_ids: list[str] = Field(default_factory=list)
    payloads: list[dict] = Field(default_factory=list)


class TrackedAccount(BaseModel):
    """A Facebook ad account the dashboard is configured to show."""

    id: str
    name: str = ""


class LandingPageConfig(BaseModel):
    """A published landing page: content plus where its leads go."""

    slug: str
    title: str
    headline: str
    subheadline: str = ""
    benefits: list[str] = Field(default_factory=list)
    cta_text: str = "Get Started"
    cpa_network: str = "traff-hub"
    campaign_hash: str = ""


class CpaNetworkCredential(BaseModel):
    """Credentials for a CPA/offer network (e.g. traff-hub.com) — stored so a
    future sync job can pull offers from it. No fetching logic yet; this is
    just where the credentials live once you're ready to wire it up."""

    name: str
    base_url: str = ""
    api_key: str = ""


class DailyInsight(BaseModel):
    """One day of performance data for a single ad account."""

    date: str
    spend: float
    clicks: int
    impressions: int
    leads: int
    ctr: float
    cpc: float
    cpl: Optional[float] = None


class AccountInsightsSummary(BaseModel):
    """Daily breakdown plus period totals for one ad account, for the dashboard."""

    account_id: str
    account_name: Optional[str] = None
    date_preset: str
    daily: list[DailyInsight] = Field(default_factory=list)
    total_spend: float = 0.0
    total_clicks: int = 0
    total_impressions: int = 0
    total_leads: int = 0
    avg_ctr: float = 0.0
    avg_cpc: float = 0.0
    avg_cpl: Optional[float] = None


class PipelineResult(BaseModel):
    product: ProductInput
    competitor_ads: list[CompetitorAd] = Field(default_factory=list)
    insights: Optional[CompetitorInsights] = None
    creatives: list[AdCreativeCopy] = Field(default_factory=list)
    images: list[GeneratedImage] = Field(default_factory=list)
    landing_page: Optional[LandingPage] = None
    campaign: Optional[CampaignPlan] = None
