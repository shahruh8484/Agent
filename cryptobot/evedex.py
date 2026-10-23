"""Live trading on EVEDEX perpetuals through the official Node SDK (see evedex_bridge/).

EVEDEX's API has no candle endpoint, so signals come from the same pair's candles
on a public CEX (Binance by default); perp prices track the index closely.
Stop-loss and take-profit are placed on EVEDEX itself, so they hold even if the bot stops.
The exchange's position is the source of truth: nothing is cached locally except
the last processed candle.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from cryptobot.backtest import BacktestConfig
from cryptobot.strategy import StrategyParams, generate_signals

log = logging.getLogger(__name__)
BRIDGE = Path(__file__).parent / "evedex_bridge" / "bridge.js"


class EvedexError(RuntimeError):
    pass


HINTS = {
    "user_not_found_error": "биржа не знает пользователя с этим ключом: этот кошелёк ни разу не входил на EVEDEX "
                            "(аккаунт создан через email). Войдите на сайт биржи этим кошельком через MetaMask.",
}


def _clean(text) -> str:
    """Bridge output is shown on panels: never let a session token through, and explain known errors."""
    text = re.sub(r"Bearer\s+[\w.-]+", "Bearer ***", str(text))[:500]
    for label, hint in HINTS.items():
        if label in text:
            return f"{text} — {hint}"
    return text


class EvedexClient:
    def __init__(self, env: str = "demo", timeout: int = 60):
        if env not in ("demo", "prod"):
            raise ValueError(f"env must be demo or prod, got {env}")
        self.env = env
        self.timeout = timeout

    def call(self, command: str, **params) -> dict:
        proc = subprocess.run(
            ["node", str(BRIDGE), command, json.dumps(params)],
            capture_output=True, text=True, timeout=self.timeout,
            env={**os.environ, "EVEDEX_ENV": self.env},
        )
        lines = proc.stdout.strip().splitlines()
        if not lines:
            raise EvedexError(_clean(f"bridge returned nothing: {proc.stderr.strip()[-500:]}"))
        result = json.loads(lines[-1])
        if proc.returncode != 0 or "error" in result:
            raise EvedexError(_clean(result.get("error", proc.stderr.strip()[-500:])))
        return result

    def instruments(self) -> list[dict]:
        return self.call("instruments")["instruments"]

    def state(self, instrument: str) -> dict:
        return self.call("state", instrument=instrument)

    def states(self, instruments: list[str]) -> dict[str, dict]:
        return self.call("states", instruments=instruments)["states"]

    def open_long(self, instrument: str, cash: float, leverage: int, stop: float, take_profit: float | None) -> dict:
        return self.call(
            "open_long", instrument=instrument, cash=round(cash, 2), leverage=leverage,
            stop=stop, takeProfit=take_profit,
        )

    def close(self, instrument: str) -> dict:
        return self.call("close", instrument=instrument)


@dataclass
class LiveConfig:
    instrument: str = "BTCUSDT:DEMO"
    leverage: int = 1
    max_usd: float = 100.0  # hard cap on position notional, whatever the sizing says
    max_positions: int = 2  # open longs across all coins of one bot; they share the balance


@dataclass
class Market:
    instrument: str  # EVEDEX name, e.g. XRPUSD
    signal_symbol: str  # candles for signals, e.g. XRP/USDT


def parse_markets(spec: str) -> list[Market]:
    """"XRPUSD, LINKUSD=LINK/USDT" -> markets; the signal pair defaults to <coin>/USDT."""
    from cryptobot.news import base_coin

    markets = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        instrument, _, symbol = part.partition("=")
        instrument = instrument.strip()
        markets.append(Market(instrument, symbol.strip() or f"{base_coin(instrument)}/USDT"))
    if len({m.instrument for m in markets}) != len(markets):
        raise ValueError(f"duplicate instrument in {spec!r}")
    return markets


@dataclass
class Action:
    kind: str  # "open", "close" or "hold"
    reason: str
    cash: float = 0.0
    stop: float = 0.0
    take_profit: float | None = None


def decide(bar: pd.Series, state: dict, params: StrategyParams, risk: BacktestConfig, live: LiveConfig,
           open_positions: int = 0) -> Action:
    """Pure decision for one closed candle, given the exchange state and longs open on other coins."""
    position = state.get("position")
    if position:
        if position.get("side") != "BUY":
            return Action("hold", "short position not opened by this bot; leaving it alone")
        if bar["exit"]:
            return Action("close", "EMA cross down")
        return Action("hold", "in position")

    if state.get("tpsl"):
        return Action("close", "stale TP/SL without a position")
    if not bar["entry"]:
        return Action("hold", "no signal")
    if open_positions >= live.max_positions:
        return Action("hold", "position limit reached")

    price = float(state["markPrice"])
    balance = float(state["availableBalance"])
    stop = price - params.stop_atr * bar["atr"]
    if stop <= 0:
        return Action("hold", "stop would be below zero")
    # Losing `risk_per_trade` of the balance at the stop, capped by leverage and max_usd.
    qty = balance * risk.risk_per_trade / (price - stop)
    cash = min(qty * price, balance * live.leverage, live.max_usd)
    if cash < 1:
        return Action("hold", "position size below $1")
    tp = price + params.take_profit_atr * bar["atr"] if params.take_profit_atr else None
    return Action("open", "EMA cross up", cash=cash, stop=stop, take_profit=tp)


def fetch_closed_candles(feed, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    raw = feed.fetch_ohlcv(symbol, timeframe, limit=limit + 1)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df.pop("timestamp"), unit="ms", utc=True)
    return df.iloc[:-1]  # drop the still-forming candle


def read_control(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def update_control(path: Path, **fields) -> None:
    """Merge fields into control.json: {"paused": bool, "close_requested": bool}."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({**read_control(path), **fields}))
    tmp.replace(path)


