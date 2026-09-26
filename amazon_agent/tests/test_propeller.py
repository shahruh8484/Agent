import pytest

from amzagent.push.propeller import PropellerClient, PropellerError, build_campaign_payload


def test_payload_shape():
    p = build_campaign_payload("n", "https://x/p", "T" * 50, "B" * 100,
                               [("https://i1", "https://m1"), ("https://i2", "https://m2")],
                               ["us"], 0.03, 10)
    assert p["direction"] == "nativeads" and p["rate_model"] == "cpc"
    assert p["target_url"] == "https://x/p"
    assert p["daily_amount"] == 10
    assert p["evenly_limits_usage"] == 1 and "total_amount" not in p
    assert p["status"] in (1, 2) and -12 <= p["timezone"] <= 12
    assert p["targeting"]["country"]["list"] == ["us"]
    assert len(p["creatives"][0]["title"]) == 30
    assert len(p["creatives"][0]["description"]) == 60
    assert [c["image"] for c in p["creatives"]] == ["https://m1", "https://m2"]


class Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data
        self.content = b"x"
        self.text = str(data)

    def json(self):
        return self._data


def test_client_sends_bearer_and_parses_id(monkeypatch):
    client = PropellerClient("tok")
    calls = []

    def fake_request(method, url, timeout, **kw):
        calls.append((method, url, kw))
        return Resp(200, {"id": 77})

    monkeypatch.setattr(client._session, "request", fake_request)
    assert client.create_campaign({"name": "x"}) == "77"
    assert client._session.headers["Authorization"] == "Bearer tok"
    assert calls[0][0] == "POST" and calls[0][1].endswith("/adv/campaigns")


def test_client_raises_on_http_error(monkeypatch):
    client = PropellerClient("tok")
    monkeypatch.setattr(client._session, "request", lambda *a, **k: Resp(403, {"error": "no"}))
    with pytest.raises(PropellerError, match="403"):
        client.stop(["1"])


def test_requires_token():
    with pytest.raises(PropellerError):
        PropellerClient("")
