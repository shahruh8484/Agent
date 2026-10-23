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
            raise EvedexError(f"bridge returned nothing: {proc.stderr.strip()[-500:]}")
        result = json.loads(lines[-1])
        if proc.returncode != 0 or "error" in result:
            raise EvedexError(result.get("error", proc.stderr.strip()[-500:]))
        return result

    def instruments(self) -> list[dict]:
        return self.call("instruments")["instruments"]

    def state(self, instrument: str) -> dict:
        return self.call("state", instrument=instrument)

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


@dataclass
class Action:
    kind: str  # "open", "close" or "hold"
    reason: str
    cash: float = 0.0
    stop: float = 0.0
    take_profit: float | None = None


def decide(bar: pd.Series, state: dict, params: StrategyParams, risk: BacktestConfig, live: LiveConfig) -> Action:
    """Pure decision for one closed candle, given the exchange state."""
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


class BotFiles:
    """Files a running bot shares with the control panel (cryptobot/panel.py)."""

    def __init__(self, state_dir: str | Path, env: str, instrument: str, timeframe: str):
        self.dir = Path(state_dir) / f"evedex_{env}_{instrument.replace(':', '_')}_{timeframe}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.status = self.dir / "status.json"
        self.control = self.dir / "control.json"
        self.trades = self.dir / "trades.csv"
        self.equity = self.dir / "equity.csv"
        self.last_candle = self.dir / "last_candle"

    def paused(self) -> bool:
        try:
            return bool(json.loads(self.control.read_text()).get("paused"))
        except (FileNotFoundError, json.JSONDecodeError):
            return False

    def write_status(self, **fields) -> None:
        tmp = self.status.with_suffix(".tmp")
        tmp.write_text(json.dumps({"updated_at": time.time(), **fields}, default=str))
        tmp.replace(self.status)  # atomic, so the panel never reads half a file

    def append(self, path: Path, row: dict) -> None:
        pd.DataFrame([row]).to_csv(path, mode="a", header=not path.exists(), index=False)


def run_live(
    client: EvedexClient,
    live: LiveConfig,
    params: StrategyParams,
    risk: BacktestConfig,
    signal_symbol: str = "BTC/USDT",
    signal_exchange: str = "binance",
    timeframe: str = "1h",
    poll_seconds: int = 60,
    state_dir: str | Path = "paper_state",
) -> None:
    import ccxt

    feed = getattr(ccxt, signal_exchange)({"enableRateLimit": True})
    files = BotFiles(state_dir, client.env, live.instrument, timeframe)
    config = {
        "env": client.env, "instrument": live.instrument, "timeframe": timeframe,
        "signal": f"{signal_exchange} {signal_symbol}", "leverage": live.leverage, "max_usd": live.max_usd,
        "risk_per_trade": risk.risk_per_trade, "fast_ema": params.fast_ema, "slow_ema": params.slow_ema,
        "stop_atr": params.stop_atr, "take_profit_atr": params.take_profit_atr,
    }
    last: dict = {}

    log.info("EVEDEX %s [%s] on %s %s %s signals, leverage=%sx, max=$%s",
             live.instrument, client.env.upper(), signal_exchange, signal_symbol, timeframe,
             live.leverage, live.max_usd)
    log.info("start state: %s", client.state(live.instrument))

    while True:
        error = None
        try:
            df = fetch_closed_candles(feed, signal_symbol, timeframe, params.slow_ema * 5)
            candle = df.index[-1].isoformat()
            if not (files.last_candle.exists() and files.last_candle.read_text() == candle):
                bar = generate_signals(df, params).iloc[-1]
                state = client.state(live.instrument)
                action = decide(bar, state, params, risk, live)
                if action.kind == "open" and files.paused():
                    action = Action("hold", "paused from the panel; not opening new positions")
                log.info("candle %s close=%.2f mark=%.2f balance=%.2f -> %s (%s)",
                         candle, bar["close"], state["markPrice"], state["availableBalance"],
                         action.kind, action.reason)

                if action.kind == "open":
                    result = client.open_long(live.instrument, action.cash, live.leverage, action.stop, action.take_profit)
                    log.info("opened qty=%s avg=%s stop=%.2f tp=%s", result["position"]["quantity"],
                             result["position"]["avgPrice"], action.stop, action.take_profit)
                elif action.kind == "close":
                    log.info("closed: %s", client.close(live.instrument).get("closed"))

                if action.kind != "hold":
                    files.append(files.trades, {
                        "time": candle, "env": client.env, "action": action.kind, "reason": action.reason,
                        "cash": action.cash, "stop": action.stop, "take_profit": action.take_profit,
                        "mark": state["markPrice"],
                    })
                    state = client.state(live.instrument)
                position = state.get("position") or {}
                files.append(files.equity, {
                    "time": candle, "balance": state["availableBalance"],
                    "unrealized_pnl": position.get("unRealizedPnL", 0.0), "mark": state["markPrice"],
                })
                last = {
                    "candle": candle, "close": float(bar["close"]), "ema_fast": float(bar["ema_fast"]),
                    "ema_slow": float(bar["ema_slow"]), "rsi": float(bar["rsi"]), "atr": float(bar["atr"]),
                    "action": action.kind, "reason": action.reason, "state": state,
                }
                files.last_candle.write_text(candle)
        except (EvedexError, subprocess.TimeoutExpired, ccxt.NetworkError) as exc:
            # The candle stays unprocessed, so the next poll retries it.
            log.warning("retrying after error: %s", exc)
            error = str(exc)
        files.write_status(config=config, paused=files.paused(), error=error, poll_seconds=poll_seconds, **last)
        time.sleep(poll_seconds)
