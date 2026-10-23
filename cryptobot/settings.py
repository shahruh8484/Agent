"""Settings a panel may change on a running bot, per coin.

The bot publishes SCHEMA in its status.json, so panels can render a form without
knowing the bot's internals; overrides come back in settings.json in the bot's
folder. The bot validates them again here and is the only authority: anything
out of range or inconsistent is rejected and the previous settings stay.
Keys, real-money mode, coins and the timeframe are deliberately not here.
"""
from __future__ import annotations

from cryptobot.backtest import BacktestConfig
from cryptobot.news import NewsConfig
from cryptobot.strategy import StrategyParams

SCHEMA = {
    "max_usd": {"label": "Максимум на позицию, $", "type": "float", "min": 1, "max": 1000, "step": 1},
    "risk_pct": {"label": "Риск на сделку, % баланса", "type": "float", "min": 0.1, "max": 5, "step": 0.1},
    "leverage": {"label": "Плечо, x", "type": "int", "min": 1, "max": 5, "step": 1},
    "stop_atr": {"label": "Стоп-лосс, ×ATR", "type": "float", "min": 0.5, "max": 10, "step": 0.1},
    "take_profit_atr": {"label": "Тейк-профит, ×ATR (0 — без тейка)", "type": "float", "min": 0, "max": 20,
                        "step": 0.1},
    "fast_ema": {"label": "Быстрая EMA, свечей", "type": "int", "min": 2, "max": 100, "step": 1},
    "slow_ema": {"label": "Медленная EMA, свечей", "type": "int", "min": 5, "max": 400, "step": 1},
    "rsi_max_entry": {"label": "Не входить при RSI выше", "type": "float", "min": 50, "max": 100, "step": 1},
    "news_mode": {"label": "Новостной фильтр", "type": "choice",
                  "options": {"off": "выключен", "shadow": "наблюдение", "on": "включён"}},
    "news_block_below": {"label": "Пропускать вход при оценке новостей ≤", "type": "float", "min": -1, "max": 0,
                         "step": 0.05},
    "news_exit_below": {"label": "Закрывать позицию при оценке новостей ≤", "type": "float", "min": -1, "max": 0,
                        "step": 0.05},
}
RULES = [
    ["fast_ema", "<", "slow_ema", "Быстрая EMA должна быть короче медленной"],
    ["news_exit_below", "<=", "news_block_below", "Порог закрытия должен быть не выше порога пропуска входа"],
]


def base_settings(live, params: StrategyParams, risk: BacktestConfig, news: NewsConfig) -> dict:
    """The bot's start-up settings (from .env / CLI), in SCHEMA units."""
    return {
        "max_usd": float(live.max_usd), "risk_pct": round(risk.risk_per_trade * 100, 4), "leverage": int(live.leverage),
        "stop_atr": float(params.stop_atr), "take_profit_atr": float(params.take_profit_atr),
        "fast_ema": int(params.fast_ema), "slow_ema": int(params.slow_ema),
        "rsi_max_entry": float(min(params.rsi_max_entry, 100)), "news_mode": news.mode,
        "news_block_below": float(news.block_below), "news_exit_below": float(news.exit_below),
    }


def validate(values: dict) -> tuple[dict, list[str]]:
    """Coerce and range-check overrides; unknown keys are dropped. Returns (clean, errors)."""
    clean, errors = {}, []
    for key, raw in values.items():
        spec = SCHEMA.get(key)
        if spec is None:
            continue
        label = spec["label"]
        if spec["type"] == "choice":
            if raw not in spec["options"]:
                errors.append(f"{label}: недопустимое значение {raw!r}")
                continue
            clean[key] = raw
            continue
        try:
            number = float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            errors.append(f"{label}: не число ({raw!r})")
            continue
        if number != number or not spec["min"] <= number <= spec["max"]:  # NaN fails too
            errors.append(f"{label}: должно быть от {spec['min']} до {spec['max']}")
            continue
        if spec["type"] == "int":
            if number != int(number):
                errors.append(f"{label}: должно быть целым")
                continue
            number = int(number)
        clean[key] = number
    return clean, errors


def check_rules(merged: dict) -> list[str]:
    errors = []
    for a, op, b, message in RULES:
        if op == "<" and not merged[a] < merged[b] or op == "<=" and not merged[a] <= merged[b]:
            errors.append(message)
    return errors


def effective(base: dict, overrides: dict) -> tuple[dict, list[str]]:
    """Base settings with valid overrides applied; on any error the overrides are ignored as a whole."""
    clean, errors = validate(overrides)
    merged = {**base, **clean}
    errors += check_rules(merged)
    if errors:
        return dict(base), errors
    return merged, []


def to_objects(s: dict, live):
    """Settings dict -> the objects the strategy code uses."""
    from cryptobot.evedex import LiveConfig

    return (
        LiveConfig(leverage=int(s["leverage"]), max_usd=float(s["max_usd"]), max_positions=live.max_positions),
        StrategyParams(fast_ema=int(s["fast_ema"]), slow_ema=int(s["slow_ema"]), stop_atr=float(s["stop_atr"]),
                       take_profit_atr=float(s["take_profit_atr"]),
                       # 100 in the form means "no RSI filter"; the strategy compares with <
                       rsi_max_entry=101.0 if s["rsi_max_entry"] >= 100 else float(s["rsi_max_entry"])),
        BacktestConfig(risk_per_trade=float(s["risk_pct"]) / 100),
        s["news_mode"], float(s["news_block_below"]), float(s["news_exit_below"]),
    )
