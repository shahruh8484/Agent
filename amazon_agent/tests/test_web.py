from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
from fastapi.testclient import TestClient

from amzagent.agent.runner import Deps, run_cycle
from amzagent.web.app import create_app
from tests.conftest import FakeCatalog, FakeLLM, make_product


@pytest.fixture
def site(settings, store):
    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    niche = store.add_niche("earbuds")
    old = make_product("OLD", reviews=500)
    old.fetched_at = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    products = [make_product("NEW", reviews=5000, price=19.99), old]
    run_cycle(Deps(settings=settings, store=store, catalog=FakeCatalog(products), llm=FakeLLM()))
    client = TestClient(create_app(settings, store, start_loop=False))
    return client, niche


def test_site_pages_and_tracking(site, store):
    client, niche = site
    home = client.get("/s/earbuds/")
    assert home.status_code == 200
    assert "Sound Picks" in home.text and "As an Amazon Associate" in home.text

    page = client.get("/s/earbuds/p/NEW?c=1&z=555")
    assert page.status_code == 200
    assert "$19.99" in page.text and "Price as of" in page.text
    assert "Choosing the right earbuds" in page.text and "Check battery life" in page.text
    assert 'href="/go/earbuds/NEW?c=1&amp;z=555"' in page.text

    go = client.get("/go/earbuds/NEW?c=1&z=555", follow_redirects=False)
    assert go.status_code == 302 and go.headers["location"].endswith("tag=test-20")
    assert store.count_events(1, "visit") == 1 and store.count_events(1, "click") == 1
    assert store.events_by_zone(1, "click") == {"555": 1}


def test_stale_price_is_hidden(site):
    client, _ = site
    page = client.get("/s/earbuds/p/OLD")
    assert page.status_code == 200 and "Price as of" not in page.text


def test_unsafe_zone_values_are_dropped(site, store):
    client, _ = site
    client.get("/go/earbuds/NEW?c=2&z=<script>")
    assert store.events_by_zone(2, "click") == {}


def test_unknown_pages_404(site):
    client, _ = site
    assert client.get("/s/nope/").status_code == 404
    assert client.get("/s/earbuds/p/NOPE").status_code == 404
    assert client.get("/media/earbuds/..%2F..%2Famzagent.db").status_code == 404


def test_media_served(site):
    client, _ = site
    resp = client.get("/media/earbuds/NEW-c1-icon.png")
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"


def test_dashboard_requires_login(site):
    client, _ = site
    assert client.get("/admin", follow_redirects=False).status_code == 303
    assert client.post("/run", follow_redirects=False).headers["location"] == "/login"
    assert client.post("/login", data={"username": "admin", "password": "bad"}).status_code == 401
    client.post("/login", data={"username": "admin", "password": "pw"})
    dash = client.get("/admin")
    assert dash.status_code == 200 and "Sound Picks" in dash.text and "Тестовый режим" in dash.text


def test_discover_requires_login(site):
    client, _ = site
    assert client.post("/discover", data={"count": "2"}, follow_redirects=False) \
        .headers["location"] == "/login"
    client.post("/login", data={"username": "admin", "password": "pw"})
    assert "Подобрать сам" in client.get("/admin").text


def test_public_home_lists_sites(site):
    client, _ = site
    home = client.get("/")
    assert home.status_code == 200
    assert "Sound Picks" in home.text and 'href="/s/earbuds/p/NEW"' in home.text
    assert "Amazon Associate" in home.text and "Войти" not in home.text


def test_login_lands_on_admin(site):
    client, _ = site
    resp = client.post("/login", data={"username": "admin", "password": "pw"},
                       follow_redirects=False)
    assert resp.headers["location"] == "/admin"
