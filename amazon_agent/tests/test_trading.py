import json
import time

import bcrypt
from fastapi.testclient import TestClient

from amzagent.web.app import create_app
from amzagent.web.trading_routes import equity_chart


def _client(settings, store):
    settings.admin_password_hash = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    client = TestClient(create_app(settings, store, start_loop=False))
    client.post("/login", data={"username": "admin", "password": "pw"})
    return client


def _bot(settings, env="demo", position=True, updated_at=None):
    folder = settings_dir(settings) / f"evedex_{env}_BTCUSDT_DEMO_1h"
    folder.mkdir(parents=True)
    pos = {"side": "BUY", "quantity": 0.0089, "avgPrice": 66910.5, "unRealizedPnL": 3.0} if position else None
    (folder / "status.json").write_text(json.dumps({
        "updated_at": updated_at or time.time(), "poll_seconds": 60, "error": None,
        "config": {"env": env, "instrument": "BTCUSDT:DEMO", "timeframe": "1h", "signal": "binance BTC/USDT",
                   "leverage": 1, "max_usd": 100, "risk_per_trade": 0.01, "fast_ema": 20, "slow_ema": 50,
                   "stop_atr": 2, "take_profit_atr": 4},
        "candle": "2026-10-23T13:00:00+00:00", "ema_fast": 67100, "ema_slow": 66800, "rsi": 58.3,
        "action": "hold", "reason": "in position",
        "state": {"markPrice": 67248.1, "availableBalance": 1012.4, "position": pos,
                  "tpsl": [{"type": "stop-loss", "price": "65800"}, {"type": "take-profit", "price": "69100"}]},
    }))
    (folder / "trades.csv").write_text("time,env,action,reason,cash,stop,take_profit,mark\n"
                                       "2026-10-23T05:00:00+00:00,demo,open,EMA cross up,580,65800,69100,66900\n")
    (folder / "equity.csv").write_text("time,balance,unrealized_pnl,mark\n"
                                       "2026-10-23T12:00:00+00:00,1000,0,66000\n"
                                       "2026-10-23T13:00:00+00:00,1012.4,3,67248\n")
    return folder


def settings_dir(settings):
    from pathlib import Path
    return Path(settings.data_dir) / "trading"


def test_tab_needs_login_and_is_linked(settings, store):
    anon = TestClient(create_app(settings, store, start_loop=False))
    assert anon.get("/admin/trading", follow_redirects=False).status_code == 303
    assert anon.post("/admin/trading/x/close", follow_redirects=False).status_code == 303
    client = _client(settings, store)
    assert 'href="/admin/trading"' in client.get("/admin").text


def test_empty_state_explains_setup(settings, store):
    page = _client(settings, store).get("/admin/trading").text
    assert "Бот ещё не подключён" in page and "COMPOSE_PROFILES=trading" in page


def test_shows_bot_state_history_and_chart(settings, store):
    _bot(settings)
    page = _client(settings, store).get("/admin/trading").text
    assert "BTCUSDT:DEMO" in page and "ДЕМО" in page and "РЕАЛЬНЫЙ СЧЁТ" not in page
    assert "$1012.40" in page and "Лонг 0.0089" in page and "65800 / 69100" in page
    assert "EMA пересекла вверх" in page and "в позиции" in page
    assert "<polyline" in page and "бот не отвечает" not in page


def test_real_account_and_dead_bot_are_flagged(settings, store):
    _bot(settings, env="prod", updated_at=time.time() - 3600)
    page = _client(settings, store).get("/admin/trading").text
    assert "РЕАЛЬНЫЙ СЧЁТ" in page and "бот не отвечает" in page


def test_buttons_write_control_file_for_the_bot(settings, store):
    folder = _bot(settings)
    client = _client(settings, store)
    control = folder / "control.json"
    client.post(f"/admin/trading/{folder.name}/pause")
    assert json.loads(control.read_text()) == {"paused": True}
    client.post(f"/admin/trading/{folder.name}/resume")
    assert json.loads(control.read_text()) == {"paused": False}
    page = client.post(f"/admin/trading/{folder.name}/close").text
    assert json.loads(control.read_text()) == {"paused": True, "close_requested": True}
    assert "закроет позицию" in page and "закрываем позицию" in page


def test_unknown_bot_is_rejected(settings, store):
    client = _client(settings, store)
    assert client.post("/admin/trading/nope/pause").status_code == 404
    assert client.post("/admin/trading/..%2F..%2Fdata/pause").status_code == 404


def test_equity_chart_needs_two_points():
    assert equity_chart([{"balance": "1000"}]) is None
    chart = equity_chart([{"balance": "1000", "time": "a"}, {"balance": "1100", "unrealized_pnl": "", "time": "b"}])
    assert round(chart["change_pct"], 2) == 10.0 and chart["points"].count(",") == 2
