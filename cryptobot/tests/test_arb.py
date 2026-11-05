import json

import pandas as pd
import pytest
from typer.testing import CliRunner

from cryptobot import arb
from cryptobot.arb import FundingBot, SpreadBot, apr, update_control
from cryptobot.main import app

NOW = 1_800_000_000.0


def q(bid, ask, size=None, ts=NOW):
    return {"bid": bid, "ask": ask, "bid_size": size, "ask_size": size, "ts": ts}


def _trades(bot):
    return pd.read_csv(bot.files.trades).to_dict("records")


def _status(bot):
    return json.loads(bot.files.status.read_text())


def test_spread_trades_only_after_costs_and_skips_traps(tmp_path):
    bot = SpreadBot(tmp_path, 60)
    quotes = {("okx", "BTC"): q(100.00, 100.01), ("bybit", "BTC"): q(100.40, 100.41),   # +0.39% gross
              ("okx", "ETH"): q(10.00, 10.001), ("bybit", "ETH"): q(10.01, 10.02),       # +0.09%: eaten by fees
              ("okx", "XRP"): q(1.00, 1.001), ("mexc", "XRP"): q(1.20, 1.21)}             # +20%: a trap
    bot.step(quotes, {"gate": "timeout"}, NOW)
    rows = _trades(bot)
    assert [(r["instrument"], r["detail"]) for r in rows] == [("BTC", "OKX → Bybit")]
    net = (100.40 / 100.01 - 1) * 100 - 0.25
    assert rows[0]["pnl_usd"] == pytest.approx(100 * net / 100, abs=1e-4)
    st = _status(bot)
    assert st["kind"] == "arb" and st["config"]["env"] == "paper" and st["feed_errors"] == {"Gate": "timeout"}
    xrp = next(o for o in st["opportunities"] if o["what"] == "XRP")
    assert "ловушк" in xrp["note"]
    # the same gap one check later is the same opportunity, not a second trade
    bot.step(quotes, {}, NOW + 60)
    assert len(_trades(bot)) == 1
    fresh = {k: {**v, "ts": NOW + 400} for k, v in quotes.items()}
    bot.step(fresh, {}, NOW + 400)
    assert len(_trades(bot)) == 2 and _status(bot)["realized_usd"] == pytest.approx(2 * rows[0]["pnl_usd"], abs=1e-3)


def test_spread_respects_book_size_staleness_and_pause(tmp_path):
    bot = SpreadBot(tmp_path, 60)
    thin = {("okx", "BTC"): q(100.0, 100.01, size=0.3), ("bybit", "BTC"): q(100.5, 100.6, size=0.3)}
    bot.step(thin, {}, NOW)
    assert _trades(bot)[0]["amount"] == pytest.approx(30.0, abs=0.1)  # only $30 sat at the top of the book
    stale = {("okx", "SOL"): q(10, 10.001, ts=NOW - 600), ("bybit", "SOL"): q(10.2, 10.21)}
    bot2 = SpreadBot(tmp_path / "b", 60)
    bot2.step(stale, {}, NOW)
    assert not bot2.files.trades.exists()
    update_control(bot2.files.control, paused=True)
    bot2.step({("okx", "BTC"): q(100.0, 100.01), ("bybit", "BTC"): q(100.5, 100.6)}, {}, NOW)
    assert not bot2.files.trades.exists() and "пауза" in _status(bot2)["reason"]


def test_spread_reports_total_outage(tmp_path):
    bot = SpreadBot(tmp_path, 60)
    bot.step({}, {"okx": "403 Forbidden", "bybit": "451"}, NOW)
    st = _status(bot)
    assert "ни одна биржа" in st["error"] and "OKX: 403 Forbidden" in st["error"]


def rate(r, next_ts=None, h=8.0):
    return {"rate": r, "interval_h": h, "next_ts": next_ts}


def test_apr():
    assert apr(0.0001, 8) == pytest.approx(10.95)
    assert apr(0.0001, 4) == pytest.approx(21.9)


