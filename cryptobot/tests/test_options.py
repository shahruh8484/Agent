import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from typer.testing import CliRunner

from cryptobot import options as opt
from cryptobot.main import app
from cryptobot.options import DEFAULTS, PutSeller, choose_put, exit_reason, put_delta, round_price, settlement_payoff

NOW = datetime.now(timezone.utc)


def _exp(days):
    return int((NOW + timedelta(days=days)).timestamp() * 1000)


def _inst(name, strike, days, kind="put"):
    return {"instrument_name": name, "strike": strike, "option_type": kind, "expiration_timestamp": _exp(days),
            "min_trade_amount": 0.1, "tick_size": 0.0001, "tick_size_steps": [{"above_price": 0.005, "tick_size": 0.0005}],
            "is_active": True}


INSTRUMENTS = [
    _inst("BTC-2D-60000-P", 60000, 2),       # too soon
    _inst("BTC-6D-55000-P", 55000, 6),
    _inst("BTC-6D-58000-P", 58000, 6),
    _inst("BTC-6D-60000-P", 60000, 6),
    _inst("BTC-6D-66000-P", 66000, 6),       # less than 8% below spot
    _inst("BTC-6D-60000-C", 60000, 6, "call"),
    _inst("BTC-13D-55000-P", 55000, 13),     # too far
]
SPOT = 67000.0


class FakeDeribit:
    env = "testnet"

    def __init__(self, margin=0.05, equity=1.0):
        self.marks = {i["instrument_name"]: 0.004 for i in INSTRUMENTS}
        self.deltas = {}
        self.positions_ = {}
        self.orders = {}
        self.sells, self.buys, self.cancels = [], [], []
        self.margin, self.equity_ = margin, equity
        self.fill_buys = True
        self.delivery = SPOT

    def index(self, cur):
        return SPOT

    def instruments(self, cur):
        return INSTRUMENTS

    def book(self, cur):
        return [{"instrument_name": n, "mark_price": m, "mark_iv": 50} for n, m in self.marks.items()]

    def ticker(self, name):
        strike = int(name.split("-")[2])
        delta = self.deltas.get(name, put_delta(SPOT, strike, 50, 6))
        return {"mark_price": self.marks[name], "best_bid_price": self.marks[name] * 0.9,
                "best_ask_price": self.marks[name] * 1.1, "greeks": {"delta": delta}}

    def summary(self, cur):
        return {"equity": self.equity_, "available_funds": self.equity_ * 0.9, "initial_margin": 0.0}

    def positions(self, cur):
        return [{"instrument_name": n, "size": -a} for n, a in self.positions_.items()]

    def margins(self, name, amount, price):
        return {"sell": self.margin, "buy": 0.0}

    def sell(self, name, amount, price):
        oid = f"o{len(self.orders) + 1}"
        self.orders[oid] = {"order_state": "open", "filled_amount": 0, "average_price": 0, "name": name,
                            "amount": amount, "price": price}
        self.sells.append((name, amount, price))
        return {"order_id": oid}

    def fill(self, oid):
        o = self.orders[oid]
        o.update(order_state="filled", filled_amount=o["amount"], average_price=o["price"])
        self.positions_[o["name"]] = o["amount"]

    def order_state(self, oid):
        return self.orders[oid]

    def cancel(self, oid):
        self.cancels.append(oid)
        self.orders[oid]["order_state"] = "cancelled"

    def buy_back(self, name, amount, price):
        self.buys.append((name, amount, price))
        if not self.fill_buys:
            return {"filled_amount": 0}
        self.positions_.pop(name, None)
        return {"filled_amount": amount, "average_price": price}

    def delivery_price(self, cur):
        return self.delivery


def _bot(tmp_path, client=None):
    return PutSeller(client or FakeDeribit(), "BTC", dict(DEFAULTS), tmp_path, 60)


def _trades(bot):
    return pd.read_csv(bot.files.trades).to_dict("records")


def test_put_delta_and_rounding():
    assert -0.5 < put_delta(100, 100, 50, 7) < -0.45
    assert -0.05 < put_delta(100, 85, 50, 7) < 0
    inst = {"tick_size": 0.0001, "tick_size_steps": [{"above_price": 0.005, "tick_size": 0.0005}]}
    assert round_price(0.00433, inst) == 0.0043 and round_price(0.00433, inst, up=True) == 0.0044
    assert round_price(0.0062, inst) == 0.006 and round_price(0.0062, inst, up=True) == 0.0065


def test_choose_put_respects_expiry_window_otm_and_delta():
    put = choose_put(INSTRUMENTS, FakeDeribit().book("BTC"), SPOT, DEFAULTS, NOW)
    assert put["instrument_name"].startswith("BTC-6D-") and put["option_type"] == "put"
    assert put["strike"] <= SPOT * 0.92
    # 60000 is the closest to delta 0.12 among the eligible strikes
    deltas = {s: abs(put_delta(SPOT, s, 50, 6)) for s in (55000, 58000, 60000)}
    assert put["strike"] == min(deltas, key=lambda s: abs(deltas[s] - 0.12))