class BotFiles:
    """Files a running bot shares with control panels (cryptobot/panel.py, the pushpannel tab).

    status.json, trades.csv and equity.csv are written by the bot; control.json by panels.
    """

    def __init__(self, state_dir: str | Path, env: str, instrument: str, timeframe: str):
        self.dir = Path(state_dir) / f"evedex_{env}_{instrument.replace(':', '_')}_{timeframe}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.status = self.dir / "status.json"
        self.control = self.dir / "control.json"
        self.trades = self.dir / "trades.csv"
        self.equity = self.dir / "equity.csv"
        self.last_candle = self.dir / "last_candle"
        self.settings = self.dir / "settings.json"  # overrides written by panels

    def paused(self) -> bool:
        return bool(read_control(self.control).get("paused"))

    def close_requested(self) -> bool:
        return bool(read_control(self.control).get("close_requested"))

    def write_status(self, **fields) -> None:
        tmp = self.status.with_suffix(".tmp")
        tmp.write_text(json.dumps({"updated_at": time.time(), **fields}, default=str))
        tmp.replace(self.status)  # atomic, so the panel never reads half a file

    def append(self, path: Path, row: dict) -> None:
        if path.exists():
            with path.open() as f:
                header = f.readline().strip().split(",")
            if header != list(row):
                # Columns changed between versions: rewrite once so every row lines up again.
                pd.concat([pd.read_csv(path), pd.DataFrame([row])]).to_csv(path, index=False)
                return
        pd.DataFrame([row]).to_csv(path, mode="a", header=not path.exists(), index=False)


STATE_REFRESH_SECONDS = 300


