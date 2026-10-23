"""Long-only spot backtester with fees, slippage, risk-based sizing and intrabar stops.

Signals are taken at a candle's close and filled at the next candle's open,
so the backtest never trades on information it wouldn't have had live.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cryptobot.strategy import StrategyParams, generate_signals


@dataclass
class BacktestConfig:
    initial_capital: float = 1000.0
    risk_per_trade: float = 0.01  # fraction of equity lost if the stop is hit
    fee_rate: float = 0.001  # 0.1% per side (Binance spot taker)
    slippage: float = 0.0005  # 0.05% worse fill on every order


@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry_price: float
    qty: float
    stop: float
    take_profit: float | None
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    reason: str = ""
    pnl: float = 0.0


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: pd.Series
    stats: dict = field(default_factory=dict)


def position_size(equity: float, entry: float, stop: float, cfg: BacktestConfig) -> float:
    """Quantity such that hitting the stop loses `risk_per_trade` of equity; never uses leverage."""
    risk_per_unit = entry - stop
    if risk_per_unit <= 0:
        return 0.0
    qty = equity * cfg.risk_per_trade / risk_per_unit
    max_qty = equity / (entry * (1 + cfg.fee_rate))
    return min(qty, max_qty)


def run_backtest(
    df: pd.DataFrame,
    params: StrategyParams | None = None,
    cfg: BacktestConfig | None = None,
) -> BacktestResult:
    params = params or StrategyParams()
    cfg = cfg or BacktestConfig()
    sig = generate_signals(df, params)

    cash = cfg.initial_capital
    trade: Trade | None = None
    trades: list[Trade] = []
    equity_curve = np.empty(len(sig))

    opens = sig["open"].to_numpy()
    highs = sig["high"].to_numpy()
    lows = sig["low"].to_numpy()
    closes = sig["close"].to_numpy()
    atrs = sig["atr"].to_numpy()
    entries = sig["entry"].to_numpy()
    exits = sig["exit"].to_numpy()
    times = sig.index

    def close_trade(i: int, raw_price: float, reason: str) -> None:
        nonlocal cash, trade
        price = raw_price * (1 - cfg.slippage)
        cash += trade.qty * price * (1 - cfg.fee_rate)
        trade.exit_time, trade.exit_price, trade.reason = times[i], price, reason
        trade.pnl = trade.qty * price * (1 - cfg.fee_rate) - trade.qty * trade.entry_price * (
            1 + cfg.fee_rate
        )
        trades.append(trade)
        trade = None

    for i in range(len(sig)):
        if i > 0:
            # Orders decided at the previous close fill at this candle's open.
            if trade is not None and exits[i - 1]:
                close_trade(i, opens[i], "signal")
            elif trade is None and entries[i - 1]:
                price = opens[i] * (1 + cfg.slippage)
                stop = price - params.stop_atr * atrs[i - 1]
                tp = price + params.take_profit_atr * atrs[i - 1] if params.take_profit_atr else None
                qty = position_size(cash, price, stop, cfg)
                if qty > 0:
                    cash -= qty * price * (1 + cfg.fee_rate)
                    trade = Trade(times[i], price, qty, stop, tp)

        if trade is not None:
            # Assume the worst when both levels sit inside one candle: stop first.
            if lows[i] <= trade.stop:
                close_trade(i, min(opens[i], trade.stop), "stop")
            elif trade.take_profit is not None and highs[i] >= trade.take_profit:
                close_trade(i, max(opens[i], trade.take_profit), "take_profit")

        equity_curve[i] = cash + (trade.qty * closes[i] if trade else 0.0)

    if trade is not None:
        close_trade(len(sig) - 1, closes[-1], "end_of_data")
        equity_curve[-1] = cash

    equity = pd.Series(equity_curve, index=times, name="equity")
    return BacktestResult(trades, equity, compute_stats(trades, equity, df, cfg))


def compute_stats(trades: list[Trade], equity: pd.Series, df: pd.DataFrame, cfg: BacktestConfig) -> dict:
    pnls = np.array([t.pnl for t in trades])
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    drawdown = equity / equity.cummax() - 1
    returns = equity.pct_change().dropna()
    periods_per_year = pd.Timedelta(days=365) / (equity.index[1] - equity.index[0]) if len(equity) > 1 else 1
    sharpe = (
        returns.mean() / returns.std() * np.sqrt(periods_per_year) if returns.std() > 0 else 0.0
    )
    return {
        "initial_capital": cfg.initial_capital,
        "final_equity": round(float(equity.iloc[-1]), 2),
        "total_return_pct": round((equity.iloc[-1] / cfg.initial_capital - 1) * 100, 2),
        "buy_and_hold_pct": round((df["close"].iloc[-1] / df["open"].iloc[0] - 1) * 100, 2),
        "max_drawdown_pct": round(float(drawdown.min()) * 100, 2),
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "profit_factor": round(wins.sum() / -losses.sum(), 2) if losses.sum() < 0 else float("inf"),
        "sharpe": round(float(sharpe), 2),
        "fees_paid": round(
            sum(t.qty * (t.entry_price + t.exit_price) * cfg.fee_rate for t in trades), 2
        ),
    }
