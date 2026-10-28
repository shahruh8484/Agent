import re
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
    assert "$1012.40" in page and "Лонг 0.0089" in page and "65800.00 / 69100.00" in page
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


def test_news_block_and_account_chart(settings, store):
    folder = _bot(settings)
    status = json.loads((folder / "status.json").read_text())
    status["config"].update(coins=["XRPUSD", "LINKUSD"], max_positions=2, news_mode="shadow",
                            news_block_below=-0.3, news_exit_below=-0.7)
    status["news"] = {"mode": "shadow", "score": -0.45, "summary": "Иск SEC против Ripple.", "effect": "block",
                      "error": "", "scored_at": "2026-10-23T13:00:00+00:00",
                      "headlines": [{"kind": "coin", "title": "SEC sues <b>Ripple</b>", "source": "coindesk.com",
                                     "url": "https://coindesk.com/x", "published": "2026-10-23T12:00:00+00:00",
                                     "key": True}]}
    (folder / "status.json").write_text(json.dumps(status))
    (folder / "equity.csv").write_text("time,balance,unrealized_pnl,mark,account\n"
                                       "2026-10-23T12:00:00+00:00,15,0,1.3,25\n"
                                       "2026-10-23T13:00:00+00:00,15,0.5,1.3,25.5\n")
    page = _client(settings, store).get("/admin/trading").text
    assert "ИИ-фильтр" in page and "наблюдение" in page and "-0.45" in page
    assert "Фильтр бы: пропустить вход" in page and "на сделки не влияет" in page
    assert "SEC sues &lt;b&gt;Ripple&lt;/b&gt;" in page and "★" in page
    assert "+2.00%" in page  # 25 -> 25.5 from the account column, not the free balance
    assert "XRPUSD, LINKUSD" in page and "не больше 2 позиций" in page


def test_price_format():
    from amzagent.web.trading_routes import price
    assert price(81710.2) == "81710.20" and price(1.3195) == "1.3195" and price(1.30) == "1.3"
    assert price(0.080221) == "0.080221" and price(None) == "—" and price("") == "—"


def test_broken_bot_files_show_a_readable_error(settings, store, monkeypatch):
    from amzagent.web import trading_routes

    _bot(settings)
    client = _client(settings, store)
    monkeypatch.setattr(trading_routes, "load_bot", lambda d: (_ for _ in ()).throw(KeyError("availableBalance")))
    res = client.get("/admin/trading")
    assert res.status_code == 500 and "не смогла открыться" in res.text
    assert "KeyError" in res.text and "availableBalance" in res.text and "href='/admin/ig'" in res.text


def test_bot_that_never_reached_the_exchange_still_renders(settings, store):
    # e.g. a demo run that failed to sign in: status has config and an error, but no state yet
    folder = settings_dir(settings) / "evedex_demo_BTCUSDT_DEMO_1h"
    folder.mkdir(parents=True)
    (folder / "status.json").write_text(json.dumps({
        "updated_at": time.time() - 86400, "poll_seconds": 60, "error": "HTTP 404 user_not_found_error",
        "config": {"env": "demo", "instrument": "BTCUSDT:DEMO", "timeframe": "1h"}}))
    (folder / "equity.csv").write_text("time,balance,unrealized_pnl,mark\n")
    res = _client(settings, store).get("/admin/trading")
    assert res.status_code == 200 and "BTCUSDT:DEMO" in res.text and "user_not_found_error" in res.text
    assert "бот не отвечает" in res.text


def test_chart_starts_when_the_account_is_funded():
    rows = [{"time": "a", "balance": "0.00004"}, {"time": "b", "balance": "0.00004"},
            {"time": "c", "balance": "3.65"}, {"time": "d", "balance": "3.72"}]
    chart = equity_chart(rows)
    assert chart["first"] == "c" and round(chart["change_pct"], 2) == 1.92 and chart["min"] == 3.65
    assert equity_chart(rows[:3]) is None  # only one funded point so far


SCHEMA = {
    "max_usd": {"label": "Максимум на позицию, $", "type": "float", "min": 1, "max": 1000, "step": 1},
    "leverage": {"label": "Плечо, x", "type": "int", "min": 1, "max": 5, "step": 1},
    "fast_ema": {"label": "Быстрая EMA", "type": "int", "min": 2, "max": 100, "step": 1},
    "slow_ema": {"label": "Медленная EMA", "type": "int", "min": 5, "max": 400, "step": 1},
    "news_mode": {"label": "Новостной фильтр", "type": "choice",
                  "options": {"off": "выключен", "shadow": "наблюдение", "on": "включён"}},
}
DEFAULTS = {"max_usd": 10.0, "leverage": 1, "fast_ema": 20, "slow_ema": 50, "news_mode": "shadow"}


