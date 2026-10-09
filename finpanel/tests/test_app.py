import pytest
from fastapi.testclient import TestClient

from finance import calc
from finance.app import create_app
from finance.assistant import AssistantError, apply_action, build_context, describe_action, history_to_messages
from finance.config import Settings
from finance.security import hash_password
from tests.conftest import TODAY, FakeBackend


@pytest.fixture
def settings(tmp_path):
    return Settings(
        secret_key="test-secret",
        admin_username="admin",
        admin_password_hash=hash_password("pass123"),
        session_https_only=False,
        data_dir=str(tmp_path),
        anthropic_api_key="test-key",
    )


def make_client(settings, repo, backend=None):
    app = create_app(settings, repo=repo, backend_factory=lambda: backend or FakeBackend("ok"), today_fn=lambda: TODAY)
    client = TestClient(app)
    return client


def login(client):
    r = client.post("/login", data={"username": "admin", "password": "pass123"}, follow_redirects=False)
    assert r.status_code == 302


def test_requires_login(settings, repo):
    client = make_client(settings, repo)
    for path in ["/", "/traffic", "/product", "/webs", "/money", "/settings", "/chat"]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/login"
    assert client.post("/chat/send", data={"message": "hi"}).status_code == 401


def test_wrong_password(settings, repo):
    client = make_client(settings, repo)
    r = client.post("/login", data={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_all_pages_render_empty_and_with_data(settings, repo):
    client = make_client(settings, repo)
    login(client)
    for path in ["/", "/traffic", "/product", "/webs", "/money", "/settings", "/chat", "/?period=all", "/traffic?period=7d"]:
        assert client.get(path).status_code == 200, path

    web = repo.add_web("Sanya")
    adv = repo.add_advertiser("Ahmed")
    link = repo.add_link(web, adv, "Glyco", "2026-10-01", "approve", 10, 30, "lead", 2)
    repo.upsert_traffic_stat(link, "2026-10-08", 100, 100, 20)
    product = repo.add_product("Glycofort", 2.2, 1070, "2026-10-01")
    o = repo.add_order("2026-10-02", product, 5, 990_000, web_id=web)
    repo.set_order_status(o, "delivered", "2026-10-05")
    repo.add_expense("2026-10-05", "general", "Сервер", 20)

    for path in ["/", "/traffic", "/product", "/webs", "/money", "/?period=all"]:
        r = client.get(path + ("&" if "?" in path else "?") + "period=all")
        assert r.status_code == 200, path
    home = client.get("/?period=all").text
    assert "Вы в плюсе" in home


def test_forms_create_records(settings, repo):
    client = make_client(settings, repo)
    login(client)
    client.post("/traffic/advertiser", data={"name": "Ahmed", "terms": "предоплата"})
    client.post("/webs/add", data={"name": "Sanya", "next": "/traffic"})
    web = repo.find_web("Sanya")["id"]
    adv = repo.find_advertiser("Ahmed")["id"]
    r = client.post("/traffic/link", data={
        "web_id": web, "advertiser_id": adv, "offer": "", "valid_from": "2026-10-01",
        "adv_pay_type": "approve", "adv_rate": 10, "guarantee_pct": 30, "web_pay_type": "lead", "web_rate": 2,
    }, follow_redirects=False)
    assert "error" not in r.headers["location"]
    link = repo.links()[0]["id"]
    client.post("/traffic/stat", data={"link_id": link, "date": "2026-10-08", "leads": 100, "valid": "", "approves": 20})
    assert repo.traffic_stats()[0]["valid"] == 100  # blank valid = leads

    client.post("/money/payment", data={"party": f"advertiser:{adv}", "date": "2026-10-01", "amount": 500, "currency": "usd"})
    client.post("/money/payment", data={"party": "courier", "date": "2026-10-01", "amount": 115000, "currency": "uzs"})
    pays = repo.payments()
    assert {p["party_type"] for p in pays} == {"advertiser", "courier"}
    courier = next(p for p in pays if p["party_type"] == "courier")
    assert courier["direction"] == "product" and courier["amount_usd"] == pytest.approx(10)

    client.post("/product/add", data={"name": "Glycofort", "unit_cost_usd": 2.2, "initial_stock": 1070})
    product = repo.find_product("Glycofort")["id"]
    client.post("/product/order", data={"product_id": product, "ship_date": "2026-10-02", "qty": 5,
                                        "amount_uzs": 990000, "delivery_uzs": 0, "web_id": "", "count": 3})
    ids = [o["id"] for o in repo.orders()]
    assert len(ids) == 3
    client.post("/product/order/status", data={"order_ids": f"{ids[0]}, {ids[1]}", "status": "delivered", "date": "2026-10-05", "delivery_uzs": ""})
    assert sum(1 for o in repo.orders() if o["status"] == "delivered") == 2

    r = client.post("/settings", data={"usd_uzs_rate": 12000, "operator_pct": 15, "tax_pct": 0, "restock_days": 10, "advertiser_low_days": 2}, follow_redirects=False)
    assert repo.usd_uzs_rate() == 12000


def test_settings_shows_openai_provider(settings, repo):
    settings.anthropic_api_key = ""
    settings.openai_api_key = "sk-test"
    client = make_client(settings, repo)
    login(client)
    assert "Подключён: OpenAI (gpt-4o)" in client.get("/settings").text


def test_form_error_is_shown(settings, repo):
    client = make_client(settings, repo)
    login(client)
    r = client.post("/money/expense", data={"direction": "general", "category": "X", "date": "2026-10-01", "amount": -5}, follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_chat_proposes_then_applies(settings, repo):
    repo.add_advertiser("Ahmed")
    backend = FakeBackend(
        "Вот что внесу — подтверди:",
        [{"name": "add_payment", "input": {"party_type": "advertiser", "party_name": "Ahmed", "direction": "traffic",
                                           "amount": 500, "currency": "usd", "date": "2026-10-10"}}],
    )
    client = make_client(settings, repo, backend)
    login(client)
    r = client.post("/chat/send", data={"message": "Ахмед скинул 500$"})
    assert r.status_code == 200
    body = r.json()
    assert "500" in body["actions"][0]
    assert repo.payments() == []  # nothing written before confirmation

    system, messages = backend.calls[0]
    assert "ТЕКУЩИЕ ДАННЫЕ" in system and "Ahmed" in system
    assert messages[-1] == {"role": "user", "content": "Ахмед скинул 500$"}

    page = client.get("/chat").text
    assert "Подтвердить" in page

    r = client.post(f"/chat/{body['id']}/apply")
    assert r.status_code == 200 and r.json()["failed"] == 0
    assert repo.payments()[0]["amount_usd"] == 500
    assert client.post(f"/chat/{body['id']}/apply").status_code == 409  # not twice


def test_chat_reject(settings, repo):
    backend = FakeBackend("", [{"name": "add_web", "input": {"name": "Vasya"}}])
    client = make_client(settings, repo, backend)
    login(client)
    msg_id = client.post("/chat/send", data={"message": "добавь веба Vasya"}).json()["id"]
    assert client.post(f"/chat/{msg_id}/reject").status_code == 200
    assert repo.webs() == []


def test_chat_with_screenshot(settings, repo):
    backend = FakeBackend("Вижу 120 лидов.")
    client = make_client(settings, repo, backend)
    login(client)
    r = client.post("/chat/send", data={"message": ""}, files={"image": ("s.png", b"\x89PNG fake", "image/png")})
    assert r.status_code == 200
    content = backend.calls[0][1][-1]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    bad = client.post("/chat/send", data={"message": ""}, files={"image": ("s.txt", b"x", "text/plain")})
    assert bad.status_code == 400


def test_chat_backend_error(settings, repo):
    def broken():
        raise AssistantError("ANTHROPIC_API_KEY не задан")

    app = create_app(settings, repo=repo, backend_factory=broken, today_fn=lambda: TODAY)
    client = TestClient(app)
    login(client)
    r = client.post("/chat/send", data={"message": "hi"})
    assert r.status_code == 400 and "ANTHROPIC_API_KEY" in r.json()["error"]


def test_apply_action_full_flow(repo):
    apply_action(repo, "add_web", {"name": "Sanya"})
    apply_action(repo, "add_advertiser", {"name": "Ahmed"})
    apply_action(repo, "add_link", {"web": "Sanya", "advertiser": "Ahmed", "offer": "", "valid_from": "2026-10-01",
                                    "adv_pay_type": "approve", "adv_rate": 10, "guarantee_pct": 30,
                                    "web_pay_type": "lead", "web_rate": 2})
    apply_action(repo, "add_traffic_stat", {"web": "sanya", "advertiser": "AHMED", "offer": "", "date": "2026-10-08",
                                            "leads": 100, "valid": 100, "approves": 20})
    apply_action(repo, "add_product", {"name": "Glycofort", "unit_cost_usd": 2.2, "initial_stock": 1070, "date": "2026-10-01"})
    apply_action(repo, "add_order", {"product": "Glycofort", "qty": 5, "amount_uzs": 990000, "delivery_uzs": 0,
                                     "ship_date": "2026-10-02", "web": "Sanya"})
    order_id = repo.orders()[0]["id"]
    apply_action(repo, "set_order_status", {"order_id": order_id, "status": "delivered", "date": "2026-10-05", "delivery_uzs": -1})
    apply_action(repo, "add_payment", {"party_type": "courier", "party_name": "", "direction": "product",
                                       "amount": 990000, "currency": "uzs", "date": "2026-10-07"})
    apply_action(repo, "add_expense", {"direction": "general", "category": "Сервер", "amount": 20, "currency": "usd", "date": "2026-10-07"})
    apply_action(repo, "add_stock_purchase", {"product": "Glycofort", "qty": 500, "cost_usd": 1100, "date": "2026-10-07"})
    apply_action(repo, "update_settings", {"usd_uzs_rate": 11600, "operator_pct": -1, "tax_pct": -1})

    s = calc.summary(repo, None, None, TODAY)
    assert s.traffic.total.adv_amount == pytest.approx(300)
    assert s.product.delivered == 1
    assert calc.courier_balance(repo).owed_uzs == pytest.approx(0)
    assert repo.settings()["usd_uzs_rate"] == 11600
    assert repo.settings()["operator_pct"] == 15

    with pytest.raises(ValueError, match="не найден"):
        apply_action(repo, "add_payment", {"party_type": "web", "party_name": "Nobody", "direction": "traffic",
                                           "amount": 1, "currency": "usd", "date": "2026-10-07"})

    context = build_context(repo, TODAY)
    assert "Sanya → Ahmed" in context and "Glycofort" in context


def test_describe_and_history():
    assert "500" in describe_action("add_payment", {"party_type": "advertiser", "party_name": "Ahmed", "direction": "traffic",
                                                     "amount": 500, "currency": "usd", "date": "2026-10-10"})
    history = [
        {"role": "assistant", "content": "привет", "actions": [], "actions_status": None},
        {"role": "user", "content": "веб Вася", "actions": [], "actions_status": None},
        {"role": "assistant", "content": "ок", "actions": [{"name": "add_web", "input": {"name": "Вася"}}], "actions_status": "applied"},
    ]
    msgs = history_to_messages(history)
    assert msgs[0]["role"] == "user"
    assert "применено" in msgs[-1]["content"]


def test_accrual_form_and_chat_tool(settings, repo):
    maks = repo.add_web("Макс")
    client = make_client(settings, repo)
    login(client)
    r = client.post("/accruals/add", data={"party": f"web:{maks}", "direction": "traffic", "date": "2026-10-08",
                                           "amount": 15343, "note": "итог"}, follow_redirects=False)
    assert "error" not in r.headers["location"]
    page = client.get("/webs").text
    assert "Начисление вручную" in page and "$15,343.00" in page
    apply_action(repo, "add_accrual", {"party_type": "web", "party_name": "макс", "direction": "traffic",
                                       "amount": 100, "date": "2026-10-08"})
    assert len(repo.accruals()) == 2


def test_network_names_reach_assistant_context(repo):
    apply_action(repo, "add_web", {"name": "Макс", "terms": "раз в неделю"})
    apply_action(repo, "add_web", {"name": "макс", "network_name": "#106 t3amtm@yandex.ru"})
    apply_action(repo, "add_advertiser", {"name": "Санж", "network_name": "Khadya Nur"})
    assert len(repo.webs()) == 1  # same web, case-insensitive
    context = build_context(repo, TODAY)
    assert "Макс (раз в неделю; в сети: #106 t3amtm@yandex.ru)" in context
    assert "Санж (в сети: Khadya Nur)" in context


def test_custom_date_range(settings, repo):
    web = repo.add_web("Max")
    adv = repo.add_advertiser("Sanzh")
    link = repo.add_link(web, adv, "", "2026-07-01", "approve", 25, 0, "approve", 23)
    repo.upsert_traffic_stat(link, "2026-07-15", 100, 90, 11)
    repo.upsert_traffic_stat(link, "2026-08-15", 200, 180, 22)
    client = make_client(settings, repo)
    login(client)
    july = client.get("/traffic?from=2026-07-01&to=2026-07-31").text
    assert "2026-07-15" in july and "2026-08-15" not in july
    assert client.get("/traffic?from=bad&to=2026-07-31").status_code == 200  # falls back to preset


def test_purchases_listed(settings, repo):
    product = repo.add_product("Glycofort", 2.2, 1070, "2026-10-01")
    repo.add_stock_move("2026-08-28", product, 759, "purchase", cost_usd=1670, note="Данил")
    repo.add_stock_move("2026-10-09", product, -759, "adjust")
    assert [m["qty"] for m in repo.stock_purchases()] == [759]
    assert repo.stock_purchases("2026-09-01", None) == []
    client = make_client(settings, repo)
    login(client)
    money = client.get("/money?period=all").text
    assert "Закупки товара" in money and "Данил" in money and "$1,670.00" in money
    assert "Закупки товара за период: <b>$1,670.00</b>" in client.get("/product?period=all").text


def test_reconcile_route(settings, repo):
    maks = repo.add_web("Макс")
    repo.add_payment("2026-08-01", "traffic", "web", maks, 13148)
    client = make_client(settings, repo)
    login(client)
    assert "Сверка: выставить баланс" in client.get("/webs").text
    r = client.post("/reconcile", data={"party": f"web:{maks}", "direction": "traffic", "target": -2231}, follow_redirects=False)
    assert "Сверено" in r.headers["location"] or "%D0%A1%D0%B2%D0%B5%D1%80%D0%B5%D0%BD%D0%BE" in r.headers["location"]
    assert calc.web_balances(repo, TODAY, "traffic")[0].balance == pytest.approx(-2231)


def test_chat_shows_recent_messages_and_collapses_old(settings, repo):
    for i in range(25):
        repo.add_chat_message("user" if i % 2 == 0 else "assistant", f"сообщение номер {i}")
    repo.add_chat_message("assistant", "длинный ответ " * 40, [{"name": "add_web", "input": {"name": "Вася"}}])
    repo.set_chat_actions_status(repo.chat_messages()[-1]["id"], "applied", "✓ Веб Вася сохранён.")
    repo.add_chat_message("user", "последнее")
    client = make_client(settings, repo)
    login(client)
    page = client.get("/chat").text
    assert "Показать ранние сообщения (7)" in page
    assert "сообщение номер 0<" not in page and "сообщение номер 24" in page
    assert "Применено: 1 действ." in page and 'class="bubble long"' in page
    full = client.get("/chat?all=1").text
    assert "сообщение номер 0<" in full and "Показать ранние" not in full
