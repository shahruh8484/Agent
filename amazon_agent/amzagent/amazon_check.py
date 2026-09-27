"""Check the Amazon Creators API from the server, read-only.

    docker compose exec app python -m amzagent.amazon_check "smart plug"

Prints what the API returns for a search: how many items, and for the
first few the fields product selection needs (price, rating, reviews,
image) with the reason a product would be rejected.
"""
from __future__ import annotations

import sys
import traceback

from amzagent.config import get_settings


def main(argv: list[str]) -> int:
    keywords = " ".join(argv) or "smart plug"
    s = get_settings()
    print(f"credential version {s.amazon_credential_version}, tag {s.amazon_partner_tag!r}, "
          f"country {s.amazon_country}, throttling {s.amazon_throttling}s", flush=True)
    try:
        from amzagent.amazon.catalog import CreatorsApiCatalog, _call
        from amzagent.selection.selector import rejection_reason

        catalog = CreatorsApiCatalog(s)
        print(f"searching {keywords!r} ... (may wait up to ~2 min on a rate limit)", flush=True)
        raw = _call(catalog._api.search_items, keywords=keywords, item_count=10)
    except Exception as exc:  # show anything: this is a diagnostic
        print(f"FAILED: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        return 1

    from amzagent.amazon.catalog import parse_item

    items = raw.items or []
    print(f"found: {len(items)}", flush=True)
    for item in items[:5]:
        p = parse_item(item)
        if p is None:
            print(f"  {getattr(item, 'asin', '?')}: no title/url -> skipped")
            continue
        reason = rejection_reason(p, s.min_rating, s.min_reviews) or "OK"
        print(f"  {p.asin} | price {p.price} | rating {p.rating} | reviews {p.review_count} "
              f"| image {bool(p.image_url)} | -> {reason}")
        print(f"      {p.title[:80]}")
    if items:
        first = items[0]
        print("\nraw fields of the first item:")
        for name in ("customer_reviews", "offers_v2", "images", "browse_node_info"):
            value = getattr(first, name, None)
            print(f"  {name}: {str(value)[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
