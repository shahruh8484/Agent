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
    assert "Импортировано" not in client.get("/").text  # shown once

    # Same name again adds to the same site instead of creating a new one.
    client.post("/import", data={"text": "ASIN: B0AAAAAAAA", "name": "top deals"})
    assert len(store.list_niches()) == 1
    assert len(store.list_niches()[0].asins) == 4

    page = client.post("/import", data={"text": "no asins here", "name": "X"})
    assert "не найдено ни одного ASIN" in page.text
