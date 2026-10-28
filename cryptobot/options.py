"""Weekly put seller for Deribit (testnet by default).

Per currency (BTC, ETH), every check:
  * with no open short put: pick the nearest expiry MIN..MAX days out and the put whose
    delta is closest to TARGET (and at least MIN_OTM % below the index), check the margin,
    and sell the minimum lot at the mark price; an unfilled order is re-priced;
  * with an open short put: buy it back once TAKE_PROFIT % of the premium is captured,
    or when it costs STOP_MULTIPLE x the premium or |delta| reaches MAX_DELTA (stop);
  * otherwise hold to expiry; Deribit settles it and the bot records the result.

Deribit's BTC/ETH options are inverse: prices, premiums and P&L are in the coin.
Status, trades and equity go to state_dir/deribit_<env>_<CUR>_puts/ like the EVEDEX bot,
so the panel's Трейдинг tab shows them and can pause the bot or close the positions.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptobot.evedex import BotFiles, read_control, update_control
from cryptobot.settings import effective

log = logging.getLogger(__name__)
LABEL = "putseller"

SCHEMA = {
    "lots": {"label": "Размер, минимальных лотов", "type": "int", "min": 1, "max": 10, "step": 1},
    "target_delta": {"label": "Целевая дельта пута", "type": "float", "min": 0.03, "max": 0.3, "step": 0.01},
    "min_otm_pct": {"label": "Страйк минимум на % ниже цены", "type": "float", "min": 2, "max": 40, "step": 0.5},
    "min_dte": {"label": "Экспирация не раньше, дней", "type": "int", "min": 1, "max": 30, "step": 1},
    "max_dte": {"label": "Экспирация не позже, дней", "type": "int", "min": 2, "max": 45, "step": 1},
    "take_profit_pct": {"label": "Откупить, когда забрано % премии", "type": "float", "min": 10, "max": 95,
                        "step": 5},
    "stop_multiple": {"label": "Стоп: пут подорожал в … раз", "type": "float", "min": 1.5, "max": 10, "step": 0.5},
    "max_delta": {"label": "Стоп по дельте, |дельта| ≥", "type": "float", "min": 0.2, "max": 0.9, "step": 0.05},
    "max_margin_pct": {"label": "Залог не больше, % счёта", "type": "float", "min": 5, "max": 90, "step": 5},
}
RULES = [
    ["min_dte", "<", "max_dte", "Минимальный срок должен быть меньше максимального"],
    ["target_delta", "<", "max_delta", "Целевая дельта должна быть меньше стоп-дельты"],
]
# Weekly expiries are 7 days apart, so the window must be at least that wide to always hold one.
DEFAULTS = {"lots": 1, "target_delta": 0.12, "min_otm_pct": 8.0, "min_dte": 3, "max_dte": 10,
            "take_profit_pct": 75.0, "stop_multiple": 3.0, "max_delta": 0.4, "max_margin_pct": 50.0}
REPRICE_SECONDS = 30 * 60


MIN_OF_MARK = 0.75  # never sell below this share of the fair price


def asking_price(mark: float, bid: float, attempts: int) -> float:
    """Where to offer the put: the fair (mark) price first; after 2 unfilled re-prices (~1 h)
    halfway to the best bid; after 4 (~2 h) the bid itself — never below 75% of the mark."""
    floor = mark * MIN_OF_MARK
    if bid <= 0 or bid >= mark:
        return max(mark, bid)
    if attempts >= 4:
        return max(bid, floor)
    if attempts >= 2:
        return max((mark + bid) / 2, floor)
    return mark


class DeribitClient:
    """The few Deribit v2 calls the bot needs, through ccxt (auth, testnet switch, rate limits)."""

    def __init__(self, env: str = "testnet", client_id: str = "", client_secret: str = ""):
        import ccxt

        if env not in ("testnet", "prod"):
            raise ValueError(f"env must be testnet or prod, got {env}")
        self.env = env
        self.x = ccxt.deribit({"apiKey": client_id, "secret": client_secret, "enableRateLimit": True})
        if env == "testnet":
            self.x.set_sandbox_mode(True)

    @staticmethod
    def _r(response):
        return response["result"]

    def index(self, cur: str) -> float:
        return float(self._r(self.x.public_get_get_index_price({"index_name": f"{cur.lower()}_usd"}))["index_price"])

    def instruments(self, cur: str) -> list[dict]:
        return self._r(self.x.public_get_get_instruments({"currency": cur, "kind": "option", "expired": "false"}))

    def book(self, cur: str) -> list[dict]:
        return self._r(self.x.public_get_get_book_summary_by_currency({"currency": cur, "kind": "option"}))

    def ticker(self, name: str) -> dict:
        return self._r(self.x.public_get_ticker({"instrument_name": name}))

    def summary(self, cur: str) -> dict:
        return self._r(self.x.private_get_get_account_summary({"currency": cur}))

    def positions(self, cur: str) -> list[dict]:
        return self._r(self.x.private_get_get_positions({"currency": cur, "kind": "option"}))

    def margins(self, name: str, amount: float, price: float) -> dict:
        return self._r(self.x.private_get_get_margins({"instrument_name": name, "amount": amount, "price": price}))

    def order_state(self, order_id: str) -> dict:
        return self._r(self.x.private_get_get_order_state({"order_id": order_id}))

    def sell(self, name: str, amount: float, price: float) -> dict:
        return self._r(self.x.private_get_sell({"instrument_name": name, "amount": amount, "type": "limit",
                                                "price": price, "label": LABEL}))["order"]

    def buy_back(self, name: str, amount: float, price: float) -> dict:
        # Limit + immediate-or-cancel: a thin book (testnet) can't fill us at an absurd price;
        # whatever doesn't fill is retried on the next check.
        return self._r(self.x.private_get_buy({"instrument_name": name, "amount": amount, "type": "limit",
                                               "price": price, "time_in_force": "immediate_or_cancel",
                                               "reduce_only": "true", "label": LABEL}))["order"]

    def cancel(self, order_id: str) -> None:
        self.x.private_get_cancel({"order_id": order_id})

    def delivery_price(self, cur: str) -> float | None:
        data = self._r(self.x.public_get_get_delivery_prices({"index_name": f"{cur.lower()}_usd", "count": 1}))
        rows = data.get("data") or []
        return float(rows[0]["delivery_price"]) if rows else None


# --- pure helpers -------------------------------------------------------------------------

def put_delta(spot: float, strike: float, iv_pct: float, days: float) -> float:
    """Black-Scholes put delta (no rates), used to shortlist strikes before asking for real greeks."""
    if spot <= 0 or strike <= 0 or iv_pct <= 0 or days <= 0:
        return 0.0
    sigma, t = iv_pct / 100, days / 365
    d1 = (math.log(spot / strike) + 0.5 * sigma ** 2 * t) / (sigma * math.sqrt(t))
    return 0.5 * (1 + math.erf(d1 / math.sqrt(2))) - 1


def round_price(price: float, inst: dict, up: bool = False) -> float:
    """Round to the instrument's tick (which on Deribit can step up above some price); up for buys."""
    tick = float(inst.get("tick_size") or 0.0001)
    for step in sorted(inst.get("tick_size_steps") or [], key=lambda s: s["above_price"]):
        if price > float(step["above_price"]):
            tick = float(step["tick_size"])
    ticks = math.ceil(price / tick - 1e-9) if up else round(price / tick)
    return round(ticks * tick, 8)


