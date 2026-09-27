from types import SimpleNamespace as NS

from amzagent.amazon.catalog import parse_item


def _item(**over):
    base = dict(
        asin="B0TEST",
        detail_page_url="https://www.amazon.com/dp/B0TEST?tag=t-20",
        item_info=NS(
            title=NS(display_value="Earbuds"),
            by_line_info=NS(brand=NS(display_value="Acme")),
            features=NS(display_values=["a", "b"]),
            classifications=NS(product_group=NS(display_value="Electronics")),
        ),
        images=NS(primary=NS(large=NS(url="https://img/large.jpg"), medium=None)),
        offers_v2=NS(listings=[
            NS(is_buy_box_winner=False, price=NS(money=NS(amount=99.0, display_amount="$99", currency="USD"), savings=None)),
            NS(is_buy_box_winner=True, price=NS(money=NS(amount=49.5, display_amount="$49.50", currency="USD"), savings=NS(percentage=20))),
        ]),
        customer_reviews=NS(count=1234, star_rating=NS(value=4.4)),
        browse_node_info=NS(website_sales_rank=NS(sales_rank=57)),
    )
    base.update(over)
    return NS(**base)


def test_parse_item_uses_buy_box_listing():
    p = parse_item(_item(), fetched_at="2026-01-01T00:00:00+00:00")
    assert p.asin == "B0TEST"
    assert p.title == "Earbuds"
    assert p.price == 49.5 and p.price_display == "$49.50"
    assert p.savings_percent == 20
    assert p.rating == 4.4 and p.review_count == 1234
    assert p.sales_rank == 57
    assert p.brand == "Acme" and p.category == "Electronics"
    assert p.image_url == "https://img/large.jpg"
    assert p.url.endswith("tag=t-20")


def test_parse_item_tolerates_missing_optional_parts():
    p = parse_item(_item(offers_v2=None, customer_reviews=None, images=None, browse_node_info=None))
    assert p.price is None and p.rating is None and p.review_count == 0
    assert p.image_url == "" and p.sales_rank is None


def test_parse_item_requires_title_and_url():
    assert parse_item(_item(detail_page_url=None)) is None
    assert parse_item(_item(item_info=None)) is None


def test_rate_limit_is_waited_out(monkeypatch):
    from amazon_creatorsapi import errors

    import amzagent.amazon.catalog as catalog

    waits = []
    monkeypatch.setattr(catalog.time, "sleep", waits.append)
    calls = {"n": 0}

    def flaky(chunk):
        calls["n"] += 1
        if calls["n"] < 3:
            raise errors.TooManyRequestsError("Rate limit exceeded")
        return ["ok"]

    assert catalog._call(flaky, ["B0"]) == ["ok"]
    assert waits == [10, 30]

    def always(chunk):
        raise errors.TooManyRequestsError("Rate limit exceeded")

    import pytest
    with pytest.raises(errors.TooManyRequestsError):
        catalog._call(always, ["B0"])


def test_amazon_check_prints_fields_and_reasons(monkeypatch, capsys):
    from types import SimpleNamespace

    import amzagent.amazon.catalog as catalog
    import amzagent.amazon_check as check
    from amzagent.config import Settings

    item = SimpleNamespace(
        asin="B0TEST", detail_page_url="https://www.amazon.com/dp/B0TEST?tag=t-20",
        item_info=SimpleNamespace(title=SimpleNamespace(display_value="Smart Plug"),
                                  by_line_info=None, features=None, classifications=None),
        offers_v2=None, customer_reviews=None, images=None, browse_node_info=None)

    class FakeApi:
        def search_items(self, **kw):
            return SimpleNamespace(items=[item])

    class FakeCatalog:
        def __init__(self, s):
            self._api = FakeApi()

    monkeypatch.setattr(check, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(catalog, "CreatorsApiCatalog", FakeCatalog)
    assert check.main(["smart", "plug"]) == 0
    out = capsys.readouterr().out
    assert "found: 1" in out and "B0TEST" in out and "no image" in out
    assert "customer_reviews: None" in out