def test_funding_enters_collects_and_exits(tmp_path):
    bot = FundingBot(tmp_path, 60)
    rates = {("okx", "BTC"): rate(0.0001, NOW + 3600),           # 11% a year: below 20
             ("bybit", "SOL"): rate(0.0003, NOW + 3600),         # 33%
             ("gate", "SOL"): rate(0.0004, NOW + 3600),          # 44% — same coin, better venue
             ("mexc", "DOGE"): rate(0.00025, NOW + 1800)}        # 27%
    bot.step(rates, {}, NOW)
    rows = _trades(bot)
    assert [(r["action"], r["instrument"], r["detail"]) for r in rows] == [("open", "SOL", "Gate"),
                                                                          ("open", "DOGE", "MEXC")]
    assert rows[0]["amount"] == 250.0 and rows[0]["pnl_usd"] == pytest.approx(-250 * 0.0016)
    # an hour later the SOL payment is due: 250 * 0.0004 = $0.10
    bot.step(rates, {}, NOW + 3601)
    pay = [r for r in _trades(bot) if r["action"] == "funding"]
    assert {r["instrument"] for r in pay} == {"SOL", "DOGE"} and any(r["pnl_usd"] == pytest.approx(0.1) for r in pay)
    # the rate fades: out only after three low checks in a row
    low = {**rates, ("gate", "SOL"): rate(0.00001, NOW + 99999)}
    for i in range(2):
        bot.step(low, {}, NOW + 3700 + i)
    assert not any(r["action"] == "close" for r in _trades(bot))
    bot.step(low, {}, NOW + 3800)
    close = [r for r in _trades(bot) if r["action"] == "close"]
    assert close and close[0]["instrument"] == "SOL" and "ставка упала" in close[0]["reason"]
    st = _status(bot)
    # SOL still pays 33% on Bybit, so the freed slot moves there
    assert [p["instrument"] for p in st["positions"]] == ["DOGE · MEXC", "SOL · Bybit"]
    assert st["equity"] == pytest.approx(1000 + st["realized_usd"])


def test_funding_panel_close_pauses_and_bad_settings_fall_back(tmp_path):
    bot = FundingBot(tmp_path, 60)
    rates = {("bybit", "SOL"): rate(0.0005, NOW + 3600)}
    bot.step(rates, {}, NOW)
    update_control(bot.files.control, close_requested=True)
    bot.step(rates, {}, NOW + 10)
    assert _trades(bot)[-1]["action"] == "close" and json.loads(bot.files.control.read_text()) == {
        "close_requested": False, "paused": True}
    bot.step(rates, {}, NOW + 20)
    assert sum(r["action"] == "open" for r in _trades(bot)) == 1
    bot.files.settings.write_text(json.dumps({"enter_apr": 5, "exit_apr": 10}))
    bot.step(rates, {}, NOW + 30)
    assert "Ставка выхода" in _status(bot)["config"]["settings_error"]


def test_cli_arb_is_paper_only(monkeypatch):
    seen = {}
    monkeypatch.setattr(arb, "run_arb", lambda feeds, coins, **k: seen.update(ex=feeds.names, coins=coins, **k))
    assert CliRunner().invoke(app, ["arb", "--exchanges", "okx,bybit", "--coins", "btc,eth"]).exit_code == 0
    assert seen["ex"] == ["okx", "bybit"] and seen["coins"] == ["BTC", "ETH"]


def test_spread_needs_both_live_order_books_to_agree(tmp_path):
    bot = SpreadBot(tmp_path, 60)
    quotes = {("gate", "ETH"): q(2349.0, 2349.8), ("okx", "ETH"): q(2357.2, 2357.3)}  # +0.32% on tickers
    gone = lambda coin, b, s: {"ask": 2357.0, "ask_size": 1.0, "bid": 2357.2, "bid_size": 1.0}
    bot.step(quotes, {}, NOW, confirm=gone)
    assert not bot.files.trades.exists() and _status(bot)["unconfirmed"] == 1
    held = lambda coin, b, s: {"ask": 2349.8, "ask_size": 0.02, "bid": 2357.2, "bid_size": 1.0}
    bot2 = SpreadBot(tmp_path / "b", 60)
    bot2.step(quotes, {}, NOW, confirm=held)
    row = _trades(bot2)[0]
    assert row["amount"] == pytest.approx(47.0, abs=0.1)  # only 0.02 ETH on the ask
    failed = SpreadBot(tmp_path / "c", 60)
    failed.step(quotes, {}, NOW, confirm=lambda *a: None)
    assert not failed.files.trades.exists()