def parse_expiry(inst: dict) -> datetime:
    return datetime.fromtimestamp(inst["expiration_timestamp"] / 1000, tz=timezone.utc)


def choose_put(instruments: list[dict], book: list[dict], spot: float, s: dict, now: datetime) -> dict | None:
    """The put to sell: nearest expiry in [min_dte, max_dte], strike <= spot*(1-min_otm), delta ~ target."""
    marks = {b["instrument_name"]: b for b in book}
    puts = [i for i in instruments if i.get("option_type") == "put" and i.get("is_active", True)]
    expiries = sorted({i["expiration_timestamp"] for i in puts
                       if s["min_dte"] <= (parse_expiry(i) - now).total_seconds() / 86400 <= s["max_dte"]})
    for exp in expiries:
        days = (exp / 1000 - now.timestamp()) / 86400
        best = None
        for i in puts:
            b = marks.get(i["instrument_name"]) or {}
            if i["expiration_timestamp"] != exp or i["strike"] > spot * (1 - s["min_otm_pct"] / 100):
                continue
            if not b.get("mark_price") or not b.get("mark_iv"):
                continue
            d = put_delta(spot, i["strike"], float(b["mark_iv"]), days)
            gap = abs(abs(d) - s["target_delta"])
            if best is None or gap < best[0] or gap == best[0] and i["strike"] < best[1]["strike"]:
                best = (gap, i, d)
        if best:
            return {**best[1], "est_delta": best[2], "days": days}
    return None