def test_exit_rules():
    s = DEFAULTS
    assert "тейк" in exit_reason(0.004, 0.001, -0.05, s)
    assert "стоп" in exit_reason(0.004, 0.012, -0.2, s)
    assert "дельта" in exit_reason(0.004, 0.005, -0.45, s)
    assert exit_reason(0.004, 0.003, -0.1, s) == ""
    assert settlement_payoff(60000, 66000) == 0 and round(settlement_payoff(60000, 50000), 4) == 0.2


def test_full_cycle_sell_fill_take_profit(tmp_path):
    c = FakeDeribit()
    bot = _bot(tmp_path, c)
    bot.step()
    assert len(c.sells) == 1 and c.sells[0][1] == 0.1 and bot.last["action"] == "sell_order"
    oid = next(iter(c.orders))
    c.fill(oid)
    bot.step()
    assert bot.last["positions"][0]["instrument"] == c.sells[0][0] and not c.buys
    # premium decays to 20% of what we sold for -> take profit
    c.marks[c.sells[0][0]] = 0.0008
    bot.step()
    assert c.buys and bot.last["action"] == "buyback" and "тейк" in bot.last["reason"]
    rows = _trades(bot)
    assert [r["action"] for r in rows] == ["sell", "buyback"] and rows[1]["pnl_coin"] > 0
    # and it sells the next one
    assert len(c.sells) == 1
    bot.step()
    assert len(c.sells) == 2


def test_stop_on_delta(tmp_path):
    c = FakeDeribit()
    bot = _bot(tmp_path, c)
    bot.step()
    c.fill("o1")
    bot.step()
    c.deltas[c.sells[0][0]] = -0.55
    bot.step()
    assert c.buys and "дельта" in bot.last["reason"]


def test_expiry_and_manual_close_are_recorded(tmp_path):
    c = FakeDeribit()
    bot = _bot(tmp_path, c)
    bot.step()
    c.fill("o1")
    bot.step()
    name = c.sells[0][0]
    # closed by hand before expiry
    c.positions_.pop(name)
    bot.step()
    assert _trades(bot)[-1]["action"] == "gone"
    # now one that expires in the money
    c.fill(next(o for o in c.orders if c.orders[o]["order_state"] == "open"))
    bot.step()
    mem = json.loads(bot.memory.read_text())
    inst = next(iter(mem["managed"]))
    mem["managed"][inst]["expiry"] = (NOW - timedelta(hours=1)).isoformat()
    bot.memory.write_text(json.dumps(mem))
    c.positions_.pop(inst)
    c.delivery = 50000.0
    bot.step()
    row = _trades(bot)[-1]
    assert row["action"] == "expired" and row["price"] > 0 and row["pnl_coin"] < 0


def test_panel_close_retries_until_filled_and_pauses(tmp_path):
    c = FakeDeribit()
    bot = _bot(tmp_path, c)
    bot.step()
    c.fill("o1")
    bot.step()
    opt.update_control(bot.files.control, close_requested=True)
    c.fill_buys = False
    bot.step()
    ctl = json.loads(bot.files.control.read_text())
    assert ctl == {"close_requested": True, "paused": True} and "повторим" in bot.last["reason"]
    c.fill_buys = True
    bot.step()
    assert json.loads(bot.files.control.read_text()) == {"close_requested": False, "paused": True}
    assert not c.positions_ and bot.last["reason"].startswith("пауза") is False
    bot.step()
    assert len(c.sells) == 1 and bot.last["reason"].startswith("пауза")


def test_unfilled_order_is_repriced_and_margin_is_checked(tmp_path, monkeypatch):
    c = FakeDeribit()
    bot = _bot(tmp_path, c)
    bot.step()
    monkeypatch.setattr(opt.time, "time", lambda: 10 ** 12)
    bot.step()
    # cancelled and replaced at a fresh price in the same check
    assert c.cancels == ["o1"] and len(c.sells) == 2 and bot.last["action"] == "sell_order"
    poor = FakeDeribit(margin=0.8, equity=1.0)
    bot2 = _bot(tmp_path / "x", poor)
    bot2.step()
    assert not poor.sells and "мало залога" in bot2.last["reason"]


def test_status_for_the_panel(tmp_path):
    c = FakeDeribit()
    bot = _bot(tmp_path, c)
    (bot.files.settings).write_text(json.dumps({"lots": 2, "target_delta": 0.5}))  # 0.5 > max -> rejected
    bot.step()
    bot.write_status()
    st = json.loads(bot.files.status.read_text())
    assert st["kind"] == "options" and st["config"]["env"] == "testnet" and st["equity_usd"] == SPOT
    assert "settings_schema" in st["config"] and "Целевая дельта" in st["config"]["settings_error"]
    assert c.sells[0][1] == 0.1  # rejected overrides -> 1 lot
    assert bot.files.dir.name == "deribit_testnet_BTC_puts"


