import json
from datetime import datetime, timezone

import pandas as pd
import pytest
from typer.testing import CliRunner

from cryptobot import ibkr
from cryptobot.ibkr import DEFAULTS, IbPutSeller, describe_error, expiry_close, expiry_day_late, market_open
from cryptobot.main import app
from cryptobot.options import put_delta

TUE = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)  # 11:00 New York
SPOT = 600.0


class FakeIb:
    def __init__(self, net_liq=1_000_000.0, margin=8000.0):
        self.marks = {}
        self.pos = {}
        self.shares = 0
        self.orders = {}
        self.sells, self.buys, self.cancels = [], [], []
        self.net_liq, self.margin_ = net_liq, margin
        self.fill_buys = True
        self.stale_open = 0

    def ensure(self):
        pass

    def spot(self, symbol):
        return SPOT

    def chain(self, symbol):
        return {"expirations": ["20261028", "20261030", "20261106"], "strikes": [float(k) for k in range(500, 621, 5)],
                "trading_class": symbol}

    def expiry_strikes(self, symbol, expiry, tc):
        # weeklies list fewer strikes than the chain's union: only multiples of 5 here
        return [k for k in self.chain(symbol)["strikes"] if k % 5 == 0]

    def quotes(self, symbol, expiry, strikes, tc):
        out = []
        for k in strikes:
            key = f"{symbol} {expiry} {k:g} P"
            mark = self.marks.get(key, max(0.06, (k - 520) * 0.05))
            out.append({"key": key, "symbol": symbol, "expiry": expiry, "strike": k, "bid": mark * 0.95,
                        "ask": mark * 1.05, "mark": mark, "delta": put_delta(SPOT, k, 18, 3.2), "iv": 0.18})
        return out

    def account(self):
        return {"net_liq": self.net_liq, "available": self.net_liq * 0.9, "init_margin": 0.0}

    def positions(self):
        rows = [{"sec_type": "OPT", "symbol": k.split()[0], "key": k, "qty": -q, "avg_cost": 0, "right": "P",
                 "strike": float(k.split()[2]), "expiry": k.split()[1]} for k, q in self.pos.items()]
        if self.shares:
            rows.append({"sec_type": "STK", "symbol": "SPY", "qty": self.shares, "avg_cost": 580})
        return rows

    def margin(self, key, qty, price):
        return self.margin_

    def open_sells(self, symbol):
        return self.stale_open

    def sell(self, key, qty, price):
        oid = str(len(self.orders) + 1)
        self.orders[oid] = {"status": "Submitted", "filled": 0.0, "avg": 0.0, "key": key, "qty": qty, "price": price}
        self.sells.append((key, qty, price))
        return oid

    def fill(self, oid):
        o = self.orders[oid]
        o.update(status="Filled", filled=float(o["qty"]), avg=o["price"])
        self.pos[o["key"]] = o["qty"]

    def order_state(self, oid):
        return self.orders[oid]

    def cancel(self, oid):
        self.cancels.append(oid)
        self.orders[oid]["status"] = "Cancelled"

    def buy_back(self, key, qty, price):
        self.buys.append((key, qty, price))
        if not self.fill_buys:
            return {"filled": 0.0, "avg": 0.0}
        self.pos.pop(key, None)
        return {"filled": float(qty), "avg": price}


def _bot(tmp_path, c=None, base=None):
    return IbPutSeller(c or FakeIb(), "SPY", "paper", dict(base or DEFAULTS), tmp_path, 60)


def _trades(bot):
    return pd.read_csv(bot.files.trades).to_dict("records")


def test_calendar_helpers():
    assert expiry_close("20261030") == datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)  # 16:00 EDT
    assert market_open(TUE) and not market_open(datetime(2026, 10, 27, 13, 0, tzinfo=timezone.utc))  # 9:00 NY
    assert not market_open(datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc))  # Saturday
    assert expiry_day_late("20261030", datetime(2026, 10, 30, 19, 30, tzinfo=timezone.utc))
    assert not expiry_day_late("20261030", datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc))


def test_sells_nearest_expiry_below_cushion_near_target_delta(tmp_path):
    c = FakeIb()
    bot = _bot(tmp_path, c)
    bot.step(TUE)
    key, qty, price = c.sells[0]
    sym, expiry, strike, right = key.split()
    assert (sym, expiry, right, qty) == ("SPY", "20261030", "P", 1)  # 20261028 is under 3 days away
    assert float(strike) <= SPOT * 0.97 and price >= 0.05
    deltas = {k: abs(put_delta(SPOT, k, 18, 3.2)) for k in range(500, 585, 5)}
    assert float(strike) == min(deltas, key=lambda k: abs(deltas[k] - 0.15))
    assert "выставлен ордер" in bot.last["reason"]


