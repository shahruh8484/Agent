import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from familychef import accounts as acc_mod
from familychef import app as app_mod
from familychef.accounts import Accounts, AuthError, verify_google_token

CLIENT_ID = "test-client.apps.googleusercontent.com"


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    monkeypatch.delenv("FC_SECRET", raising=False)
    return Accounts(tmp_path)


@pytest.fixture
def client(accounts, monkeypatch):
    monkeypatch.setenv("FC_EMAIL_PROVIDER", "dev")
    monkeypatch.setenv("FC_GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.delenv("FC_REQUIRE_LOGIN", raising=False)
    return TestClient(app_mod.create_app(accounts))


def email_login(client, email="anna@example.com", name="Анна"):
    code = client.post("/api/auth/email/start", json={"email": email}).json()["test_code"]
    res = client.post("/api/auth/email/verify", json={"email": email, "code": code, "name": name})
    assert res.status_code == 200, res.text
    return res.json()["user"]


def test_auth_config(client, monkeypatch):
    cfg = client.get("/api/auth/config").json()
    assert cfg == {"google_client_id": CLIENT_ID, "email": True, "email_test_mode": True,
                   "require_login": False}
    monkeypatch.setenv("FC_EMAIL_PROVIDER", "")
    monkeypatch.setenv("FC_GOOGLE_CLIENT_ID", "")
    cfg = client.get("/api/auth/config").json()
    assert cfg["email"] is False and cfg["google_client_id"] is None


def test_email_login_session_profile_and_logout(client):
    assert client.get("/api/me").status_code == 401
    user = email_login(client)
    assert user["name"] == "Анна" and user["email"] == "anna@example.com"
    me = client.get("/api/me").json()
    assert me["user"]["id"] == user["id"] and me["settings"] == {}
    res = client.put("/api/me", json={"name": "Anna K", "settings": {"region": "UZ", "adults": 2}})
    assert res.json()["user"]["name"] == "Anna K"
    assert client.get("/api/me").json()["settings"] == {"region": "UZ", "adults": 2}
    client.post("/api/auth/logout")
    assert client.get("/api/me").status_code == 401


def test_wrong_and_expired_codes(client, accounts):
    client.post("/api/auth/email/start", json={"email": "b@example.com"})
    res = client.post("/api/auth/email/verify", json={"email": "b@example.com", "code": "000000"})
    if res.status_code == 200:  # astronomically unlikely: the random code was 000000
        return
    assert res.status_code == 400 and res.json()["detail"] == "code_wrong"
    for _ in range(5):
        client.post("/api/auth/email/verify", json={"email": "b@example.com", "code": "1"})
    res = client.post("/api/auth/email/verify", json={"email": "b@example.com", "code": "1"})
    assert res.json()["detail"] == "too_many"
    res = client.post("/api/auth/email/verify", json={"email": "nobody@example.com", "code": "123456"})
    assert res.json()["detail"] == "code_expired"


def test_code_cooldown_and_bad_email(client):
    assert client.post("/api/auth/email/start", json={"email": "c@example.com"}).status_code == 200
    res = client.post("/api/auth/email/start", json={"email": "c@example.com"})
    assert res.status_code == 429 and res.json()["detail"] == "wait"
    res = client.post("/api/auth/email/start", json={"email": "not-an-email"})
    assert res.status_code == 400 and res.json()["detail"] == "bad_email"


def test_codes_are_hashed_at_rest(accounts):
    code = accounts.start_email_code("d@example.com", "1.2.3.4")
    with accounts._db() as db:
        stored = db.execute("SELECT code_hash FROM email_codes").fetchone()[0]
    assert code not in stored and len(stored) == 64


def test_plans_saved_listed_opened_deleted(client):
    email_login(client)
    body = {"region": "KZ", "budget": 30000, "adults": 2, "children": 1}
    plan = client.post("/api/plan", json=body).json()
    assert "plan_id" in plan
    plans = client.get("/api/plans").json()
    assert len(plans) == 1 and plans[0]["currency"] == "KZT"
    opened = client.get(f"/api/plans/{plan['plan_id']}").json()
    assert opened["result"]["totals"] == plan["totals"] and opened["request"]["region"] == "KZ"
    client.delete(f"/api/plans/{plan['plan_id']}")
    assert client.get("/api/plans").json() == []


def test_plans_are_private(client, accounts):
    email_login(client, "owner@example.com")
    plan_id = client.post("/api/plan", json={"region": "RU", "budget": 6000, "adults": 2}).json()["plan_id"]
    client.post("/api/auth/logout")
    email_login(client, "other@example.com")
    assert client.get(f"/api/plans/{plan_id}").status_code == 404
    client.delete(f"/api/plans/{plan_id}")  # must not delete someone else's plan
    client.post("/api/auth/logout")
    email_login(client, "owner@example.com")
    assert len(client.get("/api/plans").json()) == 1


def test_delete_account_removes_everything(client, accounts):
    user = email_login(client)
    client.post("/api/plan", json={"region": "RU", "budget": 6000, "adults": 2})
    client.put("/api/me", json={"settings": {"x": 1}})
    assert client.delete("/api/me").json() == {"ok": True}
    assert client.get("/api/me").status_code == 401
    assert accounts.get_user(user["id"]) is None
    with accounts._db() as db:
        assert db.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_require_login(client, monkeypatch):
    monkeypatch.setenv("FC_REQUIRE_LOGIN", "1")
    body = {"region": "RU", "budget": 6000, "adults": 2}
    res = client.post("/api/plan", json=body)
    assert res.status_code == 401 and res.json()["detail"] == "login_required"
    email_login(client)
    assert client.post("/api/plan", json=body).status_code == 200


def test_anonymous_plans_still_work_by_default(client):
    res = client.post("/api/plan", json={"region": "RU", "budget": 6000, "adults": 2})
    assert res.status_code == 200 and "plan_id" not in res.json()


# ---------------------------------------------------------------- Google
@pytest.fixture
def google_keys(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJwks:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": key.public_key()})()

    monkeypatch.setattr(acc_mod, "_jwks_client", FakeJwks())
    return key


def google_token(key, **over):
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "g-123",
              "email": "Anna@Example.com", "email_verified": True, "name": "Anna Google",
              "picture": "https://example.com/a.png", "iat": int(time.time()), "exp": int(time.time()) + 600}
    claims.update(over)
    return jwt.encode(claims, key, algorithm="RS256")


def test_google_token_verification(google_keys):
    claims = verify_google_token(google_token(google_keys), CLIENT_ID)
    assert claims["sub"] == "g-123"
    for bad in ({"aud": "someone-else"}, {"iss": "https://evil.example"}, {"exp": int(time.time()) - 3600}):
        with pytest.raises(AuthError):
            verify_google_token(google_token(google_keys, **bad), CLIENT_ID)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthError):
        verify_google_token(google_token(other), CLIENT_ID)


def test_google_login_links_to_existing_email_account(client, google_keys):
    user = email_login(client, "anna@example.com", "Анна")
    client.post("/api/auth/logout")
    res = client.post("/api/auth/google", json={"credential": google_token(google_keys)})
    assert res.status_code == 200
    g_user = res.json()["user"]
    assert g_user["id"] == user["id"] and g_user["google"] is True
    assert g_user["name"] == "Анна"  # keeps the name the person chose
    assert client.get("/api/me").status_code == 200


def test_google_login_rejects_bad_token(client, google_keys):
    res = client.post("/api/auth/google", json={"credential": google_token(google_keys, aud="x") })
    assert res.status_code == 401 and res.json()["detail"] == "google_invalid"
