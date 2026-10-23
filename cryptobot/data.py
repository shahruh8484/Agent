"""OHLCV loading via ccxt with a local CSV cache, plus a synthetic generator for tests."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]


def fetch_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    days: int = 365,
    exchange_id: str = "binance",
    cache_dir: str | Path = "data_cache",
) -> pd.DataFrame:
    """Download candles from a public exchange endpoint (no API key needed)."""
    import ccxt

    cache = Path(cache_dir) / f"{exchange_id}_{symbol.replace('/', '')}_{timeframe}_{days}d.csv"
    if cache.exists() and time.time() - cache.stat().st_mtime < 3600:
        return load_csv(cache)

    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    since = exchange.milliseconds() - days * 24 * 60 * 60 * 1000
    rows: list[list] = []
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        since = batch[-1][0] + 1
        if len(batch) < 1000:
            break

    df = pd.DataFrame(rows, columns=["timestamp", *COLUMNS])
    df = df.drop_duplicates("timestamp")
    df.index = pd.to_datetime(df.pop("timestamp"), unit="ms", utc=True)
    # The last candle is still forming; trading on it would be lookahead.
    df = df.iloc[:-1]
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache)
    return df


def load_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return df[COLUMNS].astype(float)


def synthetic_ohlcv(n: int = 5000, seed: int = 42, start_price: float = 30000.0) -> pd.DataFrame:
    """Random-walk candles with regime changes, for tests and offline demos."""
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.normal(0, 0.0005, n // 200 + 1), 200)[:n]
    returns = drift + rng.normal(0, 0.006, n)
    close = start_price * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[start_price], close[:-1]])
    spread = np.abs(rng.normal(0, 0.005, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.uniform(100, 1000, n)
    index = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=index
    )