def _bot_with_schema(settings, env="prod", **config):
    folder = _bot(settings, env=env)
    status = json.loads((folder / "status.json").read_text())
    status["config"].update({
        "settings_schema": SCHEMA, "settings_defaults": DEFAULTS, "settings_overrides": [], "settings_error": "",
        "settings_rules": [["fast_ema", "<", "slow_ema", "EMA: быстрая < медленной"]], **DEFAULTS, **config})
    (folder / "status.json").write_text(json.dumps(status))
    return folder


def test_settings_form_renders_from_the_bots_schema(settings, store):
    _bot_with_schema(settings)
    page = _client(settings, store).get("/admin/trading").text
    assert 'name="max_usd"' in page and 'max="1000"' in page and 'name="news_mode"' in page
    assert "Сохранить настройки" in page and "РЕАЛЬНЫЙ СЧЁТ. Применить" in page and "в .env: 10.0" in page


def test_settings_saved_only_where_they_differ_from_env(settings, store):
    folder = _bot_with_schema(settings)
    client = _client(settings, store)
    page = client.post(f"/admin/trading/{folder.name}/settings", data={
        "max_usd": "25", "leverage": "1", "fast_ema": "20", "slow_ema": "50", "news_mode": "on"}).text
    saved = json.loads((folder / "settings.json").read_text())
    assert {k: v for k, v in saved.items() if k != "updated_at"} == {"max_usd": 25, "news_mode": "on"}
    assert "Настройки сохранены" in page
    # back to .env values -> no overrides file at all
    client.post(f"/admin/trading/{folder.name}/settings", data={**{k: str(v) for k, v in DEFAULTS.items()}})
    assert not (folder / "settings.json").exists()
    assert len((folder / "settings_history.csv").read_text().splitlines()) == 2


def test_bad_settings_are_rejected_with_reasons(settings, store):
    folder = _bot_with_schema(settings)
    client = _client(settings, store)
    page = client.post(f"/admin/trading/{folder.name}/settings",
                       data={"max_usd": "5000", "leverage": "1.5", "fast_ema": "60", "slow_ema": "50",
                             "news_mode": "yolo"}).text
    assert not (folder / "settings.json").exists()
    assert "Настройки не сохранены" in page and "Максимум на позицию" in page and "Плечо" in page
    assert "Новостной фильтр" in page and "EMA: быстрая &lt; медленной" in page


def test_reset_and_bot_side_errors(settings, store):
    folder = _bot_with_schema(settings, settings_error="Плечо, x: должно быть от 1 до 5")
    (folder / "settings.json").write_text('{"leverage": 9}')
    client = _client(settings, store)
    assert "Бот не принял настройки" in client.get("/admin/trading").text
    client.post(f"/admin/trading/{folder.name}/settings/reset")
    assert not (folder / "settings.json").exists()


def test_old_bot_without_schema(settings, store):
    folder = _bot(settings)
    client = _client(settings, store)
    assert "Меняются в <code>.env</code>" in client.get("/admin/trading").text
    page = client.post(f"/admin/trading/{folder.name}/settings", data={"max_usd": "5"}).text
    assert "не умеет менять настройки" in page and not (folder / "settings.json").exists()
    anon = TestClient(create_app(settings, store, start_loop=False))
    assert anon.post(f"/admin/trading/{folder.name}/settings", follow_redirects=False).status_code == 303


def _options_bot(settings, env="testnet", positions=True):
    folder = settings_dir(settings) / f"deribit_{env}_BTC_puts"
    folder.mkdir(parents=True)
    pos = [{"instrument": "BTC-31OCT26-60000-P", "strike": 60000.0, "expiry": "2026-10-31T08:00:00+00:00",
            "amount": 0.1, "sold_price": 0.0043, "mark": 0.0021, "delta": -0.07, "pnl_coin": 0.00022,
            "captured_pct": 51.2}] if positions else []
    (folder / "status.json").write_text(json.dumps({
        "updated_at": time.time(), "poll_seconds": 60, "error": None, "kind": "options", "currency": "BTC",
        "index": 67012.5, "equity": 9.87654, "equity_usd": 661860.0, "available": 9.5, "initial_margin": 0.3,
        "positions": pos, "pending": None, "action": "hold", "reason": "ждём",
        "checked_at": "2026-10-26T10:00:00+00:00",
        "config": {"env": env, "currency": "BTC", "instrument": "BTC путы", "lots": 1,
                   "settings_schema": {"lots": {"label": "Размер, минимальных лотов", "type": "int", "min": 1,
                                                "max": 10, "step": 1}},
                   "settings_defaults": {"lots": 1}, "settings_overrides": [], "settings_error": "",
                   "settings_rules": []}}))
    (folder / "trades.csv").write_text(
        "time,env,action,instrument,amount,price,pnl_coin,pnl_usd,index,reason\n"
        "2026-10-20T10:00:00+00:00,testnet,sell,BTC-24OCT26-58000-P,0.1,0.004,,,66000,продан пут\n"
        "2026-10-24T08:00:00+00:00,testnet,expired,BTC-24OCT26-58000-P,0.1,0.0,0.0004,26.4,66000,экспирация вне денег — премия наша\n")
    (folder / "equity.csv").write_text("time,equity,equity_usd,index\n"
                                       "2026-10-25T10:00:00+00:00,9.8,656600,67000\n"
                                       "2026-10-26T10:00:00+00:00,9.87654,661860,67012.5\n")
    return folder


