"""Paper arbitrage on real prices from several exchanges (public data only, no keys, no money).

Two bots, each in its own panel folder:

* SpreadBot  — "buy where it's cheaper, sell where it's dearer". Assumes coins and USDT already
  sit on every exchange (no transfers per trade), so a trade is a simultaneous buy on one and
  sell on the other at the top of the book. Costs: both taker fees plus a rebalancing charge for
  moving coins back now and then. Gaps that are too large are usually suspended deposits or a
  different token with the same ticker, so they are reported, never traded.
* FundingBot — buy the coin and short the same amount of its perpetual future: price moves cancel
  out and the short collects the funding rate. Enters when the yearly rate is high enough, leaves
  when it fades. Charges taker fees on both legs at entry and exit.

Both only simulate trades; they never place orders.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptobot.evedex import BotFiles, read_control, update_control
from cryptobot.settings import effective

log = logging.getLogger(__name__)

EXCHANGES = ["binance", "bybit", "okx", "kucoin", "gate", "mexc", "bitget"]
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "TRX", "LTC", "AVAX", "LINK", "TON", "SUI"]
TITLES = {"bybit": "Bybit", "okx": "OKX", "kucoin": "KuCoin", "gate": "Gate", "mexc": "MEXC", "bitget": "Bitget",
          "binance": "Binance", "htx": "HTX"}
STALE_SECONDS = 120


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds") if ts else None


def title(ex: str) -> str:
    return TITLES.get(ex, ex)


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _short(exc: Exception) -> str:
    text = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    if "<html" in text.lower() or len(text) > 160:
        text = type(exc).__name__
    return text[:160]


# --- market data ----------------------------------------------------------------------

class Feeds:
    """Spot tickers and perpetual funding rates through ccxt's public endpoints."""

    def __init__(self, exchanges: list[str]):
        self.names = exchanges
        self._x: dict[tuple[str, str], object] = {}

    def _ex(self, name: str, kind: str):
        key = (name, kind)
        if key not in self._x:
            import ccxt

            x = getattr(ccxt, name)({"enableRateLimit": True, "timeout": 15000, "options": {"defaultType": kind}})
            x.load_markets()
            self._x[key] = x
        return self._x[key]

    def spot(self, coins: list[str]) -> tuple[dict, dict]:
        quotes, errors = {}, {}
        for name in self.names:
            try:
                x = self._ex(name, "spot")
                symbols = [f"{c}/USDT" for c in coins if f"{c}/USDT" in x.markets]
                try:
                    tickers = x.fetch_tickers(symbols)
                except Exception:  # some exchanges only return everything at once
                    tickers = {k: v for k, v in x.fetch_tickers().items() if k in symbols}
                for sym, t in tickers.items():
                    quotes[(name, sym.split("/")[0])] = {
                        "bid": _num(t.get("bid")) or 0.0, "ask": _num(t.get("ask")) or 0.0,
                        "bid_size": _num(t.get("bidVolume")), "ask_size": _num(t.get("askVolume")),
                        "ts": (t.get("timestamp") or 0) / 1000 or None}
            except Exception as exc:
                self._x.pop((name, "spot"), None)
                errors[name] = _short(exc)
        return quotes, errors

    def funding(self, coins: list[str]) -> tuple[dict, dict]:
        rates, errors = {}, {}
        for name in self.names:
            try:
                x = self._ex(name, "swap")
                symbols = [f"{c}/USDT:USDT" for c in coins if f"{c}/USDT:USDT" in x.markets]
                if x.has.get("fetchFundingRates"):
                    try:
                        data = x.fetch_funding_rates(symbols)
                    except Exception:
                        data = {k: v for k, v in x.fetch_funding_rates().items() if k in symbols}
                else:
                    data = {s: x.fetch_funding_rate(s) for s in symbols}
                for sym, f in data.items():
                    rate = _num(f.get("fundingRate"))
                    if rate is None:
                        continue
                    hours = _interval_hours(f.get("interval"))
                    rates[(name, sym.split("/")[0])] = {
                        "rate": rate, "interval_h": hours,
                        "next_ts": (f.get("nextFundingTimestamp") or f.get("fundingTimestamp") or 0) / 1000 or None}
            except Exception as exc:
                self._x.pop((name, "swap"), None)
                errors[name] = _short(exc)
        return rates, errors


