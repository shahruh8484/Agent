from datetime import datetime, timedelta, timezone

from servio.db import connect


def make_specialist(client, login, service_id, phone="+79990000002", city="Москва", **extra):
    h = login(phone, name="Иван", role="specialist", city=city)
    r = client.put("/me/specialist", headers=h, json={
        "bio": "Опыт 10 лет", "experience_years": 10, "price_from": 1500,
        "category_ids": [service_id], **extra,
    })
    assert r.status_code == 200, r.text
    return h


def make_order(client, h, service_id, **extra):
    r = client.post("/orders", headers=h, json={
        "category_id": service_id, "title": "Нужен репетитор", "city": "Москва", **extra,
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_login_flow_normalizes_phone_and_rejects_bad_code(client):
    sent = client.post("/auth/request-code", json={"phone": "8 (999) 123-45-67"}).json()
    assert sent["phone"] == "+79991234567"
    bad = client.post("/auth/verify", json={"phone": "+7 999 123 45 67", "code": "xxxx"})
    assert bad.status_code == 400
    ok = client.post("/auth/verify", json={"phone": "89991234567", "code": sent["debug_code"]})
    assert ok.status_code == 200
    assert ok.json()["is_new"] is True
    # code is single-use
    again = client.post("/auth/verify", json={"phone": "89991234567", "code": sent["debug_code"]})
    assert again.status_code == 400


def test_code_locks_after_too_many_attempts(client, settings):
    code = client.post("/auth/request-code", json={"phone": "+79990000001"}).json()["debug_code"]
    for _ in range(settings.code_max_attempts):
        client.post("/auth/verify", json={"phone": "+79990000001", "code": "0000" if code != "0000" else "1111"})
    r = client.post("/auth/verify", json={"phone": "+79990000001", "code": code})
    assert r.status_code == 429


def test_requires_auth(client):
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_categories_are_seeded(client):
    sections = client.get("/categories").json()
    assert len(sections) >= 10
    assert all(s["children"] for s in sections)


def test_becoming_specialist_starts_trial(client, login, settings):
    h = login("+79990000002", role="specialist")
    until = client.get("/me", headers=h).json()["subscription_until"]
    assert until is not None
    days = (datetime.fromisoformat(until) - datetime.now(timezone.utc)).days
    assert days == settings.trial_days - 1


def test_full_order_lifecycle(client, login, service_id):
    client_h = login("+79990000001", name="Мария", city="Москва")
    spec_h = make_specialist(client, login, service_id)
    order = make_order(client, client_h, service_id)

    feed = client.get("/orders/feed", headers=spec_h).json()
    assert [o["id"] for o in feed] == [order["id"]]
    assert feed[0]["responded"] is False

    resp = client.post(f"/orders/{order['id']}/responses", headers=spec_h,
                       json={"message": "Готов помочь", "price": 2000}).json()
    dup = client.post(f"/orders/{order['id']}/responses", headers=spec_h, json={"message": "ещё"})
    assert dup.status_code == 409

    responses = client.get(f"/orders/{order['id']}/responses", headers=client_h).json()
    assert responses[0]["specialist"]["name"] == "Иван"
    assert client.get(f"/orders/{order['id']}/responses", headers=spec_h).status_code == 403

    # chat was opened with the response as first message
    chats = client.get("/chats", headers=client_h).json()
    assert chats[0]["other_name"] == "Иван" and chats[0]["last_text"] == "Готов помочь"
    client.post(f"/chats/{resp['chat_id']}/messages", headers=client_h, json={"text": "Когда сможете?"})
    msgs = client.get(f"/chats/{resp['chat_id']}/messages", headers=spec_h).json()
    assert [m["text"] for m in msgs] == ["Готов помочь", "Когда сможете?"]
    after = client.get(f"/chats/{resp['chat_id']}/messages?after_id={msgs[0]['id']}", headers=spec_h).json()
    assert len(after) == 1

    assert client.post(f"/orders/{order['id']}/review", headers=client_h,
                       json={"rating": 5}).status_code == 400
    chosen = client.post(f"/orders/{order['id']}/choose", headers=client_h,
                         json={"response_id": resp["id"]}).json()
    assert chosen["status"] == "in_progress"
    assert client.get("/orders/feed", headers=spec_h).json() == []
    assert client.get("/orders/assigned", headers=spec_h).json()[0]["id"] == order["id"]

    client.post(f"/orders/{order['id']}/complete", headers=client_h)
    r = client.post(f"/orders/{order['id']}/review", headers=client_h, json={"rating": 5, "text": "Супер"})
    assert r.status_code == 200
    spec_id = client.get("/me", headers=spec_h).json()["id"]
    card = client.get(f"/specialists/{spec_id}").json()
    assert card["rating"] == 5 and card["reviews_count"] == 1
    assert card["reviews"][0]["text"] == "Супер"


def test_feed_filters_by_city_and_remote(client, login, service_id):
    client_h = login("+79990000001", city="Казань")
    spec_h = make_specialist(client, login, service_id, city="Москва")
    make_order(client, client_h, service_id, city="Казань")
    assert client.get("/orders/feed", headers=spec_h).json() == []
    make_order(client, client_h, service_id, city="Казань", remote=True)
    assert len(client.get("/orders/feed", headers=spec_h).json()) == 1


def test_responding_requires_subscription(client, login, service_id, settings):
    client_h = login("+79990000001")
    spec_h = make_specialist(client, login, service_id)
    order = make_order(client, client_h, service_id)
    db = connect(settings.database_path)
    db.execute("DELETE FROM subscriptions")
    db.commit()
    db.close()

    r = client.post(f"/orders/{order['id']}/responses", headers=spec_h, json={"message": "Привет"})
    assert r.status_code == 402

    checkout = client.post("/subscription/checkout", headers=spec_h, json={"plan_id": "month"}).json()
    assert checkout["status"] == "succeeded"
    until = datetime.fromisoformat(checkout["subscription_until"])
    assert until - datetime.now(timezone.utc) > timedelta(days=29)
    r = client.post(f"/orders/{order['id']}/responses", headers=spec_h, json={"message": "Привет"})
    assert r.status_code == 200


def test_subscription_extends_from_current_end(client, login):
    h = login("+79990000002", role="specialist")
    trial_end = datetime.fromisoformat(client.get("/me", headers=h).json()["subscription_until"])
    out = client.post("/subscription/checkout", headers=h, json={"plan_id": "month"}).json()
    assert datetime.fromisoformat(out["subscription_until"]) == trial_end + timedelta(days=30)


def test_other_users_cannot_read_chat_or_closed_order(client, login, service_id):
    client_h = login("+79990000001")
    spec_h = make_specialist(client, login, service_id)
    stranger = login("+79990000003")
    order = make_order(client, client_h, service_id)
    chat_id = client.post(f"/orders/{order['id']}/responses", headers=spec_h,
                          json={"message": "Привет"}).json()["chat_id"]
    assert client.get(f"/chats/{chat_id}/messages", headers=stranger).status_code == 404
    client.post(f"/orders/{order['id']}/close", headers=client_h)
    assert client.get(f"/orders/{order['id']}", headers=stranger).status_code == 404
    assert client.get(f"/orders/{order['id']}", headers=spec_h).status_code == 200


def test_specialist_search(client, login, service_id):
    make_specialist(client, login, service_id)
    section_id = client.get("/categories").json()[0]["id"]
    assert len(client.get(f"/specialists?category_id={section_id}").json()) == 1
    assert len(client.get("/specialists?city=Москва").json()) == 1
    assert client.get("/specialists?city=Омск").json() == []
    assert len(client.get("/specialists?q=10 лет").json()) == 1


def test_order_requires_leaf_category(client, login):
    h = login("+79990000001")
    section_id = client.get("/categories").json()[0]["id"]
    r = client.post("/orders", headers=h, json={"category_id": section_id, "title": "Что-то"})
    assert r.status_code == 422
