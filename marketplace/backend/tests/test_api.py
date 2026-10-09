from datetime import datetime, timedelta, timezone

from servio.db import connect


def make_specialist(client, login, service_id, phone="+998900000002", city="tashkent", **extra):
    h = login(phone, name="Иван", role="specialist", city=city)
    r = client.put("/me/specialist", headers=h, json={
        "bio": "Опыт 10 лет", "experience_years": 10, "price_from": 1500,
        "category_ids": [service_id], **extra,
    })
    assert r.status_code == 200, r.text
    return h


def make_order(client, h, service_id, **extra):
    r = client.post("/orders", headers=h, json={
        "category_id": service_id, "title": "Нужен репетитор", "city": "tashkent", **extra,
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_login_flow_normalizes_phone_and_rejects_bad_code(client):
    sent = client.post("/auth/request-code", json={"phone": "(90) 123-45-67"}).json()
    assert sent["phone"] == "+998901234567"
    bad = client.post("/auth/verify", json={"phone": "+998 90 123 45 67", "code": "xxxx"})
    assert bad.status_code == 400
    ok = client.post("/auth/verify", json={"phone": "998901234567", "code": sent["debug_code"]})
    assert ok.status_code == 200
    assert ok.json()["is_new"] is True
    # code is single-use
    again = client.post("/auth/verify", json={"phone": "998901234567", "code": sent["debug_code"]})
    assert again.status_code == 400


def test_rejects_non_uzbek_numbers(client):
    for phone in ["+7 999 123 45 67", "12345", "+998 90 123 45"]:
        assert client.post("/auth/request-code", json={"phone": phone}).status_code == 422


def test_code_locks_after_too_many_attempts(client, settings):
    code = client.post("/auth/request-code", json={"phone": "+998900000001"}).json()["debug_code"]
    for _ in range(settings.code_max_attempts):
        client.post("/auth/verify", json={"phone": "+998900000001", "code": "0000" if code != "0000" else "1111"})
    r = client.post("/auth/verify", json={"phone": "+998900000001", "code": code})
    assert r.status_code == 429


def test_requires_auth(client):
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_categories_are_seeded_in_both_languages(client):
    ru = client.get("/categories").json()
    uz = client.get("/categories", headers={"Accept-Language": "uz"}).json()
    assert len(ru) >= 10 and all(s["children"] for s in ru)
    assert ru[0]["name"] == "Репетиторы" and uz[0]["name"] == "Repetitorlar"
    assert [s["id"] for s in ru] == [s["id"] for s in uz]


def test_cities_and_errors_are_localized(client, login):
    uz = client.get("/cities", headers={"Accept-Language": "uz"}).json()
    assert {"id": "fergana", "name": "Farg'ona"} in uz
    h = login("+998900000001")
    r = client.patch("/me", headers={**h, "Accept-Language": "uz"}, json={"city": "Москва"})
    assert r.status_code == 422
    assert r.json()["detail"] == "Ro'yxatdan shaharni tanlang"
    r = client.patch("/me", headers=h, json={"city": "Москва"})
    assert r.json()["detail"] == "Выберите город из списка"


def test_user_language_follows_app(client, login):
    h = login("+998900000001")
    assert client.get("/me", headers={**h, "Accept-Language": "ru"}).json()["lang"] == "ru"
    assert client.get("/me", headers={**h, "Accept-Language": "uz-UZ"}).json()["lang"] == "uz"


def test_becoming_specialist_starts_trial(client, login, settings):
    h = login("+998900000002", role="specialist")
    until = client.get("/me", headers=h).json()["subscription_until"]
    assert until is not None
    days = (datetime.fromisoformat(until) - datetime.now(timezone.utc)).days
    assert days == settings.trial_days - 1


def test_full_order_lifecycle(client, login, service_id):
    client_h = login("+998900000001", name="Мария", city="tashkent")
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
    client_h = login("+998900000001", city="samarkand")
    spec_h = make_specialist(client, login, service_id, city="tashkent")
    make_order(client, client_h, service_id, city="samarkand")
    assert client.get("/orders/feed", headers=spec_h).json() == []
    make_order(client, client_h, service_id, city="samarkand", remote=True)
    assert len(client.get("/orders/feed", headers=spec_h).json()) == 1


def test_responding_requires_subscription(client, login, service_id, settings):
    client_h = login("+998900000001")
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
    h = login("+998900000002", role="specialist")
    trial_end = datetime.fromisoformat(client.get("/me", headers=h).json()["subscription_until"])
    out = client.post("/subscription/checkout", headers=h, json={"plan_id": "month"}).json()
    assert datetime.fromisoformat(out["subscription_until"]) == trial_end + timedelta(days=30)


def test_other_users_cannot_read_chat_or_closed_order(client, login, service_id):
    client_h = login("+998900000001")
    spec_h = make_specialist(client, login, service_id)
    stranger = login("+998900000003")
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
    uz_card = client.get("/specialists", headers={"Accept-Language": "uz"}).json()[0]
    assert uz_card["categories"][0]["name"] == "Ingliz tili"
    assert len(client.get("/specialists?city=tashkent").json()) == 1
    assert client.get("/specialists?city=nukus").json() == []
    assert len(client.get("/specialists?q=10 лет").json()) == 1


def test_order_requires_leaf_category(client, login):
    h = login("+998900000001")
    section_id = client.get("/categories").json()[0]["id"]
    r = client.post("/orders", headers=h, json={"category_id": section_id, "title": "Что-то"})
    assert r.status_code == 422
