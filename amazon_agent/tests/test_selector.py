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
