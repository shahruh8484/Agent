from fbadsagent.models import LandingPageConfig
from fbadsagent.web.landing_store import LandingPageStore, slugify


def test_slugify():
    assert slugify("Wireless Earbuds Pro!") == "wireless-earbuds-pro"
    assert slugify("   ") == "landing"


def make_page(slug="earbuds") -> LandingPageConfig:
    return LandingPageConfig(
        slug=slug,
        title="Wireless Earbuds Pro",
        headline="Hear Every Detail",
        subheadline="Premium audio, all day long.",
        benefits=["40h battery", "ANC"],
        cta_text="Get Yours",
        cpa_network="traff-hub",
        campaign_hash="6c9c0e1f",
    )


def test_add_and_list_page(tmp_path):
    store = LandingPageStore(tmp_path / "lp.json")
    store.add_page(make_page())

    pages = store.list_pages()
    assert len(pages) == 1
    assert pages[0].slug == "earbuds"
    assert pages[0].campaign_hash == "6c9c0e1f"


def test_get_page_returns_none_when_missing(tmp_path):
    store = LandingPageStore(tmp_path / "lp.json")
    assert store.get_page("nope") is None


def test_add_page_upserts_by_slug(tmp_path):
    store = LandingPageStore(tmp_path / "lp.json")
    store.add_page(make_page())
    updated = make_page()
    updated.headline = "New Headline"
    store.add_page(updated)

    pages = store.list_pages()
    assert len(pages) == 1
    assert pages[0].headline == "New Headline"


def test_remove_page(tmp_path):
    store = LandingPageStore(tmp_path / "lp.json")
    store.add_page(make_page("a"))
    store.add_page(make_page("b"))
    store.remove_page("a")

    assert [p.slug for p in store.list_pages()] == ["b"]
