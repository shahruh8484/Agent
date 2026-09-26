import pytest

from amzagent.push.propeller import PropellerClient, PropellerError, build_campaign_payload


def test_payload_shape():
    p = build_campaign_payload("n", "https://x/p", "T" * 50, "B" * 100,
                               [("https://i1", "https://m1"), ("https://i2", "https://m2")],
                               ["us"], 0.03, 10)
    assert p["direction"] == "nativeads" and p["rate_model"] == "cpc"
    assert p["target_url"] == "https://x/p"
    assert p["daily_amount"] == 10
    assert "evenly_limits_usage" not in p and "total_amount" not in p  # onclick-only / optional
    assert p["status"] == 2  # straight to moderation, not a draft
    assert -12 <= p["timezone"] <= 12
    low = build_campaign_payload("n", "u", "t", "b", [("i", "m")], ["us"], 0.03, 5)
    assert low["daily_amount"] == 10  # API minimum for push CPC
    assert p["targeting"]["country"]["list"] == ["us"]
    assert p["targeting"]["time_table"]["is_excluded"] is False
    assert len(p["targeting"]["time_table"]["list"]) == 168
    assert p["targeting"]["time_table"]["list"][:2] == ["Mon00", "Mon01"]
    assert p["targeting"]["user_activity"] == {"list": [1, 2, 3], "is_excluded": False}
    assert all(c["status"] == 1 for c in p["creatives"])
    assert p["targeting"]["traffic_categories"] == ["propeller"]
    assert "frequency" not in p and "capping" not in p  # unsupported for push CPC
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
        return Resp(200, {"result": {"id": 77}})

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


def test_inline_images_sends_data_uris(tmp_path):
    from PIL import Image

    from amzagent.push.propeller import inline_images

    Image.new("RGB", (492, 328), "red").save(tmp_path / "m.png")
    Image.new("RGB", (192, 192), "blue").save(tmp_path / "i.png")
    files = {"https://s/media/i.png": tmp_path / "i.png", "https://s/media/m.png": tmp_path / "m.png"}
    payload = build_campaign_payload("n", "u", "t", "b", [("https://s/media/i.png",
                                     "https://s/media/m.png")], ["us"], 0.03, 10)
    sent = inline_images(payload, files.get)
    assert sent["creatives"][0]["image"].startswith("data:image/jpeg;base64,")
    assert payload["creatives"][0]["image"] == "https://s/media/m.png"  # stored copy untouched
    with pytest.raises(PropellerError):
        inline_images(payload, lambda url: None)


def test_statistics_reads_every_page(monkeypatch):
    client = PropellerClient("tok")
    pages = []

    def fake_request(method, url, timeout, **kw):
        assert "tz" not in kw["params"]  # only allowed for <= 1 week ranges
        page = kw["params"]["page"]
        pages.append(page)
        return Resp(200, {"items": [{"campaign_id": 7, "zone_id": 100 + page, "impressions": 10,
                                     "clicks": 1, "spent": 0.5}], "total_pages": 3})

    monkeypatch.setattr(client._session, "request", fake_request)
    rows = client.spend(["7"], by_zone=True)
    assert pages == [1, 2, 3]
    assert [r["zone_id"] for r in rows] == ["101", "102", "103"]
    assert rows[0] == {"campaign_id": "7", "zone_id": "101", "impressions": 10, "clicks": 1,
                       "spent": 0.5}


def test_statistics_rate_limit_pauses_further_stats_calls(monkeypatch):
    import amzagent.push.propeller as propeller

    monkeypatch.setattr(propeller, "_stats_blocked_until", None)
    client = PropellerClient("tok")
    calls = []

    def fake_request(method, url, timeout, **kw):
        calls.append(url)
        if "statistics" in url:
            return Resp(429, {"message": "You exceeded the rate limit"})
        return Resp(200, {})

    monkeypatch.setattr(client._session, "request", fake_request)
    with pytest.raises(PropellerError, match="429"):
        client.spend(["1"])
    assert propeller.stats_rate_limited()
    with pytest.raises(PropellerError, match="rate limit"):
        client.spend(["1"])
    assert len(calls) == 1  # the second call never reached the API
    client.stop(["1"])  # campaign control is never held back
    assert len(calls) == 2
    monkeypatch.setattr(propeller, "_stats_blocked_until", None)
