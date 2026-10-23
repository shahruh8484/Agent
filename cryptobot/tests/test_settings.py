import json

from cryptobot.backtest import BacktestConfig
from cryptobot.evedex import LiveConfig
from cryptobot.news import NewsConfig
from cryptobot.settings import SCHEMA, base_settings, effective, to_objects, validate
from cryptobot.strategy import StrategyParams
from cryptobot.tests.test_news import FakeClient, _run_once

BASE = base_settings(LiveConfig(max_usd=10, leverage=1, max_positions=2), StrategyParams(), BacktestConfig(),
                     NewsConfig(mode="shadow"))


def test_base_uses_schema_keys_and_units():
    assert set(BASE) == set(SCHEMA)
    assert BASE["risk_pct"] == 1.0 and BASE["max_usd"] == 10 and BASE["news_mode"] == "shadow"


def test_validate_coerces_and_rejects():
    clean, errors = validate({"max_usd": "25,5", "leverage": "2", "news_mode": "on", "unknown": 1})
    assert clean == {"max_usd": 25.5, "leverage": 2, "news_mode": "on"} and errors == []
    _, errors = validate({"leverage": 9, "max_usd": "abc", "fast_ema": 2.5, "news_mode": "yolo", "risk_pct": "nan"})
    assert len(errors) == 5 and any("Плечо" in e for e in errors)


def test_effective_rejects_inconsistent_overrides_as_a_whole():
    merged, errors = effective(BASE, {"max_usd": 20, "fast_ema": 60})  # 60 >= slow 50
    assert merged == BASE and errors == ["Быстрая EMA должна быть короче медленной"]
    merged, errors = effective(BASE, {"max_usd": 20, "news_exit_below": -0.1})  # above block -0.3
    assert merged == BASE and "Порог закрытия" in errors[0]
    merged, errors = effective(BASE, {"max_usd": 20, "risk_pct": 2})
    assert errors == [] and merged["max_usd"] == 20 and merged["risk_pct"] == 2


def test_to_objects():
    live, params, risk, mode, block, exit_ = to_objects({**BASE, "rsi_max_entry": 100, "risk_pct": 2.5},
                                                        LiveConfig(max_positions=3))
    assert live.max_positions == 3 and params.rsi_max_entry == 101 and risk.risk_per_trade == 0.025
    assert (mode, block, exit_) == ("shadow", -0.3, -0.7)


def test_live_loop_applies_panel_overrides_per_coin(tmp_path, monkeypatch):
    for name, over in [("XRPUSD", {"max_usd": 5}), ("LINKUSD", {"max_usd": 3, "news_mode": "on"})]:
        d = tmp_path / f"evedex_demo_{name}_1h"
        d.mkdir(parents=True)
        (d / "settings.json").write_text(json.dumps({**over, "updated_at": 1}))
    client = FakeClient()
    status = _run_once(tmp_path, monkeypatch, client, "shadow", -0.9, max_positions=2)
    # XRP: shadow news, $5 cap -> opens; LINK: news filter switched on from the panel -> blocked
    assert client.opened == [("XRPUSD", 5)]
    link = status["evedex_demo_LINKUSD_1h"]
    assert link["reason"] == "news negative; entry skipped" and link["config"]["max_usd"] == 3
    assert link["config"]["settings_overrides"] == ["max_usd", "news_mode"] and link["config"]["settings_error"] == ""
    assert "settings_schema" in link["config"] and link["config"]["settings_defaults"]["max_usd"] == 10


def test_live_loop_ignores_bad_overrides_and_reports_them(tmp_path, monkeypatch):
    d = tmp_path / "evedex_demo_XRPUSD_1h"
    d.mkdir(parents=True)
    (d / "settings.json").write_text(json.dumps({"max_usd": 5, "leverage": 50}))
    client = FakeClient()
    status = _run_once(tmp_path, monkeypatch, client, "shadow", 0.1, max_positions=2)
    xrp = status["evedex_demo_XRPUSD_1h"]
    assert ("XRPUSD", 10) in client.opened  # .env defaults kept
    assert "Плечо" in xrp["config"]["settings_error"] and xrp["config"]["settings_overrides"] == []
