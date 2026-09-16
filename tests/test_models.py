from fbadsagent.models import ProductInput


def test_search_query_uses_keywords_when_present():
    product = ProductInput(name="Widget", description="desc", keywords=["foo", "bar"])
    assert product.search_query() == "foo bar"


def test_search_query_falls_back_to_name():
    product = ProductInput(name="Widget", description="desc")
    assert product.search_query() == "Widget"