def run_live(
    client: EvedexClient,
    live: LiveConfig,
    params: StrategyParams,
    risk: BacktestConfig,
    markets: list[Market],
    signal_exchange: str = "binance",
    timeframe: str = "1h",
    poll_seconds: int = 60,
    state_dir: str | Path = "paper_state",
    news_cfg=None,
    news_desk=None,
) -> None:
    import ccxt

    from cryptobot.news import NewsConfig, NewsDesk, effect
    from cryptobot.settings import RULES, SCHEMA, base_settings, effective, to_objects

    news_cfg = news_cfg or NewsConfig(mode="off")
    # The desk always runs; each coin's own news mode decides whether its result is used.
    desk = news_desk or NewsDesk(NewsConfig(mode="shadow", feeds=news_cfg.feeds, max_age_hours=news_cfg.max_age_hours))
    feed = getattr(ccxt, signal_exchange)({"enableRateLimit": True})
    names = [m.instrument for m in markets]
    files = {m.instrument: BotFiles(state_dir, client.env, m.instrument, timeframe) for m in markets}
    base = base_settings(live, params, risk, news_cfg)
    config = {"env": client.env, "timeframe": timeframe, "max_positions": live.max_positions, "coins": names,
              "settings_schema": SCHEMA, "settings_rules": RULES, "settings_defaults": base}
    applied: dict[str, dict] = {}

    def settings_for(m: Market):
        """This coin's settings: .env defaults plus valid panel overrides (re-read every poll)."""
        f = files[m.instrument]
        overrides = read_control(f.settings)
        overrides.pop("updated_at", None)
        current, errs = effective(base, overrides)
        if current != applied.get(m.instrument):
            if m.instrument in applied:
                log.info("%s settings now %s", m.instrument, current)
            applied[m.instrument] = current
        lv, pr, rk, mode, block, exit_ = to_objects(current, live)
        return current, errs, sorted(k for k in overrides if k in SCHEMA and not errs), lv, pr, rk, \
            NewsConfig(mode=mode, block_below=block, exit_below=exit_)
    last: dict[str, dict] = {m.instrument: {} for m in markets}
    news: dict = {}

    log.info("EVEDEX %s [%s] on %s %s signals, leverage=%sx, max=$%s, max positions=%s, news=%s",
             ", ".join(f"{m.instrument}<-{m.signal_symbol}" for m in markets), client.env.upper(),
             signal_exchange, timeframe, live.leverage, live.max_usd, live.max_positions, news_cfg.mode)
    try:
        log.info("start state: %s", client.states(names))
    except EvedexError as exc:
        log.warning("EVEDEX not reachable yet, will keep retrying: %s", exc)

    def journal(m: Market, time_: str, action: Action, mark) -> None:
        files[m.instrument].append(files[m.instrument].trades, {
            "time": time_, "env": client.env, "action": action.kind, "reason": action.reason,
            "cash": action.cash, "stop": action.stop, "take_profit": action.take_profit, "mark": mark,
        })

    sett: dict[str, tuple] = {}
    objs: dict[str, tuple] = {}
    states_at = time.monotonic()

    while True:
        errors: dict[str, str] = {}
        try:
            for m in markets:
                f = files[m.instrument]
                if f.close_requested():
                    # Stays requested until the close succeeds, so a failed attempt is retried.
                    closed = client.close(m.instrument).get("closed")
                    update_control(f.control, paused=True, close_requested=False)
                    log.info("%s closed from the panel: %s", m.instrument, closed)
                    if closed:
                        journal(m, pd.Timestamp.now(tz="UTC").isoformat(), Action("close", "closed from the panel"),
                                client.states([m.instrument])[m.instrument]["markPrice"])

            due, peek = {}, {}
            for m in markets:
                cur, serr, over, lv, pr, rk, nc = settings_for(m)
                sett[m.instrument] = (cur, serr, over)
                objs[m.instrument] = (lv, pr, rk, nc)
                try:
                    df = fetch_closed_candles(feed, m.signal_symbol, timeframe, pr.slow_ema * 5)
                except ccxt.BaseError as exc:
                    log.warning("%s: no candles for %s: %s", m.instrument, m.signal_symbol, exc)
                    errors[m.instrument] = f"свечи {m.signal_symbol}: {exc}"
                    continue
                candle = df.index[-1].isoformat()
                lc = files[m.instrument].last_candle
                if not (lc.exists() and lc.read_text() == candle):
                    due[m.instrument] = (m, candle, generate_signals(df, pr).iloc[-1])
                elif "candle" not in last[m.instrument]:
                    # Restarted after this candle was handled: show it, but don't trade on it again.
                    peek[m.instrument] = (m, candle, generate_signals(df, pr).iloc[-1])

            if due:
                watched = [n for n in due if objs[n][3].mode != "off"]
                news = {}
                if watched:
                    try:
                        news = desk.views(watched)
                    except Exception as exc:  # the filter must never stop trading
                        log.warning("news filter failed: %s", exc)
                        news = {}
                states = client.states(names)
                open_longs = sum(1 for st in states.values() if (st.get("position") or {}).get("side") == "BUY")

                for name, (m, candle, bar) in due.items():
                    f, state = files[name], states[name]
                    m_live, m_params, m_risk, m_news = objs[name]
                    position = state.get("position")
                    in_long = (position or {}).get("side") == "BUY"
                    action = decide(bar, state, m_params, m_risk, m_live, open_longs - (1 if in_long else 0))
                    if action.kind == "open" and f.paused():
                        action = Action("hold", "paused from the panel; not opening new positions")

                    view = news.get(name)
                    fx = effect(view, m_news, in_long and action.kind == "hold", action.kind == "open")
                    if fx and m_news.mode == "on":
                        action = (Action("hold", "news negative; entry skipped") if fx == "block"
                                  else Action("close", "news very negative; closing"))
                    log.info("%s candle %s close=%s mark=%s balance=%.2f -> %s (%s)%s", name, candle,
                             bar["close"], state["markPrice"], state["availableBalance"], action.kind, action.reason,
                             f" news={view.score} {fx or ''}" if view else "")

                    if action.kind == "open":
                        result = client.open_long(name, action.cash, m_live.leverage, action.stop, action.take_profit)
                        open_longs += 1
                        log.info("%s opened qty=%s avg=%s stop=%s tp=%s", name, result["position"]["quantity"],
                                 result["position"]["avgPrice"], action.stop, action.take_profit)
                    elif action.kind == "close":
                        if client.close(name).get("closed") and in_long:
                            open_longs -= 1
                    if action.kind != "hold":
                        journal(m, candle, action, state["markPrice"])
                    if view is not None:
                        f.append(f.dir / "news.csv", {
                            "time": candle, "mode": m_news.mode, "score": view.score, "effect": fx,
                            "headlines": len(view.headlines), "summary": view.summary, "error": view.error,
                            "action": action.kind, "close": float(bar["close"]),
                        })
                    last[name] = {
                        "candle": candle, "close": float(bar["close"]), "ema_fast": float(bar["ema_fast"]),
                        "ema_slow": float(bar["ema_slow"]), "rsi": float(bar["rsi"]), "atr": float(bar["atr"]),
                        "action": action.kind, "reason": action.reason,
                        "news": None if view is None else {**view.__dict__, "effect": fx, "mode": m_news.mode},
                    }

                states = client.states(names)
                account = (next(iter(states.values()))["fundingBalance"] if states else 0.0) + sum(
                    float((st.get("position") or {}).get("unRealizedPnL") or 0) for st in states.values())
                for name, (m, candle, _) in due.items():
                    position = states[name].get("position") or {}
                    files[name].append(files[name].equity, {
                        "time": candle, "balance": states[name]["availableBalance"],
                        "unrealized_pnl": position.get("unRealizedPnL", 0.0), "mark": states[name]["markPrice"],
                        "account": account,
                    })
                    files[name].last_candle.write_text(candle)
                for name, st in states.items():
                    last[name]["state"] = st
                states_at = time.monotonic()

            if peek:
                watched = [n for n in peek if objs[n][3].mode != "off"]
                views = {}
                if watched:
                    try:
                        views = desk.views(watched)
                    except Exception as exc:
                        log.warning("news filter failed: %s", exc)
                states = client.states(names)
                states_at = time.monotonic()
                for name, (m, candle, bar) in peek.items():
                    view = views.get(name)
                    last[name] = {
                        "candle": candle, "close": float(bar["close"]), "ema_fast": float(bar["ema_fast"]),
                        "ema_slow": float(bar["ema_slow"]), "rsi": float(bar["rsi"]), "atr": float(bar["atr"]),
                        "action": "hold", "reason": "restarted; waiting for the next candle",
                        "news": None if view is None else {**view.__dict__, "effect": "", "mode": objs[name][3].mode},
                    }
                for name, st in states.items():
                    last[name]["state"] = st
            elif time.monotonic() - states_at > STATE_REFRESH_SECONDS:
                # Keep balance/position current between candles (e.g. a stop hit mid-hour).
                for name, st in client.states(names).items():
                    last[name]["state"] = st
                states_at = time.monotonic()
        except (EvedexError, subprocess.TimeoutExpired, ccxt.NetworkError) as exc:
            # Unprocessed candles stay unprocessed, so the next poll retries them.
            log.warning("retrying after error: %s", exc)
            errors = {name: str(exc) for name in names}
        for m in markets:
            f = files[m.instrument]
            cur, serr, over = sett.get(m.instrument) or (base, [], [])
            f.write_status(
                config={**config, **cur, "risk_per_trade": cur["risk_pct"] / 100, "instrument": m.instrument,
                        "signal": f"{signal_exchange} {m.signal_symbol}", "settings_overrides": over,
                        "settings_error": "; ".join(serr)},
                paused=f.paused(), error=errors.get(m.instrument), poll_seconds=poll_seconds, **last[m.instrument])
        time.sleep(poll_seconds)
