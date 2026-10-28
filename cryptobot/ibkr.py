"""Weekly put seller for US stock/ETF options through Interactive Brokers (paper account by default).

Same idea as options.py (Deribit): sell an out-of-the-money put on the nearest expiry
MIN..MAX days out with delta near TARGET, buy it back at TAKE_PROFIT % of the premium or
on a stop (STOP_MULTIPLE x premium, |delta| >= MAX_DELTA), otherwise let it expire.
Differences that matter on US options:
  * prices are per share, one contract is 100 shares, everything is in USD;
  * they settle in shares: an in-the-money put at expiry means buying 100 shares, so on
    expiry day after 15:00 New York time a put with |delta| >= 0.2 is bought back, and if
    shares still arrive (assignment) the bot pauses and says so;
  * new sales only in regular trading hours; quotes are 15-minute delayed unless the
    account has a market-data subscription.
The bot talks to IB Gateway (the ib-gateway service), which logs in to the account.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptobot.evedex import BotFiles, read_control, update_control
from cryptobot.options import RULES, SCHEMA as DERIBIT_SCHEMA, exit_reason, put_delta
from cryptobot.settings import effective

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
LABEL = "putseller"
REPRICE_SECONDS = 30 * 60

SCHEMA = {**DERIBIT_SCHEMA, "lots": {**DERIBIT_SCHEMA["lots"], "label": "Размер, контрактов (1 = 100 акций)"}}
# Index ETFs move far less than crypto: a 3% cushion and delta ~0.15 still pays a premium.
DEFAULTS = {"lots": 1, "target_delta": 0.15, "min_otm_pct": 3.0, "min_dte": 3, "max_dte": 10,
            "take_profit_pct": 75.0, "stop_multiple": 3.0, "max_delta": 0.45, "max_margin_pct": 50.0}


def expiry_close(yyyymmdd: str) -> datetime:
    """US equity options stop trading at 16:00 New York time on the expiry date."""
    d = datetime.strptime(yyyymmdd, "%Y%m%d")
    return d.replace(hour=16, tzinfo=NY).astimezone(timezone.utc)


def market_open(now: datetime) -> bool:
    ny = now.astimezone(NY)
    minutes = ny.hour * 60 + ny.minute
    return ny.weekday() < 5 and 9 * 60 + 35 <= minutes <= 15 * 60 + 45


def expiry_day_late(expiry: str, now: datetime) -> bool:
    ny = now.astimezone(NY)
    return ny.strftime("%Y%m%d") == expiry and ny.hour >= 15


def _num(v) -> float | None:
    """IB sends NaN or -1 for "no data"; turn those into None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) and f >= 0 else None


def round_cents(price: float, up: bool = False) -> float:
    return (math.ceil(price * 100 - 1e-9) if up else round(price * 100)) / 100