def test_options_card(settings, store):
    folder = _options_bot(settings)
    _bot(settings)  # an EVEDEX bot next to it keeps its own card
    page = _client(settings, store).get("/admin/trading").text
    assert "BTC путы" in page and "ТЕСТОВАЯ БИРЖА" in page and "Deribit · опционы" in page
    assert "BTC-31OCT26-60000-P" in page and "51%" in page and "+0.00022" in page
    assert "Экспирация" in page and "премия наша" in page and "+0.00040 BTC" in page and "+$26.40" in page
    assert "Размер, минимальных лотов" in page and "Откупить все проданные опционы BTC" in page
    assert "Счёт, BTC" in page and "<polyline" in page  # coin-sized equity still charts
    assert "BTCUSDT:DEMO" in page  # the EVEDEX card still renders
    client = _client(settings, store)
    client.post(f"/admin/trading/{folder.name}/close")
    assert json.loads((folder / "control.json").read_text()) == {"paused": True, "close_requested": True}


def test_options_card_without_positions_disables_close(settings, store):
    _options_bot(settings, env="prod", positions=False)
    page = _client(settings, store).get("/admin/trading").text
    assert "РЕАЛЬНЫЙ СЧЁТ" in page and "Нет открытых позиций" in page
    assert re.search(r'<button class="danger"\s+disabled>Закрыть позиции', page)


def test_tiny_moves_dont_fill_the_chart():
    rows = [{"time": "a", "equity": "100"}, {"time": "b", "equity": "99.9999"}]
    chart = equity_chart(rows, min_funded=1e-9)
    ys = [float(p.split(",")[1]) for p in chart["points"].split()]
    assert abs(ys[0] - ys[1]) < 1  # under a pixel of 160, not top-to-bottom


def test_ibkr_card_in_dollars(settings, store):
    folder = settings_dir(settings) / "ibkr_paper_SPY_puts"
    folder.mkdir(parents=True)
    (folder / "status.json").write_text(json.dumps({
        "updated_at": time.time(), "poll_seconds": 60, "error": None, "kind": "options", "currency": "USD",
        "index": 600.12, "equity": 1003456.0, "equity_usd": 1003456.0, "available": 950000.0,
        "positions": [{"instrument": "SPY 20261030 570 P", "strike": 570.0, "expiry": "2026-10-30T20:00:00+00:00",
                       "amount": 1, "sold_price": 0.85, "mark": 0.4, "delta": -0.08, "pnl_coin": 45.0,
                       "captured_pct": 52.9}],
        "pending": None, "action": "hold", "reason": "держим позицию", "checked_at": "2026-10-28T15:00:00+00:00",
        "config": {"env": "paper", "currency": "USD", "underlying": "SPY", "instrument": "SPY путы",
                   "signal": "Interactive Brokers · опционы · котировки с задержкой 15 мин", "lots": 1,
                   "settings_schema": {}, "settings_defaults": {}, "settings_overrides": [], "settings_error": ""}}))
    (folder / "trades.csv").write_text(
        "time,env,action,instrument,amount,price,pnl_coin,pnl_usd,index,reason\n"
        "2026-10-23T20:00:00+00:00,paper,expired,SPY 20261023 565 P,1,0.0,72.0,72.0,598,экспирация вне денег — премия наша\n"
        "2026-10-24T20:00:00+00:00,paper,assigned,SPY 20261024 580 P,1,580,,,575,исполнение: получено 100 акций SPY по 580\n")
    page = _client(settings, store).get("/admin/trading").text
    assert "SPY путы" in page and "БУМАЖНЫЙ СЧЁТ" in page and "Interactive Brokers · опционы" in page
    assert "$1,003,456" in page and "цена SPY" in page and "+$72.00" in page
    assert "SPY 20261030 570 P" in page and "+45.00 $" in page and "Цена, $/акция" in page
    assert "Исполнение акциями" in page and "P&amp;L, $" in page and page.count("P&amp;L, $") == 1


def test_summary_lists_every_bot_real_money_first(settings, store):
    _bot(settings)                                   # EVEDEX demo, long open
    _options_bot(settings, env="testnet")            # Deribit BTC, position open, +$26.40 realized
    _bot(settings, env="prod", position=False)       # EVEDEX real money, idle
    page = _client(settings, store).get("/admin/trading").text
    summary = page.split("<h2", 2)[1].split("Подробно")[0]
    assert "Сводка" in summary and summary.count("<tr onclick") == 3
    assert summary.index("реальный") < summary.index("тестовый")  # real money on top
    assert "BTC-31OCT26-60000-P · забрано 51% премии" in summary and "+$26.40" in summary
    assert "лонг 0.0089" in summary and "в позиции" in summary and "Deribit" in summary