def test_cycle_fill_take_profit_and_status(tmp_path):
    c = FakeIb()
    bot = _bot(tmp_path, c)
    bot.step(TUE)
    c.fill("1")
    bot.step(TUE)
    key = c.sells[0][0]
    assert bot.last["positions"][0]["instrument"] == key
    c.marks[key] = c.sells[0][2] * 0.2
    bot.step(TUE)
    rows = _trades(bot)
    assert [r["action"] for r in rows] == ["sell", "buyback"] and rows[1]["pnl_usd"] > 0
    bot.write_status()
    st = json.loads(bot.files.status.read_text())
    assert st["kind"] == "options" and st["config"]["currency"] == "USD" and st["config"]["instrument"] == "SPY путы"
    assert "задержкой 15 мин" in st["config"]["signal"] and bot.files.dir.name == "ibkr_paper_SPY_puts"


def test_no_sales_outside_market_hours_and_unfilled_orders_cancelled_at_close(tmp_path):
    c = FakeIb()
    bot = _bot(tmp_path, c)
    night = datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc)
    bot.step(night)
    assert not c.sells and "биржа закрыта" in bot.last["reason"]
    bot.step(TUE)
    bot.step(night)  # still pending at night -> cancelled, not left working
    assert c.cancels == ["1"]


def test_expiry_day_buys_back_risky_put_and_worthless_expiry_keeps_premium(tmp_path):
    c = FakeIb()
    bot = _bot(tmp_path, c)
    bot.step(TUE)
    c.fill("1")
    bot.step(TUE)
    key = c.sells[0][0]
    late = datetime(2026, 10, 30, 19, 30, tzinfo=timezone.utc)  # 15:30 NY on expiry day
    c.marks[key] = c.sells[0][2] * 1.5
    monkey_delta = -0.25
    orig = c.quotes
    c.quotes = lambda *a: [{**q, "delta": monkey_delta} for q in orig(*a)]
    bot.step(late)
    assert c.buys and "перед экспирацией" in bot.last["reason"]

    c2 = FakeIb()
    bot2 = _bot(tmp_path / "b", c2)
    bot2.step(TUE)
    c2.fill("1")
    bot2.step(TUE)
    c2.pos.clear()  # expired worthless
    bot2.step(datetime(2026, 10, 30, 21, 0, tzinfo=timezone.utc))
    row = _trades(bot2)[-1]
    assert row["action"] == "expired" and row["pnl_usd"] == pytest.approx(c2.sells[0][2] * 100)


def test_assignment_pauses_the_bot(tmp_path):
    c = FakeIb()
    bot = _bot(tmp_path, c)
    bot.step(TUE)
    c.fill("1")
    bot.step(TUE)
    c.pos.clear()
    c.shares = 100
    bot.step(datetime(2026, 10, 30, 21, 0, tzinfo=timezone.utc))
    assert _trades(bot)[-1]["action"] == "assigned" and "получены акции SPY" in bot.last["reason"]
    assert json.loads(bot.files.control.read_text())["paused"] is True
    bot.step(TUE)
    assert len(c.sells) == 1  # no new sales while shares sit in the account


def test_margin_check_and_stale_order_after_restart(tmp_path):
    poor = FakeIb(net_liq=10_000, margin=8000)
    bot = _bot(tmp_path, poor)
    bot.step(TUE)
    assert not poor.sells and "мало залога" in bot.last["reason"]
    c = FakeIb()
    c.stale_open = 1
    bot2 = _bot(tmp_path / "x", c)
    bot2.step(TUE)
    assert not c.sells and "уже висит наш ордер" in bot2.last["reason"]


def test_panel_close_and_errors(tmp_path):
    c = FakeIb()
    bot = _bot(tmp_path, c)
    bot.step(TUE)
    c.fill("1")
    bot.step(TUE)
    ibkr.update_control(bot.files.control, close_requested=True)
    bot.step(TUE)
    assert c.buys and json.loads(bot.files.control.read_text()) == {"close_requested": False, "paused": True}
    assert "IB Gateway" in describe_error(ConnectionRefusedError("Connect call failed"))


def test_cli_live_gate(monkeypatch):
    monkeypatch.setattr(ibkr, "run_ibkr", lambda *a, **k: pytest.fail("must not start"))
    assert CliRunner().invoke(app, ["ibkr", "--env", "live"]).exit_code == 1
    assert CliRunner().invoke(app, ["ibkr", "--env", "live", "--real-money"], input="n\n").exit_code != 0
    assert CliRunner().invoke(app, ["ibkr", "--env", "prod"]).exit_code == 1



def test_no_option_data_is_explained(tmp_path):
    c = FakeIb()
    c.quotes = lambda *a: [{"key": "k", "strike": 570.0, "bid": 0.0, "ask": 0.0, "mark": 0.0, "delta": None,
                            "iv": None, "expiry": "20261030", "symbol": "SPY"}]
    bot = _bot(tmp_path, c)
    bot.step(TUE)
    assert not c.sells and "OPRA" in bot.last["reason"]


def test_ib_number_cleanup():
    from cryptobot.ibkr import _num

    assert _num(float("nan")) is None and _num(-1) is None and _num(None) is None and _num("x") is None
    assert _num(1.25) == 1.25 and _num(0) == 0.0