class IbClient:
    """The IB Gateway calls the bot needs, returning plain dicts (so the strategy can be tested)."""

    def __init__(self, host: str = "ib-gateway", port: int = 4004, client_id: int = 7, delayed: bool = True):
        from ib_async import IB

        self.ib, self.host, self.port, self.client_id, self.delayed = IB(), host, port, client_id, delayed
        self._contracts: dict[str, object] = {}

    def ensure(self) -> None:
        if not self.ib.isConnected():
            self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=20)
            # 3 = delayed data when there is no subscription (free); live data is used when available.
            self.ib.reqMarketDataType(3 if self.delayed else 1)

    @staticmethod
    def key(symbol: str, expiry: str, strike: float) -> str:
        return f"{symbol} {expiry} {strike:g} P"

    def _price(self, t) -> float:
        for v in (t.markPrice, t.marketPrice(), t.close):
            if v is not None and v == v and v > 0:
                return float(v)
        return 0.0

    def spot(self, symbol: str) -> float:
        from ib_async import Stock

        stock = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(stock)
        self._contracts[symbol] = stock
        return self._price(self.ib.reqTickers(stock)[0])

    def chain(self, symbol: str) -> dict:
        stock = self._contracts[symbol]
        chains = self.ib.reqSecDefOptParams(symbol, "", "STK", stock.conId)
        c = next((c for c in chains if c.exchange == "SMART" and c.tradingClass == symbol), chains[0])
        return {"expirations": sorted(c.expirations), "strikes": sorted(c.strikes), "trading_class": c.tradingClass}

    def expiry_strikes(self, symbol: str, expiry: str, trading_class: str) -> list[float]:
        """Strikes that really exist for this expiry (the chain's strike list is the union of all)."""
        from ib_async import Option

        details = self.ib.reqContractDetails(Option(symbol, expiry, 0.0, "P", "SMART", tradingClass=trading_class))
        for d in details:
            self._contracts[self.key(symbol, expiry, d.contract.strike)] = d.contract
        return sorted({float(d.contract.strike) for d in details})

    def quotes(self, symbol: str, expiry: str, strikes: list[float], trading_class: str) -> list[dict]:
        from ib_async import Option

        opts = []
        for k in strikes:
            key = self.key(symbol, expiry, k)
            if key not in self._contracts:
                o = Option(symbol, expiry, k, "P", "SMART", tradingClass=trading_class, multiplier="100")
                q = self.ib.qualifyContracts(o)
                if not q or q[0] is None or not getattr(q[0], "conId", 0):
                    continue  # no such contract for this expiry
                self._contracts[key] = q[0]
            opts.append(self._contracts[key])
        out = []
        for o, t in zip(opts, self.ib.reqTickers(*opts) if opts else []):
            g = t.modelGreeks
            opt_price = _num(g.optPrice) if g else None
            out.append({"key": self.key(symbol, expiry, o.strike), "symbol": symbol, "expiry": expiry,
                        "strike": float(o.strike), "bid": _num(t.bid) or 0.0, "ask": _num(t.ask) or 0.0,
                        "mark": opt_price if opt_price else self._price(t),
                        "delta": float(g.delta) if g and g.delta is not None and g.delta == g.delta else None,
                        "iv": _num(g.impliedVol) if g else None})
        return out

    def account(self) -> dict:
        values = {v.tag: float(v.value) for v in self.ib.accountSummary()
                  if v.currency in ("USD", "BASE") and v.tag in ("NetLiquidation", "AvailableFunds", "InitMarginReq")}
        return {"net_liq": values.get("NetLiquidation", 0.0), "available": values.get("AvailableFunds", 0.0),
                "init_margin": values.get("InitMarginReq", 0.0)}

    def positions(self) -> list[dict]:
        out = []
        for p in self.ib.positions():
            c = p.contract
            row = {"sec_type": c.secType, "symbol": c.symbol, "qty": float(p.position), "avg_cost": float(p.avgCost)}
            if c.secType == "OPT":
                row.update(key=self.key(c.symbol, c.lastTradeDateOrContractMonth, c.strike), right=c.right,
                           strike=float(c.strike), expiry=c.lastTradeDateOrContractMonth)
                self._contracts.setdefault(row["key"], c)
            out.append(row)
        return out

    def _contract(self, key: str):
        if key not in self._contracts:
            from ib_async import Option

            symbol, expiry, strike, _ = key.split()
            o = Option(symbol, expiry, float(strike), "P", "SMART", multiplier="100")
            self.ib.qualifyContracts(o)
            self._contracts[key] = o
        return self._contracts[key]

    def margin(self, key: str, qty: int, price: float) -> float:
        from ib_async import LimitOrder

        state = self.ib.whatIfOrder(self._contract(key), LimitOrder("SELL", qty, price))
        try:
            return float(state.initMarginChange)
        except (TypeError, ValueError):
            return 0.0

    def sell(self, key: str, qty: int, price: float) -> str:
        from ib_async import LimitOrder

        trade = self.ib.placeOrder(self._contract(key), LimitOrder("SELL", qty, price, orderRef=LABEL, tif="DAY"))
        self.ib.sleep(1)
        return str(trade.order.permId or trade.order.orderId)

    def _trade(self, order_id: str):
        for attempt in range(2):
            for t in self.ib.trades():
                if str(t.order.permId) == order_id or str(t.order.orderId) == order_id:
                    return t
            if attempt == 0:
                self.ib.reqAllOpenOrders()  # after a bot restart its earlier orders aren't in trades() yet
        return None

    def open_sells(self, symbol: str) -> int:
        """Our sell orders still working for this symbol (also ones placed before a restart)."""
        self.ib.reqAllOpenOrders()
        return sum(1 for t in self.ib.openTrades() if t.order.orderRef == LABEL and t.order.action == "SELL"
                   and t.contract.symbol == symbol and not t.isDone())

    def order_state(self, order_id: str) -> dict:
        t = self._trade(order_id)
        if t is None:
            return {"status": "Unknown", "filled": 0.0, "avg": 0.0}
        return {"status": t.orderStatus.status, "filled": float(t.orderStatus.filled),
                "avg": float(t.orderStatus.avgFillPrice or 0)}

    def cancel(self, order_id: str) -> None:
        t = self._trade(order_id)
        if t is not None:
            self.ib.cancelOrder(t.order)

    def buy_back(self, key: str, qty: int, price: float) -> dict:
        from ib_async import LimitOrder

        trade = self.ib.placeOrder(self._contract(key), LimitOrder("BUY", qty, price, orderRef=LABEL, tif="IOC"))
        for _ in range(10):
            self.ib.sleep(1)
            if trade.isDone():
                break
        return {"filled": float(trade.orderStatus.filled), "avg": float(trade.orderStatus.avgFillPrice or price)}

    def wait(self, seconds: float) -> None:
        self.ib.sleep(seconds)


