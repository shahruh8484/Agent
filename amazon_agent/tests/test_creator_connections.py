import sqlite3

import bcrypt
from fastapi.testclient import TestClient

from amzagent.agent.runner import Deps, run_cycle
from amzagent.amazon.creator_connections import parse_opportunities
from amzagent.store import Store
from amzagent.web.app import create_app
from tests.conftest import FakeLLM, make_product

# Text as it comes out of Ctrl+A / Ctrl+C on the Creator Connections page.
PAGE = """Recommended
Physician's CHOICE
Physician's CHOICE Fiber Gummies for GLP-...
4.4 (2,696)
41% off Limited time deal
$9.99
ASIN: B0FYZ9QQ9Z
Estimated EPC: Up to $2.50
Budget availability score: High
Accept
Recommended
Shark
Shark FlexStyle, Air Multi-Styler & Drying Sy...
4.6 (359)
$199.99
ASIN: B0DZ7RZ14S
Estimated EPC: Up to $1.10
Budget availability score: High
Accept
Miss Mouth's Messy Eater Stain Treater Spra...
-20% $7.97
ASIN: B01EIG6A4Q
Accept
"""


def test_parse_page_text():
    assert parse_opportunities(PAGE) == {
        "B0FYZ9QQ9Z": 2.5,
        "B0DZ7RZ14S": 1.1,
        "B01EIG6A4Q": None,  # no EPC line before the end
    }


def test_parse_plain_asin_list():
    assert parse_opportunities("b0fyz9qq9z, B0DZ7RZ14S\nnothing else") == {
        "B0FYZ9QQ9Z": None, "B0DZ7RZ14S": None,
    }


class ListCatalog:
    def __init__(self, products):
        self.products = {p.asin: p for p in products}
        self.searched = False

    def search(self, *a, **k):
        self.searched = True
        return []

    def get(self, asins):
        return [self.products[a].model_copy() for a in asins if a in self.products]


def test_curated_site_ranks_by_epc_and_skips_search(settings, store):
    niche = store.add_niche("Top Deals", asins={"HIGH": 2.5, "LOW": None, "BAD": 3.0})
    catalog = ListCatalog([
        make_product("HIGH", reviews=1000),
        make_product("LOW", reviews=3000),
        make_product("BAD", rating=3.2),  # fails the rating filter despite EPC
    ])
    run_cycle(Deps(settings=settings, store=store, catalog=catalog, llm=FakeLLM()))
    products = store.list_products(niche.id)
    assert [p.asin for p, _ in products] == ["HIGH", "LOW"]
    assert products[0][0].epc == 2.5
    assert not catalog.searched


def test_merge_keeps_known_epc(store):
    niche = store.add_niche("Top Deals", asins={"A": 2.0})
    assert store.merge_niche_asins(niche.id, {"A": None, "B": 1.0}) == 2
    assert store.get_niche(niche.id).asins == {"A": 2.0, "B": 1.0}


def test_old_database_is_migrated(tmp_path):
    db = sqlite3.connect(tmp_path / "amzagent.db")
    db.execute(
        "CREATE TABLE niches (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE NOT NULL,"
        " keywords TEXT NOT NULL, search_index TEXT NOT NULL DEFAULT 'All', language TEXT NOT"
        " NULL DEFAULT 'English', max_price REAL, enabled INTEGER NOT NULL DEFAULT 1,"
        " site_copy TEXT, created_at TEXT NOT NULL)"
    )
    db.execute("INSERT INTO niches (slug, keywords, created_at) VALUES ('x', 'x', 'now')")
    db.commit()
    db.close()
    store = Store(str(tmp_path))
    assert store.list_niches()[0].asins == {}


def test_import_endpoint(settings, store):
    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    client = TestClient(create_app(settings, store, start_loop=False))
    client.post("/login", data={"username": "admin", "password": "pw"})

    page = client.post("/import", data={"text": PAGE, "name": "Top Deals"})  # follows redirect
    niches = store.list_niches()
    assert len(niches) == 1 and len(niches[0].asins) == 3
    assert "Импортировано 3 товаров (2 с EPC)" in page.text
    assert "Импортировано" not in client.get("/admin").text  # shown once

    # Same name again adds to the same site instead of creating a new one.
    client.post("/import", data={"text": "ASIN: B0AAAAAAAA", "name": "top deals"})
    assert len(store.list_niches()) == 1
    assert len(store.list_niches()[0].asins) == 4

    page = client.post("/import", data={"text": "no asins here", "name": "X"})
    assert "не найдено ни одного ASIN" in page.text


from amzagent.amazon.catalog import CatalogError  # noqa: E402
from amzagent.amazon.creator_connections import parse_opportunity_details  # noqa: E402


def test_parse_card_details():
    d = parse_opportunity_details(PAGE)
    assert d["B0FYZ9QQ9Z"] == {"title": "Physician's CHOICE Fiber Gummies",
                               "brand": "Physician's CHOICE", "rating": 4.4, "reviews": 2696}
    assert d["B0DZ7RZ14S"]["title"] == "Shark FlexStyle, Air Multi-Styler & Drying"
    assert d["B0DZ7RZ14S"]["rating"] == 4.6


class DeniedCatalog:
    def search(self, *a, **k):
        raise CatalogError("AssociateNotEligible")

    def get(self, asins):
        raise CatalogError("AssociateNotEligible")


