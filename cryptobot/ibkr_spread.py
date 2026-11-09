"""Weekly put credit spreads through Interactive Brokers (paper account by default).

Like the put seller in ibkr.py, but every sold put is paired with a bought put WIDTH dollars
lower. The bought put caps the loss at (width - credit) x 100 per contract, so the broker holds
about $500 per $5-wide spread instead of the ~$13,000 a naked SPY put needs, and the permission
for spreads is far easier to get. The premium is smaller; the risk is known in advance.

Both legs go in as one combo order, so they fill together or not at all.
Exits: take profit when the spread is worth (100 - TAKE_PROFIT)% of the credit, stop when it
is worth STOP_MULTIPLE x the credit or the sold put's |delta| reaches MAX_DELTA, and on expiry
day after 15:00 New York time when the sold put is near the money (|delta| >= 0.2).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptobot.evedex import BotFiles, read_control, update_control
from cryptobot.ibkr import (REPRICE_SECONDS, expiry_close, expiry_day_late, market_open, round_cents,
                            taken_keys)
from cryptobot.options import put_delta
from cryptobot.settings import effective

log = logging.getLogger(__name__)
LABEL = "putspread"

SPREAD_SCHEMA = {
    "lots": {"label": "Размер, спредов (1 = 100 акций)", "type": "int", "min": 1, "max": 10, "step": 1},
    "width": {"label": "Ширина спреда, $ между страйками", "type": "float", "min": 1, "max": 50, "step": 1},
    "target_delta": {"label": "Целевая дельта проданного пута", "type": "float", "min": 0.05, "max": 0.4,
                     "step": 0.01},
    "min_otm_pct": {"label": "Проданный страйк минимум на % ниже цены", "type": "float", "min": 0, "max": 20,
                    "step": 0.5},
    "min_credit_pct": {"label": "Премия не меньше, % от ширины", "type": "float", "min": 5, "max": 60, "step": 1},
    "min_dte": {"label": "Экспирация не раньше, дней", "type": "int", "min": 1, "max": 30, "step": 1},
    "max_dte": {"label": "Экспирация не позже, дней", "type": "int", "min": 2, "max": 45, "step": 1},
    "take_profit_pct": {"label": "Закрыть, когда забрано % премии", "type": "float", "min": 10, "max": 95,
                        "step": 5},
    "stop_multiple": {"label": "Стоп: спред подорожал в … раз", "type": "float", "min": 1.5, "max": 5, "step": 0.5},
    "max_delta": {"label": "Стоп по дельте проданного пута, |дельта| ≥", "type": "float", "min": 0.2, "max": 0.9,
                  "step": 0.05},
    "max_margin_pct": {"label": "Риск всех спредов не больше, % счёта", "type": "float", "min": 1, "max": 50,
                       "step": 1},
}
SPREAD_RULES = [
    ["min_dte", "<", "max_dte", "Минимальный срок должен быть меньше максимального"],
    ["target_delta", "<", "max_delta", "Целевая дельта должна быть меньше стоп-дельты"],
]
# Spreads sell closer to the money than naked puts: the bought put limits the damage, and far
# out the credit would be pennies. Half the credit is the usual point to take profit.
SPREAD_DEFAULTS = {"lots": 1, "width": 5.0, "target_delta": 0.20, "min_otm_pct": 1.5, "min_credit_pct": 15.0,
                   "min_dte": 3, "max_dte": 10, "take_profit_pct": 50.0, "stop_multiple": 2.0, "max_delta": 0.45,
                   "max_margin_pct": 10.0}


def spread_exit(credit: float, value: float, short_delta: float, s: dict) -> str:
    if value <= credit * (1 - s["take_profit_pct"] / 100):
        return f"тейк: забрано {s['take_profit_pct']:.0f}% премии"
    if value >= credit * s["stop_multiple"]:
        return f"стоп: спред подорожал в {value / credit:.1f} раза"
    if abs(short_delta) >= s["max_delta"]:
        return f"стоп: |дельта| проданного пута {abs(short_delta):.2f} ≥ {s['max_delta']}"
    return ""


def spread_name(short_key: str, long_key: str) -> str:
    sym, expiry, k_short, _ = short_key.split()
    return f"{sym} {expiry} {k_short}/{long_key.split()[2]} P"


class IbSpreadSeller:
    def __init__(self, client, symbol: str, env: str, base: dict, state_dir: str | Path, poll_seconds: int,
                 delayed: bool = True):
        self.c, self.sym, self.env, self.base, self.poll, self.delayed = client, symbol, env, base, poll_seconds, delayed
        self.files = BotFiles(state_dir, env, symbol, "", folder=f"ibkr_{env}_{symbol}_spread")
        self.memory = self.files.dir / "memory.json"
        self.last: dict = {}
        self.error: str | None = None

    def _mem(self) -> dict:
        m = read_control(self.memory)
        m.setdefault("managed", {})
        m.setdefault("pending", None)
        return m

    def _save(self, m: dict) -> None:
        tmp = self.memory.with_suffix(".tmp")
        tmp.write_text(json.dumps(m))
        tmp.replace(self.memory)

    def settings(self):
        overrides = read_control(self.files.settings)
        overrides.pop("updated_at", None)
        cur, errs = effective(self.base, overrides, SPREAD_SCHEMA, SPREAD_RULES)
        return cur, errs, sorted(k for k in overrides if k in SPREAD_SCHEMA and not errs)

    def journal(self, action, name, qty, price, pnl, spot, reason):
        self.files.append(self.files.trades, {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "env": self.env, "action": action,
            "instrument": name, "amount": qty, "price": price, "pnl_coin": pnl, "pnl_usd": pnl, "index": spot,
            "reason": reason})

    def _legs(self, info: dict) -> tuple[dict | None, dict | None]:
        q = {x["key"]: x for x in self.c.quotes(self.sym, info["expiry"], [info["short_strike"], info["long_strike"]],
                                                info["trading_class"])}
        return q.get(info["short"]), q.get(info["long"])

    @staticmethod
    def _value(short: dict | None, long_: dict | None) -> float:
        return max((short or {}).get("mark", 0.0) - (long_ or {}).get("mark", 0.0), 0.0)

    def _close(self, sid: str, info: dict, m: dict, spot: float, why: str) -> int:
        short, long_ = self._legs(info)
        natural = (short or {}).get("ask", 0) - (long_ or {}).get("bid", 0)
        value = self._value(short, long_)
        price = round_cents(min(natural if natural > 0 else value * 1.3 or info["credit"] * 2, info["width"]), up=True)
        res = self.c.buy_spread(info["short"], info["long"], int(info["qty"]), price, LABEL)
        filled = int(res["filled"])
        if filled:
            pnl = round((info["credit"] - res["avg"]) * filled * 100, 2)
            self.journal("buyback", sid, filled, res["avg"], pnl, spot, why)
            log.info("%s spread closed %s x%s at %s (%s), pnl=$%s", self.sym, sid, filled, res["avg"], why, pnl)
        info["qty"] -= filled
        if info["qty"] <= 0:
            del m["managed"][sid]
            return 0
        return info["qty"]

    def step(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        s, serr, over = self.settings()
        m = self._mem()
        spot = self.c.spot(self.sym)
        positions = self.c.positions()
        opts = {p["key"]: p["qty"] for p in positions if p["sec_type"] == "OPT" and p["symbol"] == self.sym and p["qty"]}
        shares = sum(p["qty"] for p in positions if p["sec_type"] == "STK" and p["symbol"] == self.sym)
        control = read_control(self.files.control)
        paused = bool(control.get("paused"))
        action, reason = "hold", "ждём"

        # 1. spreads gone: expired, assigned, or closed by hand
        for sid, info in list(m["managed"].items()):
            have_short, have_long = info["short"] in opts, info["long"] in opts
            if have_short and have_long:
                if opts[info["short"]] > 0 or opts[info["long"]] < 0:  # the combo was booked the other way round
                    update_control(self.files.control, paused=True)
                    paused = True
                    reason = f"у спреда {sid} ноги не с той стороны (куплен верхний пут) — бот на паузе, проверьте счёт"
                continue
            if now >= expiry_close(info["expiry"]):
                if shares:
                    self.journal("assigned", sid, info["qty"], info["short_strike"], None, spot,
                                 f"исполнение: на счёте {shares:g} акций {self.sym}")
                    update_control(self.files.control, paused=True)
                    paused, action = True, "assigned"
                    reason = f"на счёте акции {self.sym} после экспирации — бот на паузе, решите, что с ними делать"
                else:
                    pnl = round(info["credit"] * info["qty"] * 100, 2)
                    self.journal("expired", sid, info["qty"], 0.0, pnl, spot, "экспирация вне денег — премия наша")
                    action, reason = "expired", "спред истёк"
                del m["managed"][sid]
            elif not have_short and not have_long:
                self.journal("gone", sid, info["qty"], 0.0, None, spot, "спред закрыт не ботом (вручную?)")
                del m["managed"][sid]
            else:  # one leg left: never leave a naked put or a stray long behind silently
                update_control(self.files.control, paused=True)
                paused, action = True, "hold"
                reason = (f"у спреда {sid} осталась одна нога ({'проданный' if have_short else 'купленный'} пут) — "
                          "бот на паузе, проверьте счёт в IBKR")

        # 2. pending combo order
        if m["pending"]:
            p = m["pending"]
            st = self.c.order_state(p["order_id"])
            if st["status"] == "Filled" or (st["filled"] and st["status"] in ("Cancelled", "ApiCancelled", "Inactive")):
                credit = st["avg"] or p["credit"]
                m["managed"][p["sid"]] = {**{k: p[k] for k in ("short", "long", "short_strike", "long_strike",
                                                                "expiry", "trading_class", "width")},
                                          "credit": credit, "qty": int(st["filled"]), "opened_at": now.isoformat()}
                self.journal("sell", p["sid"], int(st["filled"]), credit, None, spot,
                             f"продан спред, дельта ≈ {p['delta']:.2f}, риск ${(p['width'] - credit) * 100:.0f}")
                m["pending"] = None
                action, reason = "sell", "ордер исполнен"
            elif st["status"] in ("Cancelled", "ApiCancelled", "Inactive", "Unknown"):
                m["pending"] = None
            elif time.time() - p["placed_at"] > REPRICE_SECONDS or paused or not market_open(now):
                self.c.cancel(p["order_id"])
                m["pending"] = None
                reason = "ордер не исполнился — переставим"

        # 3. panel asked to close everything
        if control.get("close_requested"):
            if m["pending"]:
                self.c.cancel(m["pending"]["order_id"])
                m["pending"] = None
            left = sum(self._close(sid, info, m, spot, "закрыто из панели") for sid, info in list(m["managed"].items()))
            update_control(self.files.control, paused=True, close_requested=left > 0)
            paused, action = True, "buyback"
            reason = "закрыто из панели" if not left else "закрываем — часть не исполнилась, повторим"

        # 4. exits
        views = []
        for sid, info in list(m["managed"].items()):
            short, long_ = self._legs(info)
            value = self._value(short, long_)
            delta = (short or {}).get("delta") or 0.0
            why = spread_exit(info["credit"], value, delta, s) if short and long_ and value else ""
            if not why and expiry_day_late(info["expiry"], now) and abs(delta) >= 0.2:
                why = "перед экспирацией: проданный пут близко к деньгам"
            if why:
                action, reason = "buyback", why
                if not self._close(sid, info, m, spot, why):
                    continue
            views.append({"instrument": sid, "strike": info["short_strike"],
                          "expiry": expiry_close(info["expiry"]).isoformat(), "amount": info["qty"],
                          "sold_price": info["credit"], "mark": value, "delta": delta,
                          "pnl_coin": round((info["credit"] - value) * info["qty"] * 100, 2),
                          "captured_pct": round((1 - value / info["credit"]) * 100, 1) if info["credit"] else 0,
                          "max_loss": round((info["width"] - info["credit"]) * info["qty"] * 100, 2)})

        # 5. entry
        acct = self.c.account()
        if not m["managed"] and not m["pending"] and action not in ("buyback", "expired", "assigned"):
            if paused:
                reason = reason if reason.startswith(("у спреда", "на счёте")) else "пауза — новые продажи отключены"
            elif not market_open(now):
                reason = "биржа закрыта — продажи пн–пт 9:35–15:45 по Нью-Йорку"
            else:
                action, reason = self._enter(s, m, spot, acct, positions, now)
        if m["pending"] and reason == "ждём":
            reason = "ждём, пока купят наш ордер"
        if action == "hold" and reason == "ждём" and views:
            reason = "держим спред: тейк и стоп не достигнуты"
        log.info("%s spread: %s — %s", self.sym, action, reason)

        self._save(m)
        self.files.append(self.files.equity, {"time": now.isoformat(timespec="seconds"), "equity": acct["net_liq"],
                                              "equity_usd": acct["net_liq"], "index": spot})
        self.last = {"kind": "options", "currency": "USD", "index": spot, "equity": acct["net_liq"],
                     "equity_usd": acct["net_liq"], "available": acct["available"],
                     "initial_margin": acct["init_margin"], "positions": views,
                     "pending": m["pending"] and {**m["pending"], "price": m["pending"]["credit"]},
                     "action": action, "reason": reason, "checked_at": now.isoformat(), "settings": (s, serr, over)}

    def _enter(self, s: dict, m: dict, spot: float, acct: dict, positions: list[dict], now: datetime):
        if self.c.open_sells(self.sym, LABEL):
            return "hold", "на бирже уже висит наш ордер (бот перезапускался) — ждём его исполнения или отмены"
        ch = self.c.chain(self.sym)
        expiries = [e for e in ch["expirations"]
                    if s["min_dte"] <= (expiry_close(e) - now).total_seconds() / 86400 <= s["max_dte"]]
        if not expiries:
            return "hold", f"нет экспирации на {s['min_dte']}–{s['max_dte']} дней"
        expiry = expiries[0]
        real = self.c.expiry_strikes(self.sym, expiry, ch["trading_class"])
        limit = spot * (1 - s["min_otm_pct"] / 100)
        shorts = [k for k in real if k <= limit][-12:]
        taken = taken_keys(positions, self.sym, set())
        pairs = []
        for k in shorts:  # the bought put: the nearest real strike at least `width` below
            lower = [x for x in real if x <= k - s["width"] + 1e-9]
            if lower:
                pairs.append((k, lower[-1]))
        if not pairs:
            return "hold", "нет подходящих страйков"
        need = sorted({k for pair in pairs for k in pair})
        q = {x["strike"]: x for x in self.c.quotes(self.sym, expiry, need, ch["trading_class"])}
        if q and not any(x["bid"] > 0 or x["mark"] > 0 for x in q.values()):
            return "hold", ("IBKR не прислал котировки по опционам — похоже, нужна подписка на данные "
                            "US Options (OPRA) для бумажного счёта")
        days = (expiry_close(expiry) - now).total_seconds() / 86400
        best = None
        for k_short, k_long in pairs:
            short, long_ = q.get(k_short), q.get(k_long)
            if not short or not long_ or short["key"] in taken or long_["key"] in taken:
                continue
            d = short["delta"] if short["delta"] is not None else (
                put_delta(spot, k_short, short["iv"] * 100, days) if short["iv"] else None)
            if d is None or abs(d) >= s["max_delta"]:
                continue
            gap = abs(abs(d) - s["target_delta"])
            if best is None or gap < best[0]:
                best = (gap, short, long_, d)
        if not best:
            return "hold", "нет котировок по путам (нет данных, все слишком рискованные или заняты другим ботом)"
        _, short, long_, delta = best
        width = short["strike"] - long_["strike"]
        mid = short["mark"] - long_["mark"]
        natural = short["bid"] - long_["ask"]
        credit = round_cents(max(mid, natural))
        if credit < width * s["min_credit_pct"] / 100:
            return "hold", (f"премия мала: ${credit:.2f} за спред шириной ${width:g} "
                            f"(нужно от {s['min_credit_pct']:.0f}% ширины)")
        qty = int(s["lots"])
        risk = (width - credit) * 100 * qty
        cap = acct["net_liq"] * s["max_margin_pct"] / 100
        if risk > acct["available"] or risk > cap:
            return "hold", f"мало залога: риск ${risk:,.0f}, доступно ${min(acct['available'], cap):,.0f}"
        sid = spread_name(short["key"], long_["key"])
        order_id = self.c.sell_spread(short["key"], long_["key"], qty, credit, LABEL)
        m["pending"] = {"order_id": order_id, "sid": sid, "short": short["key"], "long": long_["key"],
                        "short_strike": short["strike"], "long_strike": long_["strike"], "width": width,
                        "credit": credit, "expiry": expiry, "trading_class": ch["trading_class"], "delta": delta,
                        "placed_at": time.time(), "instrument": sid}
        log.info("%s selling spread %s x%s for $%s (delta %.2f, risk $%.0f)", self.sym, sid, qty, credit, delta, risk)
        return "sell_order", (f"выставлен ордер: спред {sid} за ${credit:.2f} (${credit * 100 * qty:.0f}), "
                              f"максимальный риск ${risk:.0f}")

    def write_status(self) -> None:
        s, serr, over = self.last.pop("settings", None) or (self.base, [], [])
        self.files.write_status(
            config={"env": self.env, "instrument": f"{self.sym} спреды", "currency": "USD", "underlying": self.sym,
                    "signal": "Interactive Brokers · пут-спреды" + (" · котировки с задержкой 15 мин" if self.delayed else ""),
                    **s, "settings_schema": SPREAD_SCHEMA, "settings_rules": SPREAD_RULES,
                    "settings_defaults": self.base, "settings_overrides": over, "settings_error": "; ".join(serr)},
            paused=bool(read_control(self.files.control).get("paused")), error=self.error,
            poll_seconds=self.poll, **{**self.last, "kind": "options"})
        self.last["settings"] = (s, serr, over)