class IbPutSeller:
    def __init__(self, client, symbol: str, env: str, base: dict, state_dir: str | Path, poll_seconds: int,
                 delayed: bool = True):
        self.c, self.sym, self.env, self.base, self.poll, self.delayed = client, symbol, env, base, poll_seconds, delayed
        self.files = BotFiles(state_dir, env, symbol, "", folder=f"ibkr_{env}_{symbol}_puts")
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
        cur, errs = effective(self.base, overrides, SCHEMA, RULES)
        return cur, errs, sorted(k for k in overrides if k in SCHEMA and not errs)

    def journal(self, action, key, qty, price, pnl, spot, reason):
        self.files.append(self.files.trades, {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "env": self.env, "action": action,
            "instrument": key, "amount": qty, "price": price, "pnl_coin": pnl, "pnl_usd": pnl, "index": spot,
            "reason": reason})

    def _buy_back(self, key: str, info: dict, m: dict, spot: float, why: str) -> int:
        q = self.c.quotes(self.sym, info["expiry"], [info["strike"]], info["trading_class"])
        mark = q[0]["mark"] if q else info["sold_price"] * 2
        ask = q[0]["ask"] if q else 0.0
        res = self.c.buy_back(key, int(info["qty"]), round_cents(ask if ask > 0 else mark * 1.3, up=True))
        filled = int(res["filled"])
        if filled:
            pnl = round((info["sold_price"] - res["avg"]) * filled * 100, 2)
            self.journal("buyback", key, filled, res["avg"], pnl, spot, why)
            log.info("%s bought back %s x%s at %s (%s), pnl=$%s", self.sym, key, filled, res["avg"], why, pnl)
        info["qty"] -= filled
        if info["qty"] <= 0:
            del m["managed"][key]
            return 0
        return info["qty"]

    def step(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        s, serr, over = self.settings()
        m = self._mem()
        spot = self.c.spot(self.sym)
        positions = self.c.positions()
        opts = {p["key"]: p for p in positions if p["sec_type"] == "OPT" and p["symbol"] == self.sym and p["qty"]}
        shares = sum(p["qty"] for p in positions if p["sec_type"] == "STK" and p["symbol"] == self.sym)
        control = read_control(self.files.control)
        paused = bool(control.get("paused"))
        action, reason = "hold", "ждём"

        # 1. positions gone: expired worthless, assigned, or closed by hand
        for key, info in list(m["managed"].items()):
            if key in opts:
                continue
            if now >= expiry_close(info["expiry"]):
                if shares > 0:
                    self.journal("assigned", key, info["qty"], info["strike"], None, spot,
                                 f"исполнение: получено {shares:g} акций {self.sym} по {info['strike']:g}")
                    update_control(self.files.control, paused=True)
                    paused, action, reason = True, "assigned", f"получены акции {self.sym} — бот на паузе, решите, что с ними делать"
                else:
                    pnl = round(info["sold_price"] * info["qty"] * 100, 2)
                    self.journal("expired", key, info["qty"], 0.0, pnl, spot, "экспирация вне денег — премия наша")
                    action, reason = "expired", "позиция истекла"
            else:
                self.journal("gone", key, info["qty"], 0.0, None, spot, "позиция закрыта не ботом (вручную?)")
            del m["managed"][key]
        if shares and action != "assigned" and not paused:
            update_control(self.files.control, paused=True)
            paused, reason = True, f"на счёте {shares:g} акций {self.sym} — бот на паузе"

        # 2. pending sell order
        if m["pending"]:
            p = m["pending"]
            st = self.c.order_state(p["order_id"])
            if st["status"] == "Filled" or (st["filled"] and st["status"] in ("Cancelled", "ApiCancelled", "Inactive")):
                m["managed"][p["key"]] = {"sold_price": st["avg"] or p["price"], "qty": int(st["filled"]),
                                         "strike": p["strike"], "expiry": p["expiry"],
                                         "trading_class": p["trading_class"], "opened_at": now.isoformat()}
                self.journal("sell", p["key"], int(st["filled"]), st["avg"] or p["price"], None, spot,
                             f"продан пут, дельта ≈ {p['delta']:.2f}")
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
            left = sum(self._buy_back(k, i, m, spot, "закрыто из панели") for k, i in list(m["managed"].items()))
            update_control(self.files.control, paused=True, close_requested=left > 0)
            paused, action = True, "buyback"
            reason = "закрыто из панели" if not left else "закрываем — часть не исполнилась, повторим"

        # 4. exits
        views = []
        for key, info in list(m["managed"].items()):
            q = self.c.quotes(self.sym, info["expiry"], [info["strike"]], info["trading_class"])
            mark = q[0]["mark"] if q else 0.0
            delta = q[0]["delta"] if q and q[0]["delta"] is not None else 0.0
            why = exit_reason(info["sold_price"], mark, delta, s) if mark else ""
            if not why and expiry_day_late(info["expiry"], now) and abs(delta) >= 0.2:
                why = "перед экспирацией: пут близко к деньгам, чтобы не получить акции"
            if why:
                action, reason = "buyback", why
                if not self._buy_back(key, info, m, spot, why):
                    continue
            views.append({"instrument": key, "strike": info["strike"],
                          "expiry": expiry_close(info["expiry"]).isoformat(), "amount": info["qty"],
                          "sold_price": info["sold_price"], "mark": mark, "delta": delta,
                          "pnl_coin": round((info["sold_price"] - mark) * info["qty"] * 100, 2),
                          "captured_pct": round((1 - mark / info["sold_price"]) * 100, 1) if info["sold_price"] else 0})

        # 5. entry
        acct = self.c.account()
        if not m["managed"] and not m["pending"] and action not in ("buyback", "expired", "assigned"):
            if paused:
                reason = reason if reason.startswith("на счёте") else "пауза — новые продажи отключены"
            elif not market_open(now):
                reason = "биржа закрыта — продажи пн–пт 9:35–15:45 по Нью-Йорку"
            else:
                action, reason = self._enter(s, m, spot, acct, now)
        if m["pending"] and reason == "ждём":
            reason = "ждём, пока купят наш ордер"
        if action == "hold" and reason == "ждём" and views:
            reason = "держим позицию: тейк и стоп не достигнуты"
        log.info("%s: %s — %s", self.sym, action, reason)

        self._save(m)
        self.files.append(self.files.equity, {"time": now.isoformat(timespec="seconds"), "equity": acct["net_liq"],
                                              "equity_usd": acct["net_liq"], "index": spot})
        self.last = {"kind": "options", "currency": "USD", "index": spot, "equity": acct["net_liq"],
                     "equity_usd": acct["net_liq"], "available": acct["available"],
                     "initial_margin": acct["init_margin"], "positions": views, "pending": m["pending"],
                     "action": action, "reason": reason, "checked_at": now.isoformat(), "settings": (s, serr, over)}

    def _enter(self, s: dict, m: dict, spot: float, acct: dict, now: datetime):
        if self.c.open_sells(self.sym):
            return "hold", "на бирже уже висит наш ордер (бот перезапускался) — ждём его исполнения или отмены"
        ch = self.c.chain(self.sym)
        expiries = [e for e in ch["expirations"]
                    if s["min_dte"] <= (expiry_close(e) - now).total_seconds() / 86400 <= s["max_dte"]]
        if not expiries:
            return "hold", f"нет экспирации на {s['min_dte']}–{s['max_dte']} дней"
        expiry = expiries[0]
        limit = spot * (1 - s["min_otm_pct"] / 100)
        real = self.c.expiry_strikes(self.sym, expiry, ch["trading_class"])
        strikes = [k for k in real if k <= limit][-12:]  # the dozen strikes just below the cushion
        if not strikes:
            return "hold", "нет подходящих страйков"
        days = (expiry_close(expiry) - now).total_seconds() / 86400
        best = None
        quotes = self.c.quotes(self.sym, expiry, strikes, ch["trading_class"])
        if quotes and not any(q["bid"] > 0 or q["mark"] > 0 for q in quotes):
            return "hold", ("IBKR не прислал котировки по опционам — похоже, нужна подписка на данные "
                            "US Options (OPRA) для бумажного счёта")
        for q in quotes:
            if q["bid"] <= 0 and q["mark"] <= 0:
                continue
            d = q["delta"] if q["delta"] is not None else (put_delta(spot, q["strike"], q["iv"] * 100, days)
                                                           if q["iv"] else None)
            if d is None or abs(d) >= s["max_delta"]:
                continue
            gap = abs(abs(d) - s["target_delta"])
            if best is None or gap < best[0]:
                best = (gap, q, d)
        if not best:
            return "hold", "нет котировок по путам (нет данных или все слишком рискованные)"
        _, q, delta = best
        price = round_cents(max(q["mark"], q["bid"]))
        if price < 0.05:
            return "hold", f"премия слишком мала (${price:.2f} за акцию)"
        qty = int(s["lots"])
        margin = self.c.margin(q["key"], qty, price)
        cap = acct["net_liq"] * s["max_margin_pct"] / 100
        if margin > acct["available"] or margin > cap:
            return "hold", f"мало залога: нужно ${margin:,.0f}, доступно ${min(acct['available'], cap):,.0f}"
        order_id = self.c.sell(q["key"], qty, price)
        m["pending"] = {"order_id": order_id, "key": q["key"], "price": price, "strike": q["strike"],
                        "expiry": expiry, "trading_class": ch["trading_class"], "delta": delta,
                        "placed_at": time.time(), "instrument": q["key"]}
        log.info("%s selling %s x%s at $%s (delta %.2f, margin $%.0f)", self.sym, q["key"], qty, price, delta, margin)
        return "sell_order", f"выставлен ордер: {q['key']} по ${price:.2f} за акцию (${price * 100 * qty:.0f})"

    def write_status(self) -> None:
        s, serr, over = self.last.pop("settings", None) or (self.base, [], [])
        fields = {**self.last, "kind": "options"}
        self.files.write_status(
            config={"env": self.env, "instrument": f"{self.sym} путы", "currency": "USD", "underlying": self.sym,
                    "signal": "Interactive Brokers · опционы" + (" · котировки с задержкой 15 мин" if self.delayed else ""),
                    **s, "settings_schema": SCHEMA, "settings_rules": RULES, "settings_defaults": self.base,
                    "settings_overrides": over, "settings_error": "; ".join(serr)},
            paused=bool(read_control(self.files.control).get("paused")), error=self.error,
            poll_seconds=self.poll, **fields)
        self.last["settings"] = (s, serr, over)


def describe_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)) or "connect" in text.lower():
        return ("нет связи с IB Gateway: он входит в аккаунт или перезапускается (раз в сутки). "
                f"Повторим автоматически. ({type(exc).__name__}: {text[:150]})")
    return f"{type(exc).__name__}: {text}"[:400]


def run_ibkr(client, env: str, symbols: list[str], base: dict, state_dir: str | Path = "paper_state",
             check_seconds: int = 300, poll_seconds: int = 60, delayed: bool = True) -> None:
    bots = [IbPutSeller(client, s.upper(), env, base, state_dir, poll_seconds, delayed) for s in symbols]
    log.info("IBKR put seller [%s] on %s, settings %s", env.upper(), ", ".join(b.sym for b in bots), base)
    next_check = 0.0
    while True:
        due = time.monotonic() >= next_check
        for bot in bots:
            if due or read_control(bot.files.control).get("close_requested"):
                try:
                    client.ensure()
                    bot.step()
                    bot.error = None
                except Exception as exc:  # the gateway restarts daily; never let that kill the loop
                    log.warning("%s: %s", bot.sym, str(exc)[:300])
                    bot.error = describe_error(exc)
            bot.write_status()
        if due:
            next_check = time.monotonic() + check_seconds
        client.wait(poll_seconds)  # ib.sleep keeps the gateway connection serviced while waiting
