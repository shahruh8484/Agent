"""Product search through the Amazon Creators API.

The Creators API replaced PA-API 5 (retired May 15, 2026). It is only
available to approved Associates accounts; create credentials in
Associates Central -> Tools -> Creators API. Credentials of version 3.x
(Login with Amazon) are required — the older 2.x (Cognito) ones stop
working after September 11, 2026.

The HTTP/OAuth work is done by `python-amazon-paapi` (>= 7.1), which ships
the `amazon_creatorsapi` client. This module only turns its item objects
into our flat `Product` model.

Amazon's license terms this module is written around:
- prices may only be shown if fetched within the last 24h (`fetched_at`);
- images are hotlinked from Amazon's CDN, never downloaded/re-hosted;
- the detail page URL returned by the API already carries the partner tag
  and must be used as-is.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Protocol

from amzagent.config import Settings
from amzagent.models import Product

logger = logging.getLogger(__name__)


class CatalogError(RuntimeError):
    pass


class Catalog(Protocol):
    def search(
        self,
        keywords: str,
        search_index: str = "All",
        max_price: float | None = None,
        page: int = 1,
    ) -> list[Product]: ...

    def get(self, asins: list[str]) -> list[Product]: ...


def _get(obj: Any, *path: str) -> Any:
    """Walk attribute path, returning None as soon as a link is missing —
    every field in the Creators API response is optional."""
    for name in path:
        if obj is None:
            return None
        obj = getattr(obj, name, None)
    return obj


def parse_item(item: Any, fetched_at: str | None = None) -> Product | None:
    asin = _get(item, "asin")
    title = _get(item, "item_info", "title", "display_value")
    url = _get(item, "detail_page_url")
    if not (asin and title and url):
        return None

    listings = _get(item, "offers_v2", "listings") or []
    listing = next((lst for lst in listings if _get(lst, "is_buy_box_winner")), None)
    if listing is None and listings:
        listing = listings[0]

    price = _get(listing, "price", "money", "amount")
    rating = _get(item, "customer_reviews", "star_rating", "value")
    reviews = _get(item, "customer_reviews", "count")
    rank = _get(item, "browse_node_info", "website_sales_rank", "sales_rank")

    return Product(
        asin=asin,
        title=title,
        url=url,
        image_url=(
            _get(item, "images", "primary", "large", "url")
            or _get(item, "images", "primary", "medium", "url")
            or ""
        ),
        brand=_get(item, "item_info", "by_line_info", "brand", "display_value") or "",
        features=list(_get(item, "item_info", "features", "display_values") or [])[:6],
        price=float(price) if price is not None else None,
        price_display=_get(listing, "price", "money", "display_amount") or "",
        currency=_get(listing, "price", "money", "currency") or "",
        savings_percent=_get(listing, "price", "savings", "percentage"),
        rating=float(rating) if rating is not None else None,
        review_count=int(reviews or 0),
        sales_rank=int(rank) if rank is not None else None,
        category=_get(item, "item_info", "classifications", "product_group", "display_value")
        or "",
        fetched_at=fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


# Extra waits (seconds) after the SDK's own quick retries still hit the rate
# limit: a new account's quota refills slowly.
RATE_LIMIT_WAITS = (10, 30, 60)


def _call(fn, *args, **kwargs):
    """Call the API, waiting out "Rate limit exceeded" a few times."""
    from amazon_creatorsapi import errors

    for wait in (*RATE_LIMIT_WAITS, None):
        try:
            return fn(*args, **kwargs)
        except errors.TooManyRequestsError:
            if wait is None:
                raise
            logger.info("Creators API rate limit, waiting %ss", wait)
            time.sleep(wait)


class CreatorsApiCatalog:
    def __init__(self, settings: Settings):
        if not (
            settings.amazon_credential_id
            and settings.amazon_credential_secret
            and settings.amazon_partner_tag
        ):
            raise CatalogError(
                "Set AMAZON_CREDENTIAL_ID, AMAZON_CREDENTIAL_SECRET and AMAZON_PARTNER_TAG."
            )
        from amazon_creatorsapi import AmazonCreatorsApi

        self._api = AmazonCreatorsApi(
            credential_id=settings.amazon_credential_id,
            credential_secret=settings.amazon_credential_secret,
            version=settings.amazon_credential_version,
            tag=settings.amazon_partner_tag,
            country=settings.amazon_country,
            throttling=settings.amazon_throttling,
        )
        self._min_rating = settings.min_rating

    def search(
        self,
        keywords: str,
        search_index: str = "All",
        max_price: float | None = None,
        page: int = 1,
    ) -> list[Product]:
        from amazon_creatorsapi import errors

        try:
            result = _call(
                self._api.search_items,
                keywords=keywords,
                search_index=search_index or "All",
                item_count=10,  # API maximum per call
                item_page=page,
                # Prices are in the lowest denomination (cents).
                max_price=int(max_price * 100) if max_price else None,
                min_reviews_rating=max(1, min(4, int(self._min_rating))),
            )
        except errors.ItemsNotFoundError:
            return []
        except errors.AmazonCreatorsApiError as exc:
            raise CatalogError(f"Creators API search failed: {exc}") from exc
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return [p for p in (parse_item(i, now) for i in (result.items or [])) if p]

    def get(self, asins: list[str]) -> list[Product]:
        from amazon_creatorsapi import errors

        products: list[Product] = []
        for start in range(0, len(asins), 10):  # API maximum per call
            chunk = asins[start : start + 10]
            try:
                items = _call(self._api.get_items, chunk)
            except errors.ItemsNotFoundError:
                continue
            except errors.AmazonCreatorsApiError as exc:
                raise CatalogError(f"Creators API get_items failed: {exc}") from exc
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            products.extend(p for p in (parse_item(i, now) for i in items) if p)
        return products