def _interval_hours(value) -> float:
    if isinstance(value, str) and value.endswith("h"):
        return _num(value[:-1]) or 8.0
    return 8.0


def apr(rate: float, interval_h: float) -> float:
    """Funding rate per interval -> % per year."""
    return rate * (24 / interval_h) * 365 * 100


# --- shared bot plumbing ---------------------------------------------------------------

class PaperBot:
    kind = "arb"
    folder = ""
    label = ""
    SCHEMA: dict = {}
    RULES: list = []
    DEFAULTS: dict = {}

    def __init__(self, state_dir: str | Path, poll_seconds: int, base: dict | None = None):
        self.poll = poll_seconds
        self.base = {**self.DEFAULTS, **(base or {})}
        self.files = BotFiles(state_dir, "paper", "", "", folder=self.folder)
        self.memory = self.files.dir / "memory.json"
        self.error: str | None = None
        self.last: dict = {}

    def settings(self) -> tuple[dict, list[str], list[str]]:
        overrides = read_control(self.files.settings)
        overrides.pop("updated_at", None)
        cur, errs = effective(self.base, overrides, self.SCHEMA, self.RULES)
        return cur, errs, sorted(k for k in overrides if k in self.SCHEMA and not errs)

    def _mem(self) -> dict:
        return read_control(self.memory)

    def _save(self, m: dict) -> None:
        tmp = self.memory.with_suffix(".tmp")
        tmp.write_text(json.dumps(m))
        tmp.replace(self.memory)

    def journal(self, row: dict) -> None:
        self.files.append(self.files.trades, {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                               "env": "paper", **row})

    def write_status(self, settings: tuple, extra_config: dict) -> None:
        s, serr, over = settings
        self.files.write_status(
            config={"env": "paper", "instrument": self.label, "currency": "USD", **extra_config, **s,
                    "settings_schema": self.SCHEMA, "settings_rules": self.RULES, "settings_defaults": self.base,
                    "settings_overrides": over, "settings_error": "; ".join(serr)},
            kind=self.kind, paused=bool(read_control(self.files.control).get("paused")), error=self.error,
            poll_seconds=self.poll, **self.last)


# --- 1. cross-exchange spread -------------------------------------------------------------

