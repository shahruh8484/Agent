import json
import subprocess

import pandas as pd
import pytest
from typer.testing import CliRunner

from cryptobot import evedex
from cryptobot.backtest import BacktestConfig
from cryptobot.evedex import EvedexClient, EvedexError, LiveConfig, decide
from cryptobot.main import app
from cryptobot.strategy import StrategyParams

PARAMS = StrategyParams(stop_atr=2.0, take_profit_atr=4.0)
RISK = BacktestConfig(risk_per_trade=0.01)


def _bar(entry=False, exit=False, atr=500.0):
    return pd.Series({"entry": entry, "exit": exit, "atr": atr, "close": 60000.0})


def _state(position=None, tpsl=(), balance=1000.0, mark=60000.0):
    return {"position": position, "tpsl": list(tpsl), "availableBalance": balance, "markPrice": mark}


def test_entry_sizes_by_risk():
    action = decide(_bar(entry=True), _state(), PARAMS, RISK, LiveConfig(max_usd=10_000))
    assert action.kind == "open"
    assert action.stop == 59000.0 and action.take_profit == 62000.0
    # 1% of $1000 = $10 lost over a $1000 stop distance -> 0.01 BTC = $600.
    assert action.cash == pytest.approx(600.0)


def test_entry_respects_max_usd_and_leverage():
    tight = decide(_bar(entry=True, atr=10), _state(), PARAMS, RISK, LiveConfig(max_usd=50))
    assert tight.cash == 50
    no_cap = decide(_bar(entry=True, atr=10), _state(), PARAMS, RISK, LiveConfig(max_usd=1e9, leverage=2))
    assert no_cap.cash == pytest.approx(2000.0)


def test_exit_closes_only_long():
    long = {"side": "BUY", "quantity": 0.01}
    assert decide(_bar(exit=True), _state(position=long), PARAMS, RISK, LiveConfig()).kind == "close"
    assert decide(_bar(), _state(position=long), PARAMS, RISK, LiveConfig()).kind == "hold"
    short = {"side": "SELL", "quantity": 0.01}
    assert decide(_bar(exit=True), _state(position=short), PARAMS, RISK, LiveConfig()).kind == "hold"


def test_stale_tpsl_is_cleaned_up():
    action = decide(_bar(), _state(tpsl=[{"id": "1"}]), PARAMS, RISK, LiveConfig())
    assert action.kind == "close"


def test_client_parses_bridge_output(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs["env"]["EVEDEX_ENV"]))
        return subprocess.CompletedProcess(cmd, 0, stdout='noise\n{"closed": true}\n', stderr="")

    monkeypatch.setattr(evedex.subprocess, "run", fake_run)
    assert EvedexClient("demo").close("BTCUSDT:DEMO") == {"closed": True}
    cmd, env = calls[0]
    assert cmd[2] == "close" and json.loads(cmd[3]) == {"instrument": "BTCUSDT:DEMO"} and env == "demo"


def test_client_raises_bridge_error(monkeypatch):
    monkeypatch.setattr(
        evedex.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout='{"error": "boom"}\n', stderr=""),
    )
    with pytest.raises(EvedexError, match="boom"):
        EvedexClient("demo").state("BTCUSDT:DEMO")


def test_prod_requires_real_money_flag(monkeypatch):
    monkeypatch.setattr(evedex, "run_live", lambda *a, **k: pytest.fail("must not start"))
    result = CliRunner().invoke(app, ["evedex", "--env", "prod"])
    assert result.exit_code == 1 and "--real-money" in result.output


def test_prod_asks_for_confirmation(monkeypatch):
    monkeypatch.setattr(evedex, "run_live", lambda *a, **k: pytest.fail("must not start"))
    result = CliRunner().invoke(app, ["evedex", "--env", "prod", "--real-money"], input="n\n")
    assert result.exit_code != 0 and "REAL MONEY" in result.output


def test_prod_yes_skips_prompt_but_still_needs_real_money(monkeypatch):
    started = []
    monkeypatch.setattr(evedex, "run_live", lambda *a, **k: started.append(a[0].env))
    assert CliRunner().invoke(app, ["evedex", "--env", "prod", "--yes"]).exit_code == 1
    assert CliRunner().invoke(app, ["evedex", "--env", "prod", "--real-money", "--yes"]).exit_code == 0
    assert started == ["prod"]


def test_control_file_merges_fields(tmp_path):
    from cryptobot.evedex import BotFiles, update_control

    files = BotFiles(tmp_path, "demo", "BTCUSDT:DEMO", "1h")
    update_control(files.control, paused=True)
    update_control(files.control, close_requested=True)
    assert files.paused() and files.close_requested()
    update_control(files.control, close_requested=False)
    assert files.paused() and not files.close_requested()


def test_bridge_errors_never_leak_tokens_and_get_hints(monkeypatch):
    leaked = '{"error": "{\'request\': {\'Authorization\': \'Bearer eyJhbGci.eyJ1c2Vy.sig-_x\'}} user_not_found_error"}\n'
    monkeypatch.setattr(
        evedex.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout=leaked, stderr=""),
    )
    with pytest.raises(EvedexError) as err:
        EvedexClient("demo").state("BTCUSDT:DEMO")
    assert "eyJ" not in str(err.value) and "Bearer ***" in str(err.value)
    assert "MetaMask" in str(err.value)
