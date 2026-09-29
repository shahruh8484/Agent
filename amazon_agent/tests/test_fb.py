import io
import json

import bcrypt
from fastapi.testclient import TestClient
from PIL import Image

from amzagent.fb.client import (
    FacebookError,
    ad_account_id,
    check_connection,
    load_connection,
    pixel_code,
)
from amzagent.web.app import create_app
from tests.conftest import FakeLLM


def _client(settings, store):
    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    client = TestClient(create_app(settings, store, start_loop=False))
    client.post("/login", data={"username": "admin", "password": "pw"})
    return client


def test_tab_needs_login_and_is_linked_from_the_panel(settings, store):
    anon = TestClient(create_app(settings, store, start_loop=False))
    assert anon.get("/admin/fb", follow_redirects=False).status_code == 303
    client = _client(settings, store)
    assert 'href="/admin/fb"' in client.get("/admin").text
    page = client.get("/admin/fb").text
    assert "Подключение" in page and "Пиксель на лендинг" in page and "Проекты" in page


def test_connection_is_saved_and_the_token_never_shown(settings, store):
    client = _client(settings, store)
    client.post("/admin/fb/connection", data={"token": "EAAGsecret123456", "ad_account": "123",
                                              "page_id": "55", "pixel_id": "77"})
    conn = load_connection(store)
    assert conn["token"] == "EAAGsecret123456" and conn["pixel_id"] == "77"
    page = client.get("/admin/fb").text
    assert "EAAGsecret123456" not in page and "…123456" in page
    assert "fbq('init', '77')" in page.replace("&#39;", "'")
    client.post("/admin/fb/connection", data={"token": "", "ad_account": "act_9"})  # keep token
    assert load_connection(store)["token"] == "EAAGsecret123456"
    client.post("/admin/fb/connection", data={"forget_token": "1"})
    assert load_connection(store)["token"] == ""


def test_check_reports_each_part():
    class Fake:
        def get(self, path, **params):
            if path == "me":
                return {"id": "1", "name": "System User"}
            if path == "act_123":
                return {"name": "Main", "currency": "USD", "account_status": 1}
            raise FacebookError("Unsupported get request")

    conn = {"token": "x", "ad_account": "123", "page_id": "55", "pixel_id": ""}
    results = check_connection(conn, Fake())
    assert results[0] == ("Токен", True, "System User (id 1)")
    assert results[1] == ("Рекламный аккаунт", True, "Main · USD · активен")
    assert results[2][:2] == ("Страница", False)
    assert check_connection({"token": ""})[0][1] is False
    assert ad_account_id("123") == "act_123" and ad_account_id("act_5") == "act_5"
    base, lead = pixel_code("")
    assert "ВАШ_PIXEL_ID" in base and "Lead" in lead


def test_projects_texts_and_ad_images(settings, store, monkeypatch):
    import amzagent.web.fb_routes as fb_routes
    monkeypatch.setattr(fb_routes, "get_llm", lambda s: FakeLLM())
    client = _client(settings, store)

    bad = client.post("/admin/fb/projects", data={"name": "X", "lander_url": "ftp://x"})
    assert "Проект не сохранён" in bad.text and 'class="banner bad"' in bad.text
    client.post("/admin/fb/projects", data={
        "name": "Vitamin D", "lander_url": "https://land.example/", "country": "UZ",
        "language": "uz", "product": "Vitamin D3 2000 IU, 60 capsules, 150 000 so'm, COD.",
        "daily_budget": "15", "max_cpl": "2,5"})
    (p,) = store.list_fb_projects()
    assert p["daily_budget"] == 15 and p["max_cpl"] == 2.5 and p["country"] == "UZ"

    page = client.post(f"/admin/fb/projects/{p['id']}/texts", data={"language": "ru"}).text
    ads = store.list_fb_ads(p["id"])
    assert len(ads) == 3 and ads[0]["language"] == "ru" and "Variant 1" in page

    png = io.BytesIO()
    Image.new("RGB", (40, 40), (200, 30, 30)).save(png, "PNG")
    client.post(f"/admin/fb/ads/{ads[0]['id']}",
                data={"primary_text": "Edited", "headline": "H", "description": "D"},
                files={"image": ("a.png", png.getvalue(), "image/png")})
    ad = store.get_fb_ad(ads[0]["id"])
    assert ad["primary_text"] == "Edited" and ad["image"].endswith(".jpg")
    assert client.get(f"/admin/fb/media/{ad['image']}").status_code == 200
    assert client.get("/admin/fb/media/..%2F..%2Famzagent.db").status_code == 404
    rejected = client.post(f"/admin/fb/ads/{ads[1]['id']}", data={"primary_text": "x"},
                           files={"image": ("a.png", b"not an image", "image/png")})
    assert "Картинка не сохранена" in rejected.text

    client.post(f"/admin/fb/ads/{ads[2]['id']}/delete")
    assert len(store.list_fb_ads(p["id"])) == 2
    client.post(f"/admin/fb/projects/{p['id']}/delete")
    assert store.list_fb_projects() == [] and store.list_fb_ads(p["id"]) == []


def test_texts_need_a_product_description(settings, store):
    client = _client(settings, store)
    pid = store.add_fb_project(name="X", lander_url="https://x.example/")
    page = client.post(f"/admin/fb/projects/{pid}/texts", data={}).text
    assert "опишите товар" in page and json.dumps([]) == "[]"
