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
    sales_rank: int | None = None
    category: str = ""
    # Creator Connections "Estimated EPC" ($ per click) for imported
    # campaign products; None for products found by search.
    epc: float | None = None
    # Built from pasted Creator Connections text because the Creators API
    # was unavailable: no image, price or rating may be shown for it.
    offline: bool = False
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