def exit_reason(sold: float, mark: float, delta: float, s: dict, fair: float = 0.0) -> str:
    """Take profit against what we got; the stop against the fair price at the sale if we sold
    below it (a cheap fill on a thin book must not pull the stop close)."""
    if mark <= sold * (1 - s["take_profit_pct"] / 100):
        return f"тейк: забрано {s['take_profit_pct']:.0f}% премии"
    ref = max(sold, fair)
    if mark >= ref * s["stop_multiple"]:
        return f"стоп: пут подорожал в {mark / ref:.1f} раза"
    if abs(delta) >= s["max_delta"]:
        return f"стоп: |дельта| {abs(delta):.2f} ≥ {s['max_delta']}"
    return ""


def settlement_payoff(strike: float, delivery: float) -> float:
    """Inverse put payoff per 1 coin of amount, in the coin."""
    return max(strike - delivery, 0.0) / delivery if delivery > 0 else 0.0


# --- the bot ------------------------------------------------------------------------------

class PutSeller:
    def __init__(self, client: DeribitClient, cur: str, base: dict, state_dir: str | Path, poll_seconds: int):
        self.c, self.cur, self.base, self.poll = client, cur, base, poll_seconds
        self.files = BotFiles(state_dir, client.env, cur, "", folder=f"deribit_{client.env}_{cur}_puts")
        self.memory = self.files.dir / "memory.json"  # positions the bot opened, pending order
        self.last: dict = {}
        self.error: str | None = None

    # persistent memory of what this bot owns
    def _mem(self) -> dict:
        m = read_control(self.memory)
        m.setdefault("managed", {})
        m.setdefault("pending", None)
        return m

    def _save(self, m: dict) -> None:
        tmp = self.memory.with_suffix(".tmp")
        tmp.write_text(json.dumps(m))
        tmp.replace(self.memory)

    def settings(self) -> tuple[dict, list[str], list[str]]:
        overrides = read_control(self.files.settings)
        overrides.pop("updated_at", None)
        cur, errs = effective(self.base, overrides, SCHEMA, RULES)
        return cur, errs, sorted(k for k in overrides if k in SCHEMA and not errs)

    def journal(self, action: str, instrument: str, amount: float, price: float, pnl: float | None,
                index: float, reason: str) -> None:
        self.files.append(self.files.trades, {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "env": self.c.env, "action": action,
            "instrument": instrument, "amount": amount, "price": price, "pnl_coin": pnl,
            "pnl_usd": None if pnl is None else round(pnl * index, 2), "index": index, "reason": reason,
        })

    def _buy_back(self, name: str, info: dict, m: dict, spot: float, why: str) -> float:
        """Try to buy the position back now; returns the amount still open."""
        t = self.c.ticker(name)
        ask, mark = float(t.get("best_ask_price") or 0), float(t.get("mark_price") or 0)
        price = round_price(ask if ask > 0 else mark * 1.3, info, up=True)
        order = self.c.buy_back(name, info["amount"], price)
        filled = float(order.get("filled_amount") or 0)
        if filled > 0:
            paid = float(order.get("average_price") or price)
            pnl = round((info["sold_price"] - paid) * filled, 8)
            self.journal("buyback", name, filled, paid, pnl, spot, why)
            log.info("%s bought back %s x%s at %s (%s), pnl=%s", self.cur, name, filled, paid, why, pnl)
        info["amount"] = round(info["amount"] - filled, 8)
        if info["amount"] <= 1e-9:
            del m["managed"][name]
            return 0.0
        log.warning("%s %s: %s still open after buy-back attempt (%s)", self.cur, name, info["amount"], why)
        return info["amount"]

    def step(self) -> None:
        s, serr, over = self.settings()
        m = self._mem()
        now = datetime.now(timezone.utc)
        spot = self.c.index(self.cur)
        positions = {p["instrument_name"]: p for p in self.c.positions(self.cur) if float(p.get("size") or 0) != 0}
        control = read_control(self.files.control)
        paused = bool(control.get("paused"))
        action, reason = "hold", "ждём"

        # 1. settle what disappeared without us buying it back: expiry
        for name, info in list(m["managed"].items()):
            if name in positions:
                continue
            if datetime.fromisoformat(info["expiry"]) > now:
                self.journal("gone", name, info["amount"], 0.0, None, spot, "позиция закрыта не ботом (вручную?)")
                del m["managed"][name]
                continue
            delivery = self.c.delivery_price(self.cur) or spot
            payoff = settlement_payoff(info["strike"], delivery)
            pnl = round((info["sold_price"] - payoff) * info["amount"], 8)
            self.journal("expired", name, info["amount"], payoff, pnl, delivery,
                         "экспирация вне денег — премия наша" if payoff == 0 else "экспирация в деньгах")
            log.info("%s %s expired, payoff=%s pnl=%s %s", self.cur, name, payoff, pnl, self.cur)
            del m["managed"][name]
            action, reason = "expired", "позиция истекла"

        # 2. a sell order waiting to fill
        if m["pending"]:
            p = m["pending"]
            st = self.c.order_state(p["order_id"])
            filled = float(st.get("filled_amount") or 0)
            if filled > 0 and st.get("order_state") in ("filled", "cancelled"):
                m["managed"][p["instrument"]] = {"sold_price": float(st["average_price"]), "amount": filled,
                                                  "fair_price": p.get("mark", 0.0),
                                                  "strike": p["strike"], "expiry": p["expiry"],
                                                  "tick_size": p["tick_size"], "tick_size_steps": p["tick_size_steps"],
                                                  "opened_at": now.isoformat(timespec="seconds")}
                self.journal("sell", p["instrument"], filled, float(st["average_price"]), None, spot,
                             f"продан пут, дельта ≈ {p['delta']:.2f}")
                m["pending"] = None
                m.pop("attempts", None)
                action, reason = "sell", "ордер исполнен"
            elif st.get("order_state") in ("cancelled", "rejected"):
                m["pending"] = None
            elif time.time() - p["placed_at"] > REPRICE_SECONDS or paused:
                self.c.cancel(p["order_id"])
                # Remember how long we've been trying, so the next order can concede a little.
                m["attempts"] = {"instrument": p["instrument"], "count": p.get("attempt", 0) + 1}
                if filled > 0:  # partly filled: keep that part, re-check on the next step
                    st = self.c.order_state(p["order_id"])
                    m["managed"][p["instrument"]] = {"sold_price": float(st["average_price"]),
                                                      "amount": float(st["filled_amount"]), "strike": p["strike"],
                                                      "fair_price": p.get("mark", 0.0),
                                                      "expiry": p["expiry"], "tick_size": p["tick_size"],
                                                      "tick_size_steps": p["tick_size_steps"],
                                                      "opened_at": now.isoformat(timespec="seconds")}
                m["pending"] = None
                action, reason = "hold", "ордер не исполнился за 30 минут — переставим"

        # 3. panel asked to close everything
        if control.get("close_requested"):
            if m["pending"]:
                self.c.cancel(m["pending"]["order_id"])
                m["pending"] = None
            left = sum(self._buy_back(name, info, m, spot, "закрыто из панели")
                       for name, info in list(m["managed"].items()))
            # Stays requested until everything is bought back; paused either way.
            update_control(self.files.control, paused=True, close_requested=left > 0)
            paused, action = True, "buyback"
            reason = "закрыто из панели" if not left else "закрываем — часть не исполнилась, повторим"

        # 4. exits for open positions
        views = []
        for name, info in list(m["managed"].items()):
            t = self.c.ticker(name)
            mark, delta = float(t.get("mark_price") or 0), float((t.get("greeks") or {}).get("delta") or 0)
            why = exit_reason(info["sold_price"], mark, delta, s, info.get("fair_price", 0.0))
            if why:
                action, reason = "buyback", why
                if not self._buy_back(name, info, m, spot, why):
                    continue
            views.append({**info, "instrument": name, "mark": mark, "delta": delta,
                          "pnl_coin": round((info["sold_price"] - mark) * info["amount"], 8),
                          "captured_pct": round((1 - mark / info["sold_price"]) * 100, 1) if info["sold_price"] else 0})

        # 5. entry
        summary = self.c.summary(self.cur)
        equity = float(summary.get("equity") or 0)
        if not m["managed"] and not m["pending"] and action not in ("buyback", "expired"):
            if paused:
                reason = "пауза — новые продажи отключены"
            else:
                action, reason = self._enter(s, m, spot, equity, float(summary.get("available_funds") or 0), now)

        if m["pending"] and reason == "ждём":
            reason = f"ждём, пока купят наш ордер (попытка {m['pending'].get('attempt', 0) + 1})"
        if action == "hold" and reason == "ждём" and views:
            reason = "держим позицию: тейк и стоп не достигнуты, до экспирации {}".format(
                ", ".join(v["expiry"][:10] for v in views))
        log.info("%s: %s — %s", self.cur, action, reason)
        self._save(m)
        self.files.append(self.files.equity, {"time": now.isoformat(timespec="seconds"), "equity": equity,
                                              "equity_usd": round(equity * spot, 2), "index": spot})
        self.last = {
            "kind": "options", "currency": self.cur, "index": spot, "equity": equity,
            "equity_usd": round(equity * spot, 2), "available": float(summary.get("available_funds") or 0),
            "initial_margin": float(summary.get("initial_margin") or 0), "positions": views,
            "pending": m["pending"], "action": action, "reason": reason, "checked_at": now.isoformat(),
            "settings": (s, serr, over),
        }

    def _enter(self, s: dict, m: dict, spot: float, equity: float, available: float, now: datetime):
        instruments = self.c.instruments(self.cur)
        put = choose_put(instruments, self.c.book(self.cur), spot, s, now)
        if not put:
            soon = sorted({round((parse_expiry(i) - now).total_seconds() / 86400, 1) for i in instruments
                           if i.get("option_type") == "put" and parse_expiry(i) > now})[:4]
            return "hold", (f"нет подходящего пута на {s['min_dte']}–{s['max_dte']} дней; ближайшие экспирации "
                            f"через {', '.join(str(d) for d in soon) or '—'} дн.")
        t = self.c.ticker(put["instrument_name"])
        delta = float((t.get("greeks") or {}).get("delta") or put["est_delta"])
        if abs(delta) >= s["max_delta"]:
            return "hold", f"у выбранного пута |дельта| {abs(delta):.2f} — слишком рискованно"
        tries = m.get("attempts") or {}
        attempt = tries.get("count", 0) if tries.get("instrument") == put["instrument_name"] else 0
        price = round_price(asking_price(float(t.get("mark_price") or 0), float(t.get("best_bid_price") or 0),
                                         attempt), put)
        tick = float(put.get("tick_size") or 0.0001)
        if price < 2 * tick:
            return "hold", "премия слишком мала"
        amount = float(put["min_trade_amount"]) * s["lots"]
        margin = float(self.c.margins(put["instrument_name"], amount, price).get("sell") or 0)
        limit = equity * s["max_margin_pct"] / 100
        if margin > available or margin > limit:
            return "hold", f"мало залога: нужно {margin:.4f} {self.cur}, доступно {min(available, limit):.4f}"
        order = self.c.sell(put["instrument_name"], amount, price)
        m["pending"] = {"order_id": order["order_id"], "instrument": put["instrument_name"], "price": price,
                        "mark": float(t.get("mark_price") or 0),
                        "strike": put["strike"], "expiry": parse_expiry(put).isoformat(), "delta": delta,
                        "placed_at": time.time(), "amount": amount, "attempt": attempt,
                        "tick_size": put.get("tick_size"),
                        "tick_size_steps": put.get("tick_size_steps") or []}
        log.info("%s selling %s x%s at %s (delta %.2f, margin %s)", self.cur, put["instrument_name"], amount,
                 price, delta, margin)
        return "sell_order", f"выставлен ордер: {put['instrument_name']} по {price} {self.cur}"

    def write_status(self) -> None:
        s, serr, over = self.last.pop("settings", None) or (self.base, [], [])
        fields = {**self.last, "kind": "options"}  # kind even before the first successful check
        self.files.write_status(
            config={"env": self.c.env, "instrument": f"{self.cur} путы", "currency": self.cur,
                    "signal": "Deribit, недельные путы", **s, "settings_schema": SCHEMA, "settings_rules": RULES,
                    "settings_defaults": self.base, "settings_overrides": over, "settings_error": "; ".join(serr)},
            paused=bool(read_control(self.files.control).get("paused")), error=self.error,
            poll_seconds=self.poll, **fields)
        self.last["settings"] = (s, serr, over)


