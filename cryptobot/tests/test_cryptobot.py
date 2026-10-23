import numpy as np
import pandas as pd
import pytest

from cryptobot.backtest import BacktestConfig, position_size, run_backtest
from cryptobot.data import synthetic_ohlcv
from cryptobot.paper import PaperState, step
from cryptobot.strategy import StrategyParams, generate_signals, rsi


# A straight-line ramp has RSI=100, so tests on ramps disable the overbought filter.


def _candles(closes, spread=0.0):
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    index = pd.date_range("2024-01-01", periods=len(closes), freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes) + spread,
            "low": np.minimum(opens, closes) - spread,
            "close": closes,
            "volume": 1.0,
        },
        index=index,
    )


def test_position_size_risks_fixed_fraction():
    cfg = BacktestConfig(risk_per_trade=0.01)
    qty = position_size(1000, entry=100, stop=95, cfg=cfg)
    assert qty * 5 == pytest.approx(10)  # losing $5/unit risks 1% of $1000


def test_position_size_never_uses_leverage():
    cfg = BacktestConfig(risk_per_trade=0.5)
    qty = position_size(1000, entry=100, stop=99.9, cfg=cfg)
    assert qty * 100 * (1 + cfg.fee_rate) == pytest.approx(1000)


def test_rsi_bounds():
    values = rsi(synthetic_ohlcv(500)["close"], 14)
    assert values.between(0, 100).all()


def test_signals_have_no_lookahead():
    df = synthetic_ohlcv(1000)
    full = generate_signals(df, StrategyParams())
    truncated = generate_signals(df.iloc[:600], StrategyParams())
    pd.testing.assert_series_equal(full["entry"].iloc[:600], truncated["entry"])


def test_backtest_trades_on_trend_and_respects_capital():
    # Flat, then strong uptrend, then crash: should enter on the cross and get stopped/exit.
    closes = [100.0] * 80 + list(np.linspace(100, 200, 100)) + list(np.linspace(200, 120, 40))
    result = run_backtest(_candles(closes, spread=0.5), StrategyParams(take_profit_atr=0, rsi_max_entry=101))
    assert len(result.trades) >= 1
    first = result.trades[0]
    assert first.entry_time > pd.Timestamp("2024-01-04", tz="UTC")  # entered after warm-up
    assert result.stats["total_return_pct"] > 0
    assert (result.equity > 0).all()


def test_stop_loss_limits_loss():
    closes = [100.0] * 60 + list(np.linspace(100, 110, 20)) + [60.0] * 10
    params = StrategyParams(fast_ema=5, slow_ema=20, take_profit_atr=0, rsi_max_entry=101)
    cfg = BacktestConfig(risk_per_trade=0.01)
    result = run_backtest(_candles(closes, spread=0.5), params, cfg)
    stopped = [t for t in result.trades if t.reason == "stop"]
    assert stopped
    # Gap through the stop can lose more than 1%, but sizing keeps it bounded.
    assert result.stats["max_drawdown_pct"] > -15


def test_paper_step_matches_one_candle_at_a_time():
    df = synthetic_ohlcv(1500)
    state = PaperState(cash=1000.0)
    params, cfg = StrategyParams(), BacktestConfig()
    orders = []
    for end in range(200, len(df) + 1):
        orders += step(state, df.iloc[:end], params, cfg)
    assert any(o["side"] == "buy" for o in orders)
    # Re-processing the same candle is a no-op.
    assert step(state, df, params, cfg) == []
    assert state.cash > 0
