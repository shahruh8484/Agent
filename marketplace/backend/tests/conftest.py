import pytest
from fastapi.testclient import TestClient

from servio.app import create_app
from servio.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(database_path=str(tmp_path / "test.db"), _env_file=None)


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


@pytest.fixture
def login(client):
    """Logs a phone in and returns auth headers; optionally sets profile fields."""

    def _login(phone: str, **profile) -> dict:
        code = client.post("/auth/request-code", json={"phone": phone}).json()["debug_code"]
        token = client.post("/auth/verify", json={"phone": phone, "code": code}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        if profile:
            assert client.patch("/me", json=profile, headers=headers).status_code == 200
        return headers

    return _login


@pytest.fixture
def service_id(client):
    return client.get("/categories").json()[0]["children"][0]["id"]
