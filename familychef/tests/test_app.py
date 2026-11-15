from fastapi.testclient import TestClient

from familychef.app import app

client = TestClient(app)


def test_index_and_health():
    assert client.get("/").status_code == 200
    assert client.get("/healthz").json()["ok"]


def test_regions_and_ingredients():
    regions = client.get("/api/regions").json()
    assert {r["code"] for r in regions} >= {"RU", "US"}
    data = client.get("/api/ingredients", params={"region": "UZ"}).json()
    assert data["currency"] == "UZS"
    pork = next(i for i in data["items"] if i["id"] == "pork")
    assert pork["available"] is False


def test_plan_endpoint():
    res = client.post("/api/plan", json={"region": "KZ", "budget": 30000, "adults": 2, "children": 1})
    assert res.status_code == 200
    body = res.json()
    assert body["region"]["currency"] == "KZT"
    assert body["shopping_list"]


def test_plan_validation_errors():
    assert client.post("/api/plan", json={"region": "RU", "budget": -1}).status_code == 422
    assert client.post("/api/plan", json={"region": "XX", "budget": 100}).status_code == 422
    assert client.post("/api/plan", json={"region": "RU", "budget": 100,
                                          "pantry": [{"ingredient": "caviar", "quantity": 1}]}).status_code == 422
