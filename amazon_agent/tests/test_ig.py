import bcrypt
from fastapi.testclient import TestClient

from amzagent.ig.lander import compliance_issues
from amzagent.web.app import create_app
from amzagent.web.ig_routes import KEY_FLAG, norm_domain
from tests.conftest import FakeLLM

BROWSER = {"user-agent": "Mozilla/5.0 (Linux; Android 14) Mobile Safari/537.36"}


def _client(settings, store):
    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    client = TestClient(create_app(settings, store, start_loop=False))
    client.post("/login", data={"username": "admin", "password": "pw"})
    return client


def _project(client, **extra):
    data = {"name": "BR bets", "brand": "Marca", "license_url": "https://marca.bet.br/",
            "offer_url": "https://partner.example/go?aff=7&sub1={click_id}&z={zone}",
            "payout": "40", "domain": "https://www.Apostas-Exemplo.com/", "country": "BR",
            "language": "pt", "offer": "Bônus de 100% até R$200, rollover 5x, depósito mínimo "
                                       "R$20 via PIX.", **extra}
    return client.post("/admin/ig/projects", data=data)


def test_tab_needs_login_and_is_linked(settings, store):
    anon = TestClient(create_app(settings, store, start_loop=False))
    assert anon.get("/admin/ig", follow_redirects=False).status_code == 303
    client = _client(settings, store)
    assert 'href="/admin/ig"' in client.get("/admin").text
    page = client.get("/admin/ig").text
    assert "Постбэк" in page and store.get_flag(KEY_FLAG) in page


def test_project_validation_and_domain(settings, store):
    client = _client(settings, store)
    bad = _project(client, offer_url="http://x")
    assert "Проект не сохранён" in bad.text
    settings.domain = "pushpannel.com"
    assert "отдельный домен" in _project(client, domain="pushpannel.com").text
    _project(client)
    p = store.list_ig_projects()[0]
    assert p["domain"] == "apostas-exemplo.com" and p["payout"] == 40
    assert "уже занят" in _project(client, name="dup").text
    assert norm_domain("HTTP://www.a.com:443/x") == "a.com"


def test_lander_on_own_domain_click_and_postback(settings, store, monkeypatch):
    import amzagent.web.ig_routes as ig_routes
    monkeypatch.setattr(ig_routes, "get_llm", lambda s: FakeLLM())
    client = _client(settings, store)
    _project(client)
    pid = store.list_ig_projects()[0]["id"]
    client.post(f"/admin/ig/projects/{pid}/write")
    anon = TestClient(create_app(settings, store, start_loop=False))
    host = {**BROWSER, "host": "apostas-exemplo.com"}

    # not reachable on the main domain for visitors, only on its own domain
    assert anon.get(f"/l/{pid}/", headers=BROWSER).status_code == 404
    page = anon.get("/?c=12&z=555", headers=host)
    assert page.status_code == 200 and "Apostas esportivas 18+" in page.text
    assert "Jogue com responsabilidade" in page.text and 'href="/go?c=12&amp;z=555"' in page.text
    assert anon.get("/admin", headers=host).status_code == 404  # nothing else on that domain
    assert "Disallow: /" in anon.get("/robots.txt", headers=host).text

    go = anon.get("/go?c=12&z=555&js=1", headers=host, follow_redirects=False)
    assert go.status_code == 302
    click_id = go.headers["location"].split("sub1=")[1].split("&")[0]
    assert len(click_id) == 16 and go.headers["location"].endswith("&z=555")
    bot = anon.get("/go", headers={"host": "apostas-exemplo.com", "user-agent": "curl/8"},
                   follow_redirects=False)
    assert bot.status_code == 403

    key = store.get_flag(KEY_FLAG)
    assert anon.get(f"/pb/ig?key=wrong&click_id={click_id}&event=ftd").status_code == 403
    assert anon.get(f"/pb/ig?key={key}&subid={click_id}&status=registration").text == "ok"
    assert anon.get(f"/pb/ig?key={key}&click_id={click_id}&goal=ftd").text == "ok"
    assert anon.get(f"/pb/ig?key={key}&click_id={click_id}&event=ftd").text == "duplicate"
    assert anon.get(f"/pb/ig?key={key}&click_id={click_id}&event=dep&sum=12,5").text == "ok"
    assert anon.get(f"/pb/ig?key={key}&click_id=nope&event=ftd").text.startswith("unknown")

    st = store.ig_stats(pid)
    assert (st["visit"], st["click"], st["bot"], st["reg"], st["ftd"], st["dep"]) == \
        (1, 1, 1, 1, 1, 1)
    assert st["revenue"] == 52.5  # CPA from the project + the redeposit sum
    assert store.ig_zone_stats(pid)[0]["zone"] == "555"
    panel = client.get("/admin/ig").text
    assert "$52.50" in panel and "555" in panel

    assert anon.get("/caddy/ask?domain=www.apostas-exemplo.com").status_code == 200
    assert anon.get("/caddy/ask?domain=other.com").status_code == 404


def test_owner_preview_is_not_counted(settings, store):
    client = _client(settings, store)
    _project(client, domain="")
    pid = store.list_ig_projects()[0]["id"]
    assert client.get(f"/l/{pid}/", headers=BROWSER).status_code == 200
    go = client.get(f"/l/{pid}/go", headers=BROWSER, follow_redirects=False)
    assert "sub1=preview" in go.headers["location"]
    st = store.ig_stats(pid)
    assert st["visit"] == 0 and st["click"] == 0
    assert "Не указан домен" in client.get("/admin/ig").text


def test_lander_edit_and_forbidden_words(settings, store):
    client = _client(settings, store)
    _project(client)
    pid = store.list_ig_projects()[0]["id"]
    client.post(f"/admin/ig/projects/{pid}/lander", data={
        "headline": "Ganhe dinheiro garantido", "intro": "x", "steps": "Um\n\nDois",
        "faq": "Como sacar? | Via PIX\nsem resposta"})
    page = client.get("/admin/ig").text
    assert "Запрещённые" in page and "ganhe dinheiro" in page
    assert compliance_issues("Aposte com calma", "pt") == []
    import json
    lander = json.loads(store.get_ig_project(pid)["lander"])
    assert lander["steps"] == ["Um", "Dois"] and len(lander["faq"]) == 1
