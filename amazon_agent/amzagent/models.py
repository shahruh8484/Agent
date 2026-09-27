from __future__ import annotations

from pydantic import BaseModel, Field


class Product(BaseModel):
    """An Amazon item as returned by the Creators API, flattened to what
    the site and the selector need."""

    asin: str
    title: str
    url: str  # detail page URL — already carries the partner tag
    image_url: str = ""
    brand: str = ""
    features: list[str] = Field(default_factory=list)
    price: float | None = None
    price_display: str = ""
    currency: str = ""
    savings_percent: float | None = None
    rating: float | None = None
    review_count: int = 0
    # Rating/reviews read from the pasted Creator Connections page, for when
    # the API returns none (new accounts get no CustomerReviews). Used only
    # to pick and rank products — never shown on the site.
    hint_rating: float | None = None
    hint_reviews: int = 0
    sales_rank: int | None = None
    category: str = ""
    # Creator Connections "Estimated EPC" ($ per click) for imported
    # campaign products; None for products found by search.
    epc: float | None = None
    # Creator Connections "Budget availability score": high | medium | low
    # ("" = unknown). Low means the brand's bonus budget is nearly gone.
    cc_budget: str = ""
    # Built from pasted Creator Connections text because the Creators API
    # was unavailable: no image, price or rating may be shown for it.
    offline: bool = False
    # AI-drawn illustration of the product *type*, shown (labelled as an
    # illustration) only while no real Amazon photo is available.
    illustration_url: str = ""
    # ISO timestamp of the API call the price came from. Amazon only lets
    # you show a price fetched within the last 24h.
    fetched_at: str = ""


class ProductCopy(BaseModel):
    """LLM-written text for one product on the site and in push ads."""

    summary: str
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    push_title: str
    push_text: str
    # "What to look for" advice about the product *type* (e.g. juicers in
    # general), never claims about this particular model.
    product_type: str = ""
    buying_tips: list[str] = Field(default_factory=list)
    # Bumped when the copy format gains fields; older copy is rewritten.
    version: int = 0


COPY_VERSION = 1


class GuidePick(BaseModel):
    """One product in a section's buying guide."""

    asin: str
    best_for: str = ""  # e.g. "Best for large living rooms"
    blurb: str = ""  # 1-2 sentences, from the product's own features


class SiteSection(BaseModel):
    """A topic on a site (e.g. "Air Purifiers") with a comparison guide."""

    slug: str
    name: str
    asins: list[str] = Field(default_factory=list)  # best first
    guide_title: str = ""
    intro: str = ""
    how_to_choose: list[str] = Field(default_factory=list)
    picks: list[GuidePick] = Field(default_factory=list)
    verdict: str = ""


class SitePlan(BaseModel):
    """How a site's products are grouped into sections, and their guides."""

    signature: str = ""  # which products it was built for
    built_at: str = ""
    sections: list[SiteSection] = Field(default_factory=list)


class SiteCopy(BaseModel):
    site_title: str
    tagline: str
    intro: str


class Niche(BaseModel):
    id: int | None = None
    slug: str
    keywords: str
    search_index: str = "All"
    language: str = "English"
    max_price: float | None = None
    enabled: bool = True
    # Curated site: products come from this fixed ASIN list ({asin: EPC})
    # instead of a keyword search, e.g. Creator Connections imports.
    asins: dict[str, float | None] = Field(default_factory=dict)
    # Card details from the pasted page ({asin: {title, brand, rating,
    # reviews}}), used to build the site while the API is unavailable.
    asin_meta: dict[str, dict] = Field(default_factory=dict)
