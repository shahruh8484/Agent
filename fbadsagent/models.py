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


class PipelineResult(BaseModel):
    product: ProductInput
    competitor_ads: list[CompetitorAd] = Field(default_factory=list)
    insights: Optional[CompetitorInsights] = None
    creatives: list[AdCreativeCopy] = Field(default_factory=list)
    images: list[GeneratedImage] = Field(default_factory=list)
    landing_page: Optional[LandingPage] = None
    campaign: Optional[CampaignPlan] = None
