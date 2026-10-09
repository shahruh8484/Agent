import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from servio.app import create_app
from servio.config import Settings

PAYME_KEY = "payme-secret"
CLICK_SECRET = "click-secret"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_path=str(tmp_path / "test.db"), _env_file=None, payment_mode="live",
        payme_merchant_id="merchant123", payme_key=PAYME_KEY,
        click_service_id="111", click_merchant_id="222", click_secret_key=CLICK_SECRET,
    )


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


@pytest.fixture
def specialist(client, login):
    h = login("+998900000002", role="specialist")
    trial_end = client.get("/me", headers=h).json()["subscription_until"]
    return h, trial_end


def checkout(client, h, provider, plan="month"):
    r = client.post("/subscription/checkout", headers=h, json={"plan_id": plan, "provider": provider})
    assert r.status_code == 200, r.text
    return r.json()


def payme(client, method, params, key=PAYME_KEY):
    auth = "Basic " + base64.b64encode(f"Paycom:{key}".encode()).decode()
    return client.post("/payments/payme", headers={"Authorization": auth},
                       json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).json()


def test_plans_list_live_providers(client):
    data = client.get("/plans").json()
    assert data["currency"] == "UZS"
    assert data["providers"] == ["payme", "click"]
    assert client.post("/subscription/checkout", json={"plan_id": "month", "provider": "dev"}).status_code == 401


def test_dev_provider_rejected_in_live_mode(client, specialist):
    h, _ = specialist
    r = client.post("/subscription/checkout", headers=h, json={"plan_id": "month", "provider": "dev"})
    assert r.status_code == 400


def test_payme_checkout_url(client, specialist):
    h, _ = specialist
    out = checkout(client, h, "payme")
    assert out["status"] == "pending"
    encoded = out["confirmation_url"].removeprefix("https://test.paycom.uz/")
    params = base64.b64decode(encoded).decode()
    assert f"m=merchant123;ac.order_id={out['payment_id']};a=9900000;" in params


def test_payme_full_flow_extends_subscription(client, specialist):
    h, trial_end = specialist
    pid = checkout(client, h, "payme")["payment_id"]
    account = {"order_id": str(pid)}

    assert payme(client, "CheckPerformTransaction", {"amount": 9900000, "account": account})["result"] == {"allow": True}
    assert payme(client, "CheckPerformTransaction", {"amount": 100, "account": account})["error"]["code"] == -31001
    assert payme(client, "CheckPerformTransaction", {"amount": 9900000, "account": {"order_id": "999"}})["error"]["code"] == -31050

    created = payme(client, "CreateTransaction", {"id": "tx1", "time": 1, "amount": 9900000, "account": account})["result"]
    assert created["state"] == 1
    # same id is idempotent, another transaction for the same order is refused
    assert payme(client, "CreateTransaction", {"id": "tx1", "time": 1, "amount": 9900000, "account": account})["result"] == created
    assert payme(client, "CreateTransaction", {"id": "tx2", "time": 2, "amount": 9900000, "account": account})["error"]["code"] == -31050

    performed = payme(client, "PerformTransaction", {"id": "tx1"})["result"]
    assert performed["state"] == 2
    assert payme(client, "PerformTransaction", {"id": "tx1"})["result"] == performed
    assert payme(client, "CancelTransaction", {"id": "tx1", "reason": 5})["error"]["code"] == -31007
    assert payme(client, "CheckTransaction", {"id": "tx1"})["result"]["state"] == 2
    statement = payme(client, "GetStatement", {"from": 0, "to": 10})["result"]["transactions"]
    assert statement[0]["id"] == "tx1" and statement[0]["account"] == account

    status = client.get(f"/payments/{pid}", headers=h).json()
    assert status["status"] == "succeeded"
    assert status["subscription_until"] > trial_end


def test_payme_cancel_before_perform(client, specialist):
    h, _ = specialist
    pid = checkout(client, h, "payme")["payment_id"]
    payme(client, "CreateTransaction", {"id": "tx1", "time": 1, "amount": 9900000, "account": {"order_id": str(pid)}})
    assert payme(client, "CancelTransaction", {"id": "tx1", "reason": 3})["result"]["state"] == -1
    assert payme(client, "PerformTransaction", {"id": "tx1"})["error"]["code"] == -31008
    assert client.get(f"/payments/{pid}", headers=h).json()["status"] == "canceled"


def test_payme_rejects_bad_auth_and_unknown_method(client):
    assert payme(client, "CheckTransaction", {"id": "x"}, key="wrong")["error"]["code"] == -32504
    assert payme(client, "Nope", {})["error"]["code"] == -32601
    assert payme(client, "CheckTransaction", {"id": "missing"})["error"]["code"] == -31003


def click_form(action, pid, amount="99000", prepare_id=None, error="0", trans="555", secret=CLICK_SECRET):
    form = {"click_trans_id": trans, "service_id": "111", "click_paydoc_id": "1",
            "merchant_trans_id": str(pid), "amount": amount, "action": str(action),
            "error": error, "error_note": "", "sign_time": "2026-10-09 12:00:00"}
    if prepare_id is not None:
        form["merchant_prepare_id"] = str(prepare_id)
    src = trans + "111" + secret + str(pid) + (str(prepare_id) if prepare_id is not None else "") + amount + str(action) + form["sign_time"]
    form["sign_string"] = hashlib.md5(src.encode()).hexdigest()
    return form


def test_click_prepare_and_complete(client, specialist):
    h, trial_end = specialist
    out = checkout(client, h, "click")
    assert "service_id=111" in out["confirmation_url"] and f"transaction_param={out['payment_id']}" in out["confirmation_url"]
    pid = out["payment_id"]

    assert client.post("/payments/click/prepare", data=click_form(0, pid, secret="bad")).json()["error"] == -1
    assert client.post("/payments/click/prepare", data=click_form(0, pid, amount="1000")).json()["error"] == -2
    assert client.post("/payments/click/prepare", data=click_form(0, 999)).json()["error"] == -5

    prep = client.post("/payments/click/prepare", data=click_form(0, pid)).json()
    assert prep["error"] == 0 and prep["merchant_prepare_id"] == pid
    done = client.post("/payments/click/complete", data=click_form(1, pid, prepare_id=pid)).json()
    assert done["error"] == 0 and done["merchant_confirm_id"] == pid
    again = client.post("/payments/click/complete", data=click_form(1, pid, prepare_id=pid)).json()
    assert again["error"] == -4
    assert client.get(f"/payments/{pid}", headers=h).json()["subscription_until"] > trial_end


def test_click_complete_with_click_side_error_cancels(client, specialist):
    h, _ = specialist
    pid = checkout(client, h, "click")["payment_id"]
    client.post("/payments/click/prepare", data=click_form(0, pid))
    out = client.post("/payments/click/complete", data=click_form(1, pid, prepare_id=pid, error="-5017")).json()
    assert out["error"] == -9
    assert client.get(f"/payments/{pid}", headers=h).json()["status"] == "canceled"