def _import(store):
    from amzagent.amazon.creator_connections import parse_opportunities
    return store.add_niche("Top Deals", asins=parse_opportunities(PAGE),
                           asin_meta=parse_opportunity_details(PAGE))


def test_fallback_builds_site_from_pasted_page(settings, store):
    settings.amazon_partner_tag = "screensoundlo-20"
    settings.min_reviews = 300
    niche = _import(store)
    deps = Deps(settings=settings, store=store, catalog=DeniedCatalog(), llm=FakeLLM())
    run_cycle(deps)

    products = store.list_products(niche.id)
    # Gummies: 2,696 reviews + $2.50 EPC ranks first, Shark (359 reviews,
    # $1.10) next; Miss Mouth's card has no rating line -> kept, ranked last.
    assert [p.asin for p, _ in products] == ["B0FYZ9QQ9Z", "B0DZ7RZ14S", "B01EIG6A4Q"]
    p = products[0][0]
    assert p.offline and p.image_url == "" and p.price is None and p.rating is None
    assert p.url.startswith("https://www.amazon.com/dp/B0FYZ9QQ9Z?ref=t_ac_spc_accepted_tile"
                            "&linkCode=tr1&tag=screensoundlo-20&linkId=B0FYZ9QQ9Z_")
    assert all(c is not None for _, c in products)
    assert any("fallback mode" in line for line in deps.log)

    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    client = TestClient(create_app(settings, store, start_loop=False))
    page = client.get("/s/top-deals/p/B0FYZ9QQ9Z").text
    assert 'class="ph"' in page and "Price as of" not in page and "ratings on Amazon" not in page
    assert "See current price, photos and customer reviews on Amazon" in page
    assert client.get("/go/top-deals/B0FYZ9QQ9Z", follow_redirects=False) \
        .headers["location"].split("&linkId=")[0].endswith("tag=screensoundlo-20")


def test_import_site_size_limits_the_shelf(settings, store):
    settings.amazon_partner_tag = "screensoundlo-20"
    settings.min_reviews = 300
    settings.import_site_size = 2
    niche = _import(store)
    run_cycle(Deps(settings=settings, store=store, catalog=DeniedCatalog(), llm=FakeLLM()))
    # the best two of the three that qualify
    assert [p.asin for p, _ in store.list_products(niche.id)] == ["B0FYZ9QQ9Z", "B0DZ7RZ14S"]


def test_api_recovery_replaces_fallback_pages(settings, store):
    settings.amazon_partner_tag = "t-20"
    niche = _import(store)
    run_cycle(Deps(settings=settings, store=store, catalog=DeniedCatalog(), llm=FakeLLM()))
    assert store.list_products(niche.id)[0][0].offline

    llm = FakeLLM()
    api = ListCatalog([make_product("B0FYZ9QQ9Z"), make_product("B0DZ7RZ14S")])
    run_cycle(Deps(settings=settings, store=store, catalog=api, llm=llm))
    products = store.list_products(niche.id)
    assert products and not any(p.offline for p, _ in products)
    assert all(p.image_url for p, _ in products)
    assert any("B0FYZ9QQ9Z" in prompt for prompt in llm.prompts)  # copy rewritten

    # A later API outage must not downgrade the full pages.
    run_cycle(Deps(settings=settings, store=store, catalog=DeniedCatalog(), llm=FakeLLM()))
    assert not any(p.offline for p, _ in store.list_products(niche.id))


def test_fallback_rejects_weak_cards(settings, store):
    settings.amazon_partner_tag = "t-20"
    settings.min_reviews = 1000  # Shark has 359
    niche = _import(store)
    run_cycle(Deps(settings=settings, store=store, catalog=DeniedCatalog(), llm=FakeLLM()))
    assert "B0DZ7RZ14S" not in [p.asin for p, _ in store.list_products(niche.id)]


def test_fallback_products_get_labelled_illustrations(settings, store):
    from tests.test_ai_creatives import FakePainter, SceneLLM

    settings.amazon_partner_tag = "t-20"
    settings.illustrations_per_run = 2
    settings.campaigns_per_site = 0  # no push creatives drawn in this test
    niche = _import(store)
    painter = FakePainter()
    deps = Deps(settings=settings, store=store, catalog=DeniedCatalog(), llm=SceneLLM(),
                painter=painter)
    run_cycle(deps)
    products = [p for p, _ in store.list_products(niche.id)]
    drawn = [p for p in products if p.illustration_url]
    assert len(drawn) == 2  # capped per run
    assert drawn[0].illustration_url.endswith(f"/media/top-deals/site-{drawn[0].asin}.jpg")
    assert any("1 left for later" in line for line in deps.log)

    # Next cycle rebuilds the fallback products: drawings are kept, the rest drawn.
    run_cycle(Deps(settings=settings, store=store, catalog=DeniedCatalog(), llm=SceneLLM(),
                   painter=painter))
    assert all(p.illustration_url for p, _ in store.list_products(niche.id))
    assert len(painter.prompts) == 3  # nothing redrawn

    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    client = TestClient(create_app(settings, store, start_loop=False))
    page = client.get(f"/s/top-deals/p/{drawn[0].asin}").text
    assert 'class="illu"' in page and "Illustration — not the actual product photo" in page
    assert "Illustration</span>" in client.get("/s/top-deals/").text