def describe_error(exc: Exception, check_seconds: int) -> str:
    """Short text for the panel: exchange outages come back as whole HTML error pages."""
    import re

    import ccxt

    text = str(exc)
    if isinstance(exc, (ccxt.ExchangeNotAvailable, ccxt.NetworkError)):
        code = re.search(r"\b(5\d\d)\b", text)
        return (f"Deribit временно недоступен{f' ({code.group(1)})' if code else ''}: "
                f"проверим снова через {max(1, check_seconds // 60)} мин. Позиции на бирже не затронуты.")
    text = re.sub(r"<[^>]+>|<!--.*?-->", " ", text)  # just in case another error carries HTML
    return f"{type(exc).__name__}: {' '.join(text.split())}"[:400]


def run_options(client: DeribitClient, currencies: list[str], base: dict, state_dir: str | Path = "paper_state",
                check_seconds: int = 300, poll_seconds: int = 60) -> None:
    import ccxt

    bots = [PutSeller(client, cur.upper(), base, state_dir, poll_seconds) for cur in currencies]
    log.info("Deribit put seller [%s] on %s, settings %s", client.env.upper(), ", ".join(b.cur for b in bots), base)
    next_check = 0.0
    while True:
        due = time.monotonic() >= next_check
        for bot in bots:
            # The panel's buttons are acted on within a poll; the market is checked every check_seconds.
            if due or read_control(bot.files.control).get("close_requested"):
                try:
                    bot.step()
                    bot.error = None
                except (ccxt.BaseError, KeyError, ValueError, TypeError) as exc:
                    log.warning("%s: %s", bot.cur, str(exc)[:300])
                    bot.error = describe_error(exc, check_seconds)  # shown until the next successful check
            bot.write_status()
        if due:
            next_check = time.monotonic() + check_seconds
        time.sleep(poll_seconds)
