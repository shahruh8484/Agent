"""CLI entrypoint.

Examples:
    python -m cryptobot.main backtest --symbol BTC/USDT --timeframe 4h --days 730
    python -m cryptobot.main backtest --synthetic          # offline demo
    python -m cryptobot.main optimize --symbol ETH/USDT --timeframe 1h
    python -m cryptobot.main paper --symbol BTC/USDT --timeframe 1h
    python -m cryptobot.main evedex --instrument BTCUSDT:DEMO      # EVEDEX demo
"""
from __future__ import annotations

import itertools
import json
import logging
import os

import typer

from cryptobot.backtest import BacktestConfig, run_backtest
from cryptobot.data import fetch_ohlcv, synthetic_ohlcv
from cryptobot.strategy import StrategyParams

app = typer.Typer(add_completion=False)


def _load(symbol: str, timeframe: str, days: int, exchange: str, synthetic: bool):
    return synthetic_ohlcv() if synthetic else fetch_ohlcv(symbol, timeframe, days, exchange)


@app.command()
def backtest(
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    days: int = 365,
    exchange: str = "binance",
    capital: float = 1000.0,
    risk: float = typer.Option(0.01, help="Fraction of equity risked per trade"),
    fast: int = 20,
    slow: int = 50,
    stop_atr: float = 2.0,
    tp_atr: float = typer.Option(4.0, help="Take-profit in ATRs, 0 to disable"),
    synthetic: bool = typer.Option(False, help="Use generated data instead of the exchange"),
    trades_csv: str = typer.Option("", help="Save the trade list to this CSV"),
):
    """Test the strategy on historical candles."""
    df = _load(symbol, timeframe, days, exchange, synthetic)
    params = StrategyParams(fast_ema=fast, slow_ema=slow, stop_atr=stop_atr, take_profit_atr=tp_atr)
    result = run_backtest(df, params, BacktestConfig(initial_capital=capital, risk_per_trade=risk))
    typer.echo(f"{symbol} {timeframe}: {df.index[0]} .. {df.index[-1]} ({len(df)} candles)")
    typer.echo(json.dumps(result.stats, indent=2))
    if trades_csv:
        import pandas as pd

        pd.DataFrame([t.__dict__ for t in result.trades]).to_csv(trades_csv, index=False)
        typer.echo(f"trades saved to {trades_csv}")


@app.command()
def optimize(
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    days: int = 730,
    exchange: str = "binance",
    synthetic: bool = False,
    train_frac: float = typer.Option(0.7, help="Share of history used to pick parameters"),
):
    """Grid-search EMA/stop params on the first part of history, then check them on the rest."""
    df = _load(symbol, timeframe, days, exchange, synthetic)
    split = int(len(df) * train_frac)
    train, test = df.iloc[:split], df.iloc[split:]

    results = []
    for fast, slow, stop_atr in itertools.product([10, 20, 30], [50, 100, 200], [1.5, 2.0, 3.0]):
        if fast >= slow:
            continue
        params = StrategyParams(fast_ema=fast, slow_ema=slow, stop_atr=stop_atr)
        stats = run_backtest(train, params).stats
        if stats["trades"] >= 10:
            results.append((stats["sharpe"], params, stats))
    if not results:
        typer.echo("not enough trades in the training window; use more --days")
        raise typer.Exit(1)

    results.sort(key=lambda r: r[0], reverse=True)
    typer.echo("Top 5 on training data:")
    for sharpe, p, s in results[:5]:
        typer.echo(f"  fast={p.fast_ema} slow={p.slow_ema} stop={p.stop_atr}: sharpe={sharpe} return={s['total_return_pct']}% dd={s['max_drawdown_pct']}%")
    best = results[0][1]
    typer.echo("\nBest params on unseen test data (this is the number that matters):")
    typer.echo(json.dumps(run_backtest(test, best).stats, indent=2))


@app.command()
def paper(
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    exchange: str = "binance",
    capital: float = 1000.0,
    risk: float = 0.01,
    fast: int = 20,
    slow: int = 50,
    stop_atr: float = 2.0,
    tp_atr: float = 4.0,
    testnet: bool = typer.Option(False, help="Also place orders on the exchange testnet (needs EXCHANGE_API_KEY/SECRET)"),
    poll: int = 60,
):
    """Run the strategy on live candles with simulated money."""
    from cryptobot.paper import run_paper

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    params = StrategyParams(fast_ema=fast, slow_ema=slow, stop_atr=stop_atr, take_profit_atr=tp_atr)
    run_paper(
        symbol, timeframe, params, BacktestConfig(initial_capital=capital, risk_per_trade=risk),
        exchange_id=exchange, testnet=testnet,
        api_key=os.getenv("EXCHANGE_API_KEY", ""), api_secret=os.getenv("EXCHANGE_API_SECRET", ""),
        poll_seconds=poll,
    )


@app.command("evedex-instruments")
def evedex_instruments(env: str = typer.Option("demo", help="demo or prod")):
    """List EVEDEX instruments (names to pass as --instrument)."""
    from cryptobot.evedex import EvedexClient

    for i in EvedexClient(env).instruments():
        typer.echo(f"{i['name']:<20} mark={i['markPrice']:<12} maxLev={i['maxLeverage']:<4} minQty={i['minQuantity']}")


