import json
import math

import pandas as pd
import pytest

from cryptobot import ibkr
from cryptobot.ibkr_spread import SPREAD_DEFAULTS, IbSpreadSeller, spread_exit
from cryptobot.tests.test_ibkr import SPOT, TUE, FakeIb


class SpreadIb(FakeIb):
    """Puts priced like real ones: cheap far below the price, rising fast near it."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.spreads, self.closes = [], []
        self.fill_closes = True

    def put_price(self, k):
        return round(20 * math.exp((k - SPOT) / 15), 2)

    def quotes(self, symbol, expiry, strikes, tc):
        out = []
        for k in strikes:
            key = f"{symbol} {expiry} {k:g} P"
            mark = self.marks.get(key, self.put_price(k))
            out.append({"key": key, "symbol": symbol, "expiry": expiry, "strike": k, "bid": round(mark * 0.97, 2),
                        "ask": round(mark * 1.03, 2), "mark": mark, "iv": 0.18,
                        "delta": self.deltas.get(key, ibkr.put_delta(SPOT, k, 18, 3.2))})
        return out

    deltas: dict = {}

    def sell_spread(self, short, long_, qty, credit, label):
        oid = str(len(self.orders) + 1)
        self.orders[oid] = {"status": "Submitted", "filled": 0.0, "avg": 0.0, "legs": (short, long_), "qty": qty,
                            "price": credit}
        self.spreads.append((short, long_, qty, credit, label))
        return oid

    def fill(self, oid):
        o = self.orders[oid]
        o.update(status="Filled", filled=float(o["qty"]), avg=o["price"])
        short, long_ = o["legs"]
        self.pos[short] = o["qty"]      # FakeIb.positions reports these as short (-qty)
        self.longs = {**getattr(self, "longs", {}), long_: o["qty"]}

    def positions(self):
        rows = super().positions()
        for k, q in getattr(self, "longs", {}).items():
            rows.append({"sec_type": "OPT", "symbol": k.split()[0], "key": k, "qty": q, "avg_cost": 0, "right": "P",
                         "strike": float(k.split()[2]), "expiry": k.split()[1]})
        return rows

    def buy_spread(self, short, long_, qty, price, label):
        self.closes.append((short, long_, qty, price))
        if not self.fill_closes:
            return {"filled": 0.0, "avg": 0.0}
        self.pos.pop(short, None)
        self.longs.pop(long_, None)
        return {"filled": float(qty), "avg": price}


def _bot(tmp_path, c, **over):
    return IbSpreadSeller(c, "SPY", "paper", {**SPREAD_DEFAULTS, **over}, tmp_path, 60)


def _trades(bot):
    return pd.read_csv(bot.files.trades).to_dict("records")


def test_exit_rules():
    s = SPREAD_DEFAULTS
    assert "тейк" in spread_exit(1.0, 0.5, -0.1, s)
    assert "стоп" in spread_exit(1.0, 2.0, -0.3, s)
    assert "дельта" in spread_exit(1.0, 1.2, -0.5, s)
    assert spread_exit(1.0, 0.8, -0.2, s) == ""


def test_sells_a_5_dollar_spread_near_target_delta_with_capped_risk(tmp_path):
    c = SpreadIb()
    bot = _bot(tmp_path, c)
    bot.step(TUE)
    short, long_, qty, credit, label = c.spreads[0]
    k_short, k_long = float(short.split()[2]), float(long_.split()[2])
    assert k_short - k_long == 5 and k_short <= SPOT * 0.985 and label == "putspread" and qty == 1
    assert credit == pytest.approx(round(c.put_price(k_short) - c.put_price(k_long), 2), abs=0.01)
    assert credit >= 5 * 0.15
    deltas = {k: abs(ibkr.put_delta(SPOT, k, 18, 3.2)) for k in range(500, 595, 5) if k - 5 >= 500}
    assert k_short == min(deltas, key=lambda k: abs(deltas[k] - 0.20))
    assert "максимальный риск" in bot.last["reason"]


def test_cycle_fill_take_profit_and_status(tmp_path):
    c = SpreadIb()
    bot = _bot(tmp_path, c)
    bot.step(TUE)
    short, long_, _, credit, _ = c.spreads[0]
    c.fill("1")
    bot.step(TUE)
    pos = bot.last["positions"][0]
    assert pos["instrument"].endswith(" P") and "/" in pos["instrument"]
    assert pos["max_loss"] == pytest.approx((5 - credit) * 100)
    c.marks[short] = c.put_price(float(short.split()[2])) * 0.3  # the spread lost most of its value
    c.marks[long_] = c.put_price(float(long_.split()[2])) * 0.3
    bot.step(TUE)
    rows = _trades(bot)
    assert [r["action"] for r in rows] == ["sell", "buyback"] and rows[1]["pnl_usd"] > 0 and "тейк" in rows[1]["reason"]
    bot.write_status()
    st = json.loads(bot.files.status.read_text())
    assert st["config"]["instrument"] == "SPY спреды" and bot.files.dir.name == "ibkr_paper_SPY_spread"


def test_stop_and_expiry(tmp_path):
    c = SpreadIb()
    bot = _bot(tmp_path, c)
    bot.step(TUE)
    short, long_, _, credit, _ = c.spreads[0]
    c.fill("1")
    bot.step(TUE)
    c.marks[short] = c.put_price(float(short.split()[2])) + credit * 2  # spread value ~3x the credit
    bot.step(TUE)
    assert c.closes and "стоп" in _trades(bot)[-1]["reason"] and _trades(bot)[-1]["pnl_usd"] < 0

    c2 = SpreadIb()
    bot2 = _bot(tmp_path / "b", c2)
    bot2.step(TUE)
    c2.fill("1")
    bot2.step(TUE)
    c2.pos.clear()
    c2.longs.clear()  # both legs expired worthless
    from datetime import datetime, timezone
    bot2.step(datetime(2026, 10, 30, 21, 0, tzinfo=timezone.utc))
    row = _trades(bot2)[-1]
    assert row["action"] == "expired" and row["pnl_usd"] == pytest.approx(c2.spreads[0][3] * 100)


def test_one_leg_left_or_wrong_side_pauses(tmp_path):
    c = SpreadIb()
    bot = _bot(tmp_path, c)
    bot.step(TUE)
    c.fill("1")
    bot.step(TUE)
    c.longs.clear()  # the protective put vanished: a naked short is left
    bot.step(TUE)
    assert "осталась одна нога" in bot.last["reason"]
    assert json.loads(bot.files.control.read_text())["paused"] is True

    c2 = SpreadIb()
    bot2 = _bot(tmp_path / "w", c2)
    bot2.step(TUE)
    c2.fill("1")
    bot2.step(TUE)
    short, long_ = c2.spreads[0][:2]
    c2.pos[short] = -1  # FakeIb reports -qty, so this is +1: bought instead of sold
    bot2.step(TUE)
    assert "не с той стороны" in bot2.last["reason"]


def test_skips_contracts_the_put_seller_holds_and_checks_risk(tmp_path):
    c = SpreadIb()
    probe = _bot(tmp_path / "p", SpreadIb())
    probe.step(TUE)
    favourite = probe.c.spreads[0][0]
    c.pos[favourite] = 1  # the naked put seller already sold this one
    bot = _bot(tmp_path, c)
    bot.step(TUE)
    assert c.spreads and favourite not in c.spreads[0][:2]
    poor = SpreadIb(net_liq=2000)
    bot2 = _bot(tmp_path / "x", poor)
    bot2.step(TUE)
    assert not poor.spreads and "мало залога" in bot2.last["reason"]


def test_run_ibkr_runs_spread_bots_next_to_put_sellers(tmp_path, monkeypatch):
    c = SpreadIb()
    calls = []
    monkeypatch.setattr(c, "wait", lambda s: (_ for _ in ()).throw(KeyboardInterrupt()), raising=False)
    monkeypatch.setattr(IbSpreadSeller, "step", lambda self, now=None: calls.append(self.sym))
    monkeypatch.setattr(ibkr.IbPutSeller, "step", lambda self, now=None: calls.append("put " + self.sym))
    with pytest.raises(KeyboardInterrupt):
        ibkr.run_ibkr(c, "paper", ["SPY"], dict(ibkr.DEFAULTS), state_dir=tmp_path, spread_symbols=["QQQ"])
    assert calls == ["put SPY", "QQQ"] and (tmp_path / "ibkr_paper_QQQ_spread" / "status.json").exists()