class SpreadBot(PaperBot):
    folder = "arb_paper_spread"
    label = "Арбитраж между биржами"
    SCHEMA = {
        "trade_usd": {"label": "Сумма одной сделки, $", "type": "float", "min": 10, "max": 10000, "step": 10},
        "min_net_pct": {"label": "Сделка, если чистая выгода ≥ %", "type": "float", "min": 0, "max": 3, "step": 0.01},
        "fee_pct": {"label": "Комиссия биржи за сделку, %", "type": "float", "min": 0, "max": 0.5, "step": 0.01},
        "rebalance_pct": {"label": "Перевозка монет между биржами, % от сделки", "type": "float", "min": 0,
                          "max": 1, "step": 0.01},
        "max_gap_pct": {"label": "Разница больше % — ловушка, не торгуем", "type": "float", "min": 0.5, "max": 20,
                        "step": 0.5},
    }
    DEFAULTS = {"trade_usd": 100.0, "min_net_pct": 0.05, "fee_pct": 0.1, "rebalance_pct": 0.05,
                "max_gap_pct": 3.0, "capital_usd": 1000.0}
    COOLDOWN = 300  # a gap that persists for minutes is one opportunity, not one per check

    def step(self, quotes: dict, errors: dict, now: float | None = None) -> None:
        now = now or time.time()
        st = self.settings()
        s = st[0]
        m = self._mem()
        m.setdefault("realized", 0.0)
        m.setdefault("trades", 0)
        m.setdefault("last_trade", {})
        paused = bool(read_control(self.files.control).get("paused"))
        cost = 2 * s["fee_pct"] + s["rebalance_pct"]

        best = []
        coins = sorted({c for _, c in quotes})
        for coin in coins:
            live = [(ex, q) for (ex, c), q in quotes.items() if c == coin and q["bid"] > 0 and q["ask"] > 0
                    and (not q.get("ts") or now - q["ts"] < STALE_SECONDS)]
            pick = None
            for ea, qa in live:
                for eb, qb in live:
                    if ea != eb and (pick is None or qb["bid"] / qa["ask"] > pick[3]["bid"] / pick[1]["ask"]):
                        pick = (ea, qa, eb, qb)
            if not pick:
                continue
            ea, qa, eb, qb = pick
            gross = (qb["bid"] / qa["ask"] - 1) * 100
            best.append({"coin": coin, "buy": ea, "sell": eb, "ask": qa["ask"], "bid": qb["bid"], "gross": gross,
                         "net": gross - cost, "trap": gross > s["max_gap_pct"],
                         "size": min([v for v in (qa["ask_size"] and qa["ask_size"] * qa["ask"],
                                                  qb["bid_size"] and qb["bid_size"] * qb["bid"]) if v] or [None])
                         if (qa["ask_size"] or qb["bid_size"]) else None})
        best.sort(key=lambda o: -o["net"])

        done = []
        for o in best:
            if paused or o["trap"] or o["net"] < s["min_net_pct"]:
                continue
            key = f"{o['coin']} {o['buy']}>{o['sell']}"
            if now - m["last_trade"].get(key, 0) < self.COOLDOWN:
                continue
            usd = min(s["trade_usd"], o["size"]) if o["size"] else s["trade_usd"]
            if usd < 5:
                continue
            pnl = round(usd * o["net"] / 100, 4)
            m["realized"] = round(m["realized"] + pnl, 4)
            m["trades"] += 1
            m["last_trade"][key] = now
            self.journal({"action": "arb", "instrument": o["coin"], "detail": f"{title(o['buy'])} → {title(o['sell'])}",
                          "amount": round(usd, 2), "price": o["ask"], "price2": o["bid"],
                          "gross_pct": round(o["gross"], 3), "pnl_usd": pnl,
                          "reason": f"купили на {title(o['buy'])} по {o['ask']}, продали на {title(o['sell'])} по {o['bid']}"})
            done.append(f"{o['coin']} +${pnl:.2f}")

        if paused:
            reason = "пауза — новые сделки отключены"
        elif done:
            reason = "сделки: " + ", ".join(done)
        elif best:
            top = best[0]
            reason = (f"выгодных разниц нет: лучшая {top['coin']} {title(top['buy'])}→{title(top['sell'])} "
                      f"{top['gross']:+.3f}%, после комиссий {top['net']:+.3f}%")
        else:
            reason = "нет цен ни с одной биржи"
        equity = s["capital_usd"] + m["realized"]
        self.error = None if quotes else "ни одна биржа не ответила: " + "; ".join(f"{title(k)}: {v}" for k, v in errors.items())
        self._save(m)
        self.files.append(self.files.equity, {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                              "equity": round(equity, 4)})
        self.last = {
            "action": "arb" if done else "hold", "reason": reason, "checked_at": iso(now), "equity": equity,
            "realized_usd": m["realized"], "trade_count": m["trades"],
            "feed_errors": {title(k): v for k, v in errors.items()},
            "exchanges_ok": sorted({title(ex) for ex, _ in quotes}),
            "opportunities": [{"what": o["coin"], "where": f"купить {title(o['buy'])} → продать {title(o['sell'])}",
                               "gross": round(o["gross"], 3), "net": round(o["net"], 3),
                               "note": "похоже на ловушку (закрыт ввод/вывод или другой токен)" if o["trap"]
                               else "выгодно" if o["net"] >= s["min_net_pct"] else ""}
                              for o in best[:10]],
            "metric_label": "Разница", "net_label": "После комиссий",
        }
        log.info("spread: %s", reason)
        self.write_status(st, {"signal": "покупка на одной бирже и продажа на другой",
                               "exchanges": [title(e) for e in sorted({ex for ex, _ in quotes} | set(errors))]})


# --- 2. funding-rate carry ----------------------------------------------------------------