@app.command()
def evedex(
    instrument: str = typer.Option("BTCUSDT:DEMO", help="EVEDEX instrument, see evedex-instruments"),
    signal_symbol: str = typer.Option("BTC/USDT", help="Pair whose candles drive the signals"),
    signal_exchange: str = typer.Option("binance", help="ccxt exchange for candles (binance, bybit, okx...)"),
    timeframe: str = "1h",
    env: str = typer.Option("demo", help="demo (test USDT) or prod (real money, needs --real-money)"),
    real_money: bool = typer.Option(False, "--real-money", help="Required together with --env prod"),
    yes: bool = typer.Option(False, "--yes", help="Skip the real-money prompt (for servers; still needs --real-money)"),
    leverage: int = typer.Option(1, min=1, max=5),
    max_usd: float = typer.Option(100.0, help="Hard cap on position size in USD"),
    risk: float = typer.Option(0.01, help="Fraction of balance lost if the stop is hit"),
    fast: int = 20,
    slow: int = 50,
    stop_atr: float = 2.0,
    tp_atr: float = 4.0,
    poll: int = 60,
    state_dir: str = typer.Option("paper_state", help="Where status/trades are written for the panels"),
    instruments: str = typer.Option("", help="Several coins: 'XRPUSD,LINKUSD=LINK/USDT' (overrides --instrument)"),
    max_positions: int = typer.Option(2, min=1, help="Open longs at once across all coins"),
    news: str = typer.Option("off", help="News filter: off, shadow (only record) or on (veto entries, exit early)"),
    news_block: float = typer.Option(-0.3, help="Skip entries when the news score is at or below this"),
    news_exit: float = typer.Option(-0.7, help="Close a position when the news score is at or below this"),
):
    """Trade the strategy on EVEDEX (demo by default)."""
    from cryptobot.evedex import EvedexClient, LiveConfig, Market, parse_markets, run_live
    from cryptobot.news import NewsConfig

    if news not in ("off", "shadow", "on"):
        typer.echo("--news must be off, shadow or on")
        raise typer.Exit(1)
    markets = parse_markets(instruments) if instruments.strip() else [Market(instrument, signal_symbol)]
    instrument = ", ".join(m.instrument for m in markets)

    if env == "prod" and not real_money:
        typer.echo("--env prod trades REAL money; add --real-money to confirm.")
        raise typer.Exit(1)
    if env == "prod" and not yes:
        typer.confirm(
            f"REAL MONEY on {instrument}: up to ${max_usd} per position, {leverage}x leverage. Continue?",
            abort=True,
        )

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run_live(
        EvedexClient(env),
        LiveConfig(leverage=leverage, max_usd=max_usd, max_positions=max_positions),
        StrategyParams(fast_ema=fast, slow_ema=slow, stop_atr=stop_atr, take_profit_atr=tp_atr),
        BacktestConfig(risk_per_trade=risk),
        markets, signal_exchange=signal_exchange, timeframe=timeframe, poll_seconds=poll,
        state_dir=state_dir, news_cfg=NewsConfig(mode=news, block_below=news_block, exit_below=news_exit),
    )


@app.command()
def options(
    env: str = typer.Option("testnet", help="testnet (fake coins) or prod (real money, needs --real-money)"),
    real_money: bool = typer.Option(False, "--real-money", help="Required together with --env prod"),
    yes: bool = typer.Option(False, "--yes", help="Skip the real-money prompt (servers; still needs --real-money)"),
    currencies: str = typer.Option("BTC,ETH", help="Option underlyings on Deribit"),
    state_dir: str = "paper_state",
    check: int = typer.Option(300, help="Seconds between market checks"),
    poll: int = typer.Option(60, help="Seconds between status updates / panel button checks"),
):
    """Sell weekly out-of-the-money puts on Deribit (testnet by default)."""
    from cryptobot.options import DEFAULTS, DeribitClient, run_options

    if env not in ("testnet", "prod"):
        typer.echo("--env must be testnet or prod")
        raise typer.Exit(1)
    if env == "prod" and not real_money:
        typer.echo("--env prod trades REAL money; add --real-money to confirm.")
        raise typer.Exit(1)
    if env == "prod" and not yes:
        typer.confirm(f"REAL MONEY: selling puts on {currencies} on Deribit. Continue?", abort=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run_options(DeribitClient(env, os.getenv("DERIBIT_CLIENT_ID", ""), os.getenv("DERIBIT_CLIENT_SECRET", "")),
                [c.strip().upper() for c in currencies.split(",") if c.strip()], dict(DEFAULTS),
                state_dir=state_dir, check_seconds=check, poll_seconds=poll)


@app.command()
def panel(
    host: str = typer.Option("127.0.0.1", help="0.0.0.0 to open from other devices (needs PANEL_PASSWORD)"),
    port: int = 8050,
    state_dir: str = "paper_state",
):
    """Web panel to watch and control running EVEDEX bots."""
    import uvicorn

    from cryptobot.panel import create_app

    if host not in ("127.0.0.1", "localhost") and not os.getenv("PANEL_PASSWORD"):
        typer.echo("Set PANEL_PASSWORD before exposing the panel beyond localhost.")
        raise typer.Exit(1)
    typer.echo(f"Panel: http://{'localhost' if host == '0.0.0.0' else host}:{port}")
    uvicorn.run(create_app(state_dir), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    app()
