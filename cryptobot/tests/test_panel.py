import json
import time

import pytest
from fastapi.testclient import TestClient

from cryptobot.evedex import BotFiles, EvedexError
from cryptobot.panel import create_app


class FakeClient:
    closed: list = []

    def __init__(self, env):
        self.env = env

    def close(self, instrument):
        FakeClient.closed.append((self.env, instrument))
        return {"closed": True}


@pytest.fixture
def bot(tmp_path):
    files = BotFiles(tmp_path, "demo", "BTCUSDT:DEMO", "1h")
    files.write_status(
        config={"env": "demo", "instrument": "BTCUSDT:DEMO", "timeframe": "1h"},
        paused=False, error=None, poll_seconds=60, candle="2026-10-23T13:00:00+00:00",
        state={"markPrice": 67000, "availableBalance": 1000, "position": None, "tpsl": []},
    )
    files.append(files.equity, {"time": "2026-10-23T13:00:00+00:00", "balance": 1000, "unrealized_pnl": 0, "mark": 67000})
    files.append(files.trades, {"time": "2026-10-23T13:00:00+00:00", "action": "open", "reason": "EMA cross up"})
    FakeClient.closed = []
    return tmp_path, files


def test_lists_bots_with_history(bot):
    state_dir, files = bot
    client = TestClient(create_app(state_dir, password=""))
    assert "Cryptobot" in client.get("/").text
    [b] = client.get("/api/bots").json()
    assert b["name"] == files.dir.name
    assert b["status"]["alive"] is True
    assert b["equity"][0]["balance"] == 1000 and b["trades"][0]["action"] == "open"


def test_stale_bot_is_not_alive(bot):
    state_dir, files = bot
    status = json.loads(files.status.read_text())
    status["updated_at"] = time.time() - 3600
    files.status.write_text(json.dumps(status))
    [b] = TestClient(create_app(state_dir, password="")).get("/api/bots").json()
    assert b["status"]["alive"] is False


def test_pause_resume_writes_control_file(bot):
    state_dir, files = bot
    client = TestClient(create_app(state_dir, password=""))
    client.post(f"/api/bots/{files.dir.name}/pause")
    assert files.paused() is True
    client.post(f"/api/bots/{files.dir.name}/resume")
    assert files.paused() is False


def test_close_pauses_and_closes_on_right_env(bot):
    state_dir, files = bot
    client = TestClient(create_app(state_dir, password="", client_factory=FakeClient))
    assert client.post(f"/api/bots/{files.dir.name}/close").json()["closed"] is True
    assert FakeClient.closed == [("demo", "BTCUSDT:DEMO")]
    assert files.paused() is True


def test_close_reports_exchange_error(bot):
    state_dir, files = bot

    class Failing(FakeClient):
        def close(self, instrument):
            raise EvedexError("no session")

    client = TestClient(create_app(state_dir, password="", client_factory=Failing))
    res = client.post(f"/api/bots/{files.dir.name}/close")
    assert res.status_code == 502 and "no session" in res.json()["detail"]


def test_unknown_bot_and_traversal_rejected(bot):
    state_dir, _ = bot
    client = TestClient(create_app(state_dir, password=""))
    assert client.post("/api/bots/nope/pause").status_code == 404
    assert client.post("/api/bots/..%2F..%2Fetc/pause").status_code == 404


def test_password_required_when_set(bot):
    state_dir, _ = bot
    client = TestClient(create_app(state_dir, password="s3cret"))
    assert client.get("/api/bots").status_code == 401
    assert client.get("/api/bots", auth=("any", "wrong")).status_code == 401
    assert client.get("/api/bots", auth=("any", "s3cret")).status_code == 200
