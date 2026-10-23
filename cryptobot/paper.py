"""Paper trader: runs the strategy on live candles and simulates fills locally.

With `testnet=True` orders are also sent to the exchange's testnet (fake money,
requires testnet API keys). This module never trades on a real-money account.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from cryptobot.backtest import BacktestConfig, position_size
from cryptobot.strategy import StrategyParams, generate_signals

log = logging.getLogger(__name__)


@dataclass
class PaperState:
    cash: float
    qty: float = 0.0
    entry_price: float = 0.0
    stop: float = 0.0
    take_profit: float = 0.0
    last_candle: str = ""  # ISO timestamp of the last processed candle

    @classmethod
    def load(cls, path: Path, initial_capital: float) -> "PaperState":
        if path.exists():
            return cls(**json.loads(path.read_text()))
        return cls(cash=initial_capital)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))


def step(state: PaperState, df: pd.DataFrame, params: StrategyParams, cfg: BacktestConfig) -> list[dict]:
    """Process the newest closed candle in `df`; return the simulated orders it produced."""
    sig = generate_signals(df, params)
    bar = sig.iloc[-1]
    ts = sig.index[-1].isoformat()
    if ts == state.last_candle:
        return []
    state.last_candle = ts
    orders: list[dict] = []

    def sell(raw_price: float, reason: str) -> None:
        price = raw_price * (1 - cfg.slippage)
        proceeds = state.qty * price * (1 - cfg.fee_rate)
        pnl = proceeds - state.qty * state.entry_price * (1 + cfg.fee_rate)
        orders.append({"time": ts, "side": "sell", "qty": state.qty, "price": price, "reason": reason, "pnl": pnl})
        state.cash += proceeds
        state.qty = state.entry_price = state.stop = state.take_profit = 0.0

    if state.qty > 0:
        if bar["low"] <= state.stop:
            sell(min(bar["open"], state.stop), "stop")
        elif state.take_profit and bar["high"] >= state.take_profit:
            sell(max(bar["open"], state.take_profit), "take_profit")
        elif bar["exit"]:
            sell(bar["close"], "signal")
    elif bar["entry"]:
        price = bar["close"] * (1 + cfg.slippage)
        stop = price - params.stop_atr * bar["atr"]
        qty = position_size(state.cash, price, stop, cfg)
        if qty > 0:
            state.cash -= qty * price * (1 + cfg.fee_rate)
            state.qty, state.entry_price, state.stop = qty, price, stop
            state.take_profit = price + params.take_profit_atr * bar["atr"] if params.take_profit_atr else 0.0
            orders.append({"time": ts, "side": "buy", "qty": qty, "price": price, "reason": "signal", "pnl": 0.0})
    return orders


def run_paper(
    symbol: str,
    timeframe: str,
    params: StrategyParams,
    cfg: BacktestConfig,
    exchange_id: str = "binance",
    state_dir: str | Path = "paper_state",
    testnet: bool = False,
    api_key: str = "",
    api_secret: str = "",
    poll_seconds: int = 60,
) -> None:
    import ccxt

    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True, "apiKey": api_key, "secret": api_secret})
    if testnet:
        exchange.set_sandbox_mode(True)

    state_dir = Path(state_dir)
    state_path = state_dir / f"{symbol.replace('/', '')}_{timeframe}.json"
    trades_path = state_dir / f"{symbol.replace('/', '')}_{timeframe}_trades.csv"
    state = PaperState.load(state_path, cfg.initial_capital)
    history = params.slow_ema * 5

    log.info("Paper trading %s %s on %s (testnet=%s), cash=%.2f", symbol, timeframe, exchange_id, testnet, state.cash)
    while True:
        try:
            raw = exchange.fetch_ohlcv(symbol, timeframe, limit=history + 1)
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df.index = pd.to_datetime(df.pop("timestamp"), unit="ms", utc=True)
            df = df.iloc[:-1]  # drop the still-forming candle

            for order in step(state, df, params, cfg):
                log.info("%s %.6f @ %.2f (%s) pnl=%.2f", order["side"].upper(), order["qty"], order["price"], order["reason"], order["pnl"])
                if testnet:
                    side = exchange.create_market_buy_order if order["side"] == "buy" else exchange.create_market_sell_order
                    side(symbol, exchange.amount_to_precision(symbol, order["qty"]))
                pd.DataFrame([order]).to_csv(trades_path, mode="a", header=not trades_path.exists(), index=False)
            state.save(state_path)

            equity = state.cash + state.qty * df["close"].iloc[-1]
            log.info("candle %s close=%.2f equity=%.2f", df.index[-1], df["close"].iloc[-1], equity)
        except ccxt.NetworkError as exc:
            log.warning("network error, retrying: %s", exc)
        time.sleep(poll_seconds)
