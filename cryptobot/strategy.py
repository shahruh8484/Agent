"""Trend-following strategy: EMA crossover with an RSI filter and ATR-based exits."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class StrategyParams:
    fast_ema: int = 20
    slow_ema: int = 50
    rsi_period: int = 14
    rsi_max_entry: float = 70.0  # don't buy into an overbought market
    atr_period: int = 14
    stop_atr: float = 2.0  # stop-loss distance in ATRs
    take_profit_atr: float = 4.0  # take-profit distance in ATRs (0 disables)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    return (100 - 100 / (1 + rs)).fillna(100.0)


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift()
    true_range = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def generate_signals(df: pd.DataFrame, params: StrategyParams) -> pd.DataFrame:
    """Add indicator columns plus `entry`/`exit` booleans, known at each candle's close."""
    out = df.copy()
    out["ema_fast"] = ema(out["close"], params.fast_ema)
    out["ema_slow"] = ema(out["close"], params.slow_ema)
    out["rsi"] = rsi(out["close"], params.rsi_period)
    out["atr"] = atr(out, params.atr_period)

    above = out["ema_fast"] > out["ema_slow"]
    crossed_up = above & ~above.shift(fill_value=False)
    crossed_down = ~above & above.shift(fill_value=False)

    warmed_up = pd.Series(range(len(out)), index=out.index) >= params.slow_ema
    out["entry"] = crossed_up & (out["rsi"] < params.rsi_max_entry) & warmed_up
    out["exit"] = crossed_down
    return out
