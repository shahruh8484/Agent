import time

import pytest
from fastapi.testclient import TestClient

from familychef import app as app_mod
from familychef.accounts import Accounts
from familychef import admin

WEEK = {"region": "UZ", "budget": 650000, "adults": 2, "children": 1, "days": 7}
DAY = {**WEEK, "days": 1}


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    monkeypatch.delenv("FC_SECRET", raising=False)
    monkeypatch.setenv("FC_DATA_DIR", str(tmp_path))
    return Accounts(tmp_path)


@pytest.fixture
def client(accounts, monkeypatch):
    monkeypatch.setenv("FC_EMAIL_PROVIDER", "dev")
    monkeypatch.setenv("FC_LIMITS", "1")
    monkeypatch.delenv("FC_REQUIRE_LOGIN", raising=False)
    c = TestClient(app_mod.create_app(accounts))
    code = c.post("/api/auth/email/start", json={"email": "free@example.com"}).json()["test_code"]
    c.post("/api/auth/email/verify", json={"email": "free@example.com", "code": code})
    return c


def test_free_user_gets_one_day_once_a_week(client, accounts):
    ent = client.get("/api/me").json()["entitlements"]
    assert ent["limits"] and not ent["plus"] and ent["max_days"] == 1 and ent["next_free_at"] is None
    res = client.post("/api/plan", json=DAY)
    assert res.status_code == 200 and len(res.json()["days"]) == 1
    assert res.json()["entitlements"]["next_free_at"] > time.time() + 6 * 86400
    again = client.post("/api/plan", json=DAY)
    assert again.status_code == 402 and again.json()["detail"] == "free_used"
    assert again.json()["next_free_at"] > time.time()


def test_free_user_cannot_use_plus_features(client):
    assert client.post("/api/plan", json=WEEK).json() == {"detail": "plus_required", "feature": "week"}
    replace = {**DAY, "fixed": [{"day": 1, "slot": "lunch", "recipe": "plov"}]}
    assert client.post("/api/plan", json=replace).json()["feature"] == "replace"
    assert client.post("/api/plan", json={**DAY, "seed": 3}).json()["feature"] == "another"


def test_free_plan_ignores_own_prices(client):
    res = client.post("/api/plan", json={**DAY, "price_overrides": {"potato": {"price": 1}}}).json()
    prices = {i["id"]: i["pack_price"] for c in res["shopping_list"] for i in c["items"]}
    assert prices.get("potato", 2) != 1


def test_week_after_the_free_menu_is_available_again(client, accounts, monkeypatch):
    client.post("/api/plan", json=DAY)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 7 * 86400 + 60)
    assert client.post("/api/plan", json=DAY).status_code == 200


def test_plus_unlocks_everything_and_admin_grants_it(client, accounts, capsys):
    assert admin.main(["plus", "free@example.com", "30"]) == 0
    assert "Plus" in capsys.readouterr().out
    ent = client.get("/api/me").json()["entitlements"]
    assert ent["plus"] and ent["unlimited"] and ent["max_days"] == 7
    for _ in range(3):
        assert client.post("/api/plan", json=WEEK).status_code == 200
    assert client.post("/api/plan", json={**WEEK, "seed": 2}).status_code == 200
    assert len(client.get("/api/plans").json()) == 4
    admin.main(["free", "free@example.com"])
    assert client.get("/api/me").json()["entitlements"]["plus"] is False
    assert len(client.get("/api/plans").json()) == 1  # history is a Plus feature


def test_limits_off_means_everything_is_open(client, monkeypatch):
    monkeypatch.setenv("FC_LIMITS", "0")
    assert client.post("/api/plan", json=WEEK).status_code == 200
    assert client.post("/api/plan", json=WEEK).status_code == 200


def test_one_day_menu_budget_is_a_share_of_the_week(client):
    t = client.post("/api/plan", json=DAY).json()["totals"]
    assert t["basis"] == "food" and t["weekly_budget"] == 650000
    assert t["budget"] == round(650000 / 7)
    assert t["food_cost"] < t["cost"]  # whole packs cost more than one day of food


def test_admin_unknown_user(accounts, capsys):
    assert admin.main(["plus", "nobody@example.com"]) == 1