class FundingBot(PaperBot):
    folder = "arb_paper_funding"
    label = "Ставка финансирования"
    SCHEMA = {
        "capital_usd": {"label": "Капитал (бумажный), $", "type": "float", "min": 100, "max": 100000, "step": 100},
        "max_positions": {"label": "Позиций одновременно", "type": "int", "min": 1, "max": 5, "step": 1},
        "enter_apr": {"label": "Входить, если ставка ≥ % годовых", "type": "float", "min": 1, "max": 300, "step": 1},
        "exit_apr": {"label": "Выходить, если ставка < % годовых", "type": "float", "min": -50, "max": 100,
                     "step": 1},
        "spot_fee_pct": {"label": "Комиссия спот, %", "type": "float", "min": 0, "max": 0.5, "step": 0.01},
        "perp_fee_pct": {"label": "Комиссия фьючерс, %", "type": "float", "min": 0, "max": 0.5, "step": 0.01},
    }
    RULES = [["exit_apr", "<", "enter_apr", "Ставка выхода должна быть меньше ставки входа"]]
    DEFAULTS = {"capital_usd": 1000.0, "max_positions": 2, "enter_apr": 20.0, "exit_apr": 5.0,
                "spot_fee_pct": 0.1, "perp_fee_pct": 0.06}
    LOW_CHECKS = 3  # the rate must stay low for this many checks in a row before we leave

    def step(self, rates: dict, errors: dict, now: float | None = None) -> None:
        now = now or time.time()
        st = self.settings()
        s = st[0]
        m = self._mem()
        m.setdefault("realized", 0.0)
        m.setdefault("positions", [])
        control = read_control(self.files.control)
        paused = bool(control.get("paused"))
        fee = (s["spot_fee_pct"] + s["perp_fee_pct"]) / 100
        notes = []

        def book(pnl: float) -> None:
            m["realized"] = round(m["realized"] + pnl, 4)

        # 1. collect funding and decide exits
        for p in list(m["positions"]):
            r = rates.get((p["exchange"], p["coin"]))
            while p.get("next_ts") and now >= p["next_ts"]:
                got = round(p["notional"] * p["rate"], 4)
                p["collected"] = round(p.get("collected", 0) + got, 4)
                book(got)
                self.journal({"action": "funding", "instrument": p["coin"], "detail": title(p["exchange"]),
                              "amount": p["notional"], "price": p["rate"], "pnl_usd": got,
                              "reason": f"выплата финансирования {p['rate'] * 100:+.4f}%"})
                p["next_ts"] += p.get("interval_h", 8) * 3600
            if r:
                p["rate"], p["interval_h"] = r["rate"], r["interval_h"]
                if r.get("next_ts") and r["next_ts"] > now:
                    p["next_ts"] = r["next_ts"]
                p["apr"] = apr(r["rate"], r["interval_h"])
                p["low"] = p.get("low", 0) + 1 if p["apr"] < s["exit_apr"] else 0
            why = ("закрыто из панели" if control.get("close_requested")
                   else f"ставка упала до {p['apr']:.1f}% годовых" if p.get("low", 0) >= self.LOW_CHECKS else "")
            if why:
                cost = round(-p["notional"] * fee, 4)
                book(cost)
                m["positions"].remove(p)
                total = round(p.get("collected", 0) - p["notional"] * fee * 2, 4)
                self.journal({"action": "close", "instrument": p["coin"], "detail": title(p["exchange"]),
                              "amount": p["notional"], "price": p["rate"], "pnl_usd": cost,
                              "reason": f"{why}; итог позиции ${total:+.2f}"})
                notes.append(f"закрыли {p['coin']} на {title(p['exchange'])}: {why}")
        if control.get("close_requested"):
            update_control(self.files.control, paused=True, close_requested=False)
            paused = True

        # 2. rank and enter
        ranked = sorted(({"exchange": ex, "coin": c, **r, "apr": apr(r["rate"], r["interval_h"])}
                         for (ex, c), r in rates.items()), key=lambda o: -o["apr"])
        held = {p["coin"] for p in m["positions"]}
        slot = s["capital_usd"] / s["max_positions"]
        for o in ranked:
            if paused or len(m["positions"]) >= s["max_positions"] or o["apr"] < s["enter_apr"]:
                break
            if o["coin"] in held:
                continue
            notional = round(slot / 2, 2)  # half buys the coin, half is margin for the short
            cost = round(-notional * fee, 4)
            book(cost)
            m["positions"].append({"exchange": o["exchange"], "coin": o["coin"], "notional": notional,
                                   "rate": o["rate"], "interval_h": o["interval_h"], "apr": o["apr"],
                                   "next_ts": o.get("next_ts") or now + o["interval_h"] * 3600,
                                   "opened_at": now, "collected": 0.0, "low": 0})
            held.add(o["coin"])
            self.journal({"action": "open", "instrument": o["coin"], "detail": title(o["exchange"]),
                          "amount": notional, "price": o["rate"], "pnl_usd": cost,
                          "reason": f"купили {o['coin']} и открыли шорт: ставка {o['apr']:.1f}% годовых"})
            notes.append(f"вошли в {o['coin']} на {title(o['exchange'])} ({o['apr']:.0f}% годовых)")

        if paused and not notes:
            reason = "пауза — новые входы отключены"
        elif notes:
            reason = "; ".join(notes)
        elif m["positions"]:
            reason = "держим: " + ", ".join(f"{p['coin']} {p['apr']:.1f}% годовых" for p in m["positions"])
        elif ranked:
            top = ranked[0]
            reason = (f"ждём: лучшая ставка {top['coin']} на {title(top['exchange'])} {top['apr']:.1f}% годовых, "
                      f"входим от {s['enter_apr']:.0f}%")
        else:
            reason = "нет данных о ставках ни с одной биржи"
        self.error = None if rates else "ни одна биржа не ответила: " + "; ".join(f"{title(k)}: {v}" for k, v in errors.items())
        equity = s["capital_usd"] + m["realized"]
        self._save(m)
        self.files.append(self.files.equity, {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                              "equity": round(equity, 4)})
        self.last = {
            "action": "hold", "reason": reason, "checked_at": iso(now), "equity": equity, "realized_usd": m["realized"],
            "feed_errors": {title(k): v for k, v in errors.items()},
            "exchanges_ok": sorted({title(ex) for ex, _ in rates}),
            "positions": [{"instrument": f"{p['coin']} · {title(p['exchange'])}", "notional": p["notional"],
                           "apr": round(p["apr"], 1), "collected": p.get("collected", 0.0),
                           "next_ts": iso(p.get("next_ts"))} for p in m["positions"]],
            "opportunities": [{"what": o["coin"], "where": title(o["exchange"]), "gross": round(o["apr"], 1),
                               "net": round(o["rate"] * 100, 4),
                               "note": "держим" if (o["exchange"], o["coin"]) in
                               {(p["exchange"], p["coin"]) for p in m["positions"]}
                               else "подходит" if o["apr"] >= s["enter_apr"] else ""}
                              for o in ranked[:10]],
            "metric_label": "% годовых", "net_label": "Ставка за период, %",
        }
        log.info("funding: %s", reason)
        self.write_status(st, {"signal": "покупка монеты + шорт фьючерса, доход — ставка финансирования",
                               "exchanges": [title(e) for e in sorted({ex for ex, _ in rates} | set(errors))]})


# --- loop -------------------------------------------------------------------------------

def run_arb(feeds: Feeds, coins: list[str], state_dir: str | Path = "paper_state",
            spread_seconds: int = 60, funding_seconds: int = 300) -> None:
    spread, funding = SpreadBot(state_dir, spread_seconds), FundingBot(state_dir, spread_seconds)
    log.info("paper arbitrage on %s for %s", ", ".join(feeds.names), ", ".join(coins))
    next_funding = 0.0
    while True:
        started = time.monotonic()
        try:
            spread.step(*feeds.spot(coins))
        except Exception as exc:  # keep the loop alive; the panel shows the error
            log.exception("spread step failed")
            spread.error = _short(exc)
            spread.write_status(spread.settings(), {})
        if started >= next_funding or read_control(funding.files.control).get("close_requested"):
            try:
                funding.step(*feeds.funding(coins))
            except Exception as exc:
                log.exception("funding step failed")
                funding.error = _short(exc)
                funding.write_status(funding.settings(), {})
            next_funding = started + funding_seconds
        else:
            funding.write_status(funding.settings(), {})  # keep "alive" fresh between rate checks
        time.sleep(max(1.0, spread_seconds - (time.monotonic() - started)))
