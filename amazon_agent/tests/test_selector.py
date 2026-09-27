from amzagent.selection.selector import score, select_products
from tests.conftest import make_product


def test_rejects_with_reasons_and_ranks_best_first():
    products = [
        make_product("LOWRATE", rating=3.5),
        make_product("FEWREV", reviews=10),
        make_product("NOPRICE", price=None),
        make_product("NOIMG", image_url=""),
        make_product("OK_SMALL", reviews=200),
        make_product("OK_BIG", reviews=50000),
        make_product("OK_BIG", reviews=50000),  # duplicate
    ]
    selected, rejected = select_products(products, limit=10, min_rating=4.0, min_reviews=100)
    assert [p.asin for p in selected] == ["OK_BIG", "OK_SMALL"]
    assert set(rejected) == {"LOWRATE", "FEWREV", "NOPRICE", "NOIMG"}
    assert "reviews" in rejected["FEWREV"]


def test_limit_and_discount_bonus():
    a = make_product("A", reviews=1000)
    b = make_product("B", reviews=1000, savings_percent=40)
    assert score(b) > score(a)
    selected, _ = select_products([a, b], limit=1, min_rating=4.0, min_reviews=100)
    assert [p.asin for p in selected] == ["B"]


def test_without_api_reviews_the_pasted_page_or_sales_rank_decides():
    # API gave no reviews; the pasted page did: filter and rank on that.
    weak = make_product("WEAK", rating=None, reviews=0)
    weak.hint_rating, weak.hint_reviews = 3.6, 5000
    good = make_product("GOOD", rating=None, reviews=0)
    good.hint_rating, good.hint_reviews = 4.6, 12000
    # No reviews anywhere: judged on sales rank alone, not rejected.
    ranked = make_product("RANKED", rating=None, reviews=0, sales_rank=50)
    selected, rejected = select_products([weak, good, ranked], limit=10, min_rating=4.0,
                                         min_reviews=100)
    assert [p.asin for p in selected] == ["GOOD", "RANKED"]
    assert "rating 3.6" in rejected["WEAK"]