def test_cli_prod_gate(monkeypatch):
    monkeypatch.setattr(opt, "run_options", lambda *a, **k: pytest.fail("must not start"))
    assert CliRunner().invoke(app, ["options", "--env", "prod"]).exit_code == 1
    assert CliRunner().invoke(app, ["options", "--env", "prod", "--real-money"], input="n\n").exit_code != 0
    assert CliRunner().invoke(app, ["options", "--env", "mainnet"]).exit_code == 1


def test_window_always_contains_a_weekly_expiry():
    # Mondays 09:37 UTC: next Friday is 3.9 days out, the one after 10.9 — the old 4..9 window missed both
    monday = datetime(2026, 10, 26, 9, 37, tzinfo=timezone.utc)
    fri = lambda d: int(datetime(2026, 10, d, 8, tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
    insts = [{**_inst("BTC-30OCT-60000-P", 60000, 0), "expiration_timestamp": fri(30)},
             {**_inst("BTC-6NOV-60000-P", 60000, 0), "expiration_timestamp": fri(30) + 7 * 86400000}]
    book = [{"instrument_name": i["instrument_name"], "mark_price": 0.004, "mark_iv": 50} for i in insts]
    assert choose_put(insts, book, 70000, {**DEFAULTS, "min_dte": 4, "max_dte": 9}, monday) is None
    assert choose_put(insts, book, 70000, DEFAULTS, monday)["instrument_name"] == "BTC-30OCT-60000-P"


def test_no_put_reason_lists_expiries(tmp_path):
    c = FakeDeribit()
    bot = PutSeller(c, "BTC", {**DEFAULTS, "min_dte": 20, "max_dte": 30}, tmp_path, 60)
    bot.step()
    assert "ближайшие экспирации через" in bot.last["reason"] and "6.0" in bot.last["reason"]


def test_outage_errors_are_short():
    import ccxt

    page = 'deribit GET https://test.deribit.com/x 502 Bad Gateway <!DOCTYPE html><html class="no-js">...</html>'
    msg = opt.describe_error(ccxt.ExchangeNotAvailable(page), 300)
    assert msg.startswith("Deribit временно недоступен (502)") and "<" not in msg and "5 мин" in msg
    other = opt.describe_error(ValueError("bad <b>thing</b>"), 300)
    assert other == "ValueError: bad thing"


def test_asking_price_concedes_slowly_and_never_below_75pct_of_mark():
    from cryptobot.options import asking_price

    assert asking_price(0.0020, 0.0016, 0) == 0.0020
    assert asking_price(0.0020, 0.0016, 2) == pytest.approx(0.0018)
    assert asking_price(0.0020, 0.0016, 4) == 0.0016
    assert asking_price(0.0020, 0.0011, 4) == pytest.approx(0.0015)  # bid too low: 75% of the mark, not the bid
    assert asking_price(0.0020, 0.0011, 2) == pytest.approx(0.00155)  # halfway is above the 75% floor
    assert asking_price(0.0020, 0.0, 9) == 0.0020  # nobody bidding: keep the fair price


def test_unfilled_orders_concede_over_time(tmp_path, monkeypatch):
    c = FakeDeribit()
    bot = _bot(tmp_path, c)
    clock = [1000.0]
    monkeypatch.setattr(opt.time, "time", lambda: clock[0])
    bot.step()
    assert "попытка 1" in bot.last["reason"] or bot.last["action"] == "sell_order"
    prices = [c.sells[-1][2]]
    for _ in range(4):
        clock[0] += 31 * 60
        bot.step()
        prices.append(c.sells[-1][2])
    mark = c.marks[c.sells[0][0]]
    assert prices[0] == round_price(mark, INSTRUMENTS[0]) and prices[-1] < prices[0]
    assert min(prices) >= round_price(mark * 0.9, INSTRUMENTS[0])  # fake bid is 0.9 x mark
    bot.step()
    assert "ждём, пока купят наш ордер (попытка 5)" == bot.last["reason"]



def test_stop_uses_fair_price_when_sold_cheap():
    s = DEFAULTS
    # sold at 0.0011 although fair was 0.0020: 0.0023 is just noise, not 3x
    assert exit_reason(0.0011, 0.0023, -0.08, s, fair=0.0020) == ""
    assert "стоп" in exit_reason(0.0011, 0.0061, -0.2, s, fair=0.0020)
    assert "стоп" in exit_reason(0.0011, 0.0034, -0.2, s)  # without the fair price: old behaviour
