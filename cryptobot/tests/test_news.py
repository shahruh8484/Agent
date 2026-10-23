import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from cryptobot import evedex
from cryptobot.backtest import BacktestConfig
from cryptobot.evedex import Action, LiveConfig, Market, decide, parse_markets, run_live
from cryptobot.news import (
    Headline,
    NewsConfig,
    NewsDesk,
    NewsView,
    base_coin,
    effect,
    parse_feed,
    relevant,
)
from cryptobot.strategy import StrategyParams

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>SEC drops appeal against Ripple, XRP jumps</title><link>https://x/1</link>
<pubDate>Fri, 23 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Chainlink LINK integrates with bank</title><link>https://x/2</link>
<pubDate>Fri, 23 Oct 2026 09:00:00 GMT</pubDate></item>
<item><title>How to link your wallet</title><link>https://x/3</link>
<pubDate>Fri, 23 Oct 2026 08:00:00 GMT</pubDate></item>
<item><title>Old XRP story</title><link>https://x/4</link>
<pubDate>Mon, 19 Oct 2026 08:00:00 GMT</pubDate></item>
<item><title>Crypto market sees $1B liquidations</title><link>https://x/5</link>
<pubDate>Fri, 23 Oct 2026 07:00:00 GMT</pubDate></item>
<item><title>No date</title></item>
<item><title>XRP evil link</title><link>javascript:alert(1)</link><pubDate>Fri, 23 Oct 2026 06:00:00 GMT</pubDate></item>
</channel></rss>"""


def _headlines():
    return parse_feed(RSS, "coindesk.com")


def test_base_coin():
    assert base_coin("XRPUSD") == "XRP" and base_coin("BTCUSDT:DEMO") == "BTC" and base_coin("linkusd") == "LINK"


def test_parse_feed_skips_items_without_date():
    items = _headlines()
    assert len(items) == 6 and items[-1].url == ""
    assert items[0].published == datetime(2026, 10, 23, 10, tzinfo=timezone.utc)


def test_relevant_picks_coin_then_market_and_drops_stale():
    xrp = relevant(_headlines(), "XRP", 24, NOW)
    assert [(k, h.url) for k, h in xrp] == [("coin", "https://x/1"), ("coin", ""), ("market", "https://x/5")]
    link = relevant(_headlines(), "LINK", 24, NOW)
    # "link your wallet" must not match the LINK ticker
    assert [h.url for _, h in link if _ == "coin"] == ["https://x/2"]


class FakeLLM:
    configured = True

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete_json(self, system, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _desk(llm, headlines=None, errors=()):
    return NewsDesk(NewsConfig(mode="shadow", max_age_hours=10_000), llm,
                    fetch=lambda feeds: (headlines if headlines is not None else _headlines(), list(errors)))


def test_desk_scores_clamps_and_marks_key_headlines():
    llm = FakeLLM([{"score": 5, "summary": "Хорошо", "key": [0, 99]}])
    view = _desk(llm).views(["XRPUSD"])["XRPUSD"]
    assert view.score == 1.0 and view.summary == "Хорошо"
    assert view.headlines[0].get("key") and not view.headlines[1].get("key")
    # headlines are passed as data, with the coin
    assert "Coin: XRP" in llm.prompts[0] and "SEC drops appeal" in llm.prompts[0]


def test_desk_reuses_score_while_headlines_unchanged():
    llm = FakeLLM([{"score": -0.5, "summary": "a"}])
    desk = _desk(llm)
    desk.views(["XRPUSD"])
    assert desk.views(["XRPUSD"])["XRPUSD"].score == -0.5 and len(llm.prompts) == 1


def test_desk_failures_never_raise():
    view = _desk(FakeLLM([RuntimeError("timeout")])).views(["XRPUSD"])["XRPUSD"]
    assert view.score is None and "ИИ не ответил" in view.error
    down = _desk(FakeLLM([]), headlines=[], errors=["coindesk.com: 503"]).views(["XRPUSD"])["XRPUSD"]
    assert down.score is None and "недоступны" in down.error
    quiet = _desk(FakeLLM([]), headlines=[]).views(["XRPUSD"])["XRPUSD"]
    assert quiet.score == 0.0


def test_desk_without_key_explains():
    class NoKey(FakeLLM):
        configured = False

    view = _desk(NoKey([])).views(["XRPUSD"])["XRPUSD"]
    assert view.score is None and "OPENAI_API_KEY" in view.error


def test_effect_thresholds():
    cfg = NewsConfig(block_below=-0.3, exit_below=-0.7)
    assert effect(NewsView(score=-0.4), cfg, in_position=False, wants_entry=True) == "block"
    assert effect(NewsView(score=-0.2), cfg, in_position=False, wants_entry=True) == ""
    assert effect(NewsView(score=-0.8), cfg, in_position=True, wants_entry=False) == "exit"
    assert effect(NewsView(score=-0.5), cfg, in_position=True, wants_entry=False) == ""
    assert effect(NewsView(score=None), cfg, in_position=True, wants_entry=True) == ""


def test_parse_markets():
    markets = parse_markets("XRPUSD, LINKUSD=LINK/USDT ,TAOUSD")
    assert [(m.instrument, m.signal_symbol) for m in markets] == [
        ("XRPUSD", "XRP/USDT"), ("LINKUSD", "LINK/USDT"), ("TAOUSD", "TAO/USDT")]
    with pytest.raises(ValueError):
        parse_markets("XRPUSD,XRPUSD")


def test_position_limit_blocks_new_entries():
    bar = pd.Series({"entry": True, "exit": False, "atr": 0.01, "close": 1.3})
    state = {"position": None, "tpsl": [], "availableBalance": 25.0, "markPrice": 1.3}
    params, risk = StrategyParams(), BacktestConfig()
    assert decide(bar, state, params, risk, LiveConfig(max_usd=10, max_positions=2), open_positions=1).kind == "open"
    limited = decide(bar, state, params, risk, LiveConfig(max_usd=10, max_positions=2), open_positions=2)
    assert limited.kind == "hold" and limited.reason == "position limit reached"


# --- one full loop of the live bot with a fake exchange, candles and news ---

class Stop(Exception):
    pass


def _cross_up_candles():
    closes = [1.0] * 260 + [1.1]  # flat then a jump: EMA20 crosses EMA50 on the last closed candle
    idx = pd.date_range("2026-10-12", periods=len(closes) + 1, freq="h", tz="UTC")
    rows = [[int(t.timestamp() * 1000), c, c * 1.002, c * 0.998, c, 1.0] for t, c in zip(idx, closes + [1.1])]
    return rows


class FakeFeed:
    def __init__(self, *a, **k):
        pass

    def fetch_ohlcv(self, symbol, timeframe, limit):
        return _cross_up_candles()[-(limit):]


class FakeClient:
    env = "demo"

    def __init__(self, positions=None):
        self.positions = positions or {}
        self.opened = []
        self.closed = []

    def states(self, names):
        return {n: {"markPrice": 1.1, "availableBalance": 25.0, "fundingBalance": 25.0,
                    "position": self.positions.get(n), "tpsl": []} for n in names}

    def open_long(self, name, cash, leverage, stop, tp):
        self.opened.append((name, cash))
        self.positions[name] = {"side": "BUY", "quantity": 1, "avgPrice": 1.1, "unRealizedPnL": 0}
        return {"position": self.positions[name]}

    def close(self, name):
        self.closed.append(name)
        return {"closed": True}


def _run_once(tmp_path, monkeypatch, client, news_mode, score, max_positions=1):
    import ccxt

    monkeypatch.setattr(ccxt, "fakeex", FakeFeed, raising=False)
    monkeypatch.setattr(evedex.time, "sleep", lambda s: (_ for _ in ()).throw(Stop()))
    desk = NewsDesk(NewsConfig(mode=news_mode), FakeLLM([{"score": score, "summary": "s"}] * 5),
                    fetch=lambda feeds: ([Headline("XRP news", "x", "u", datetime.now(timezone.utc)),
                                          Headline("LINK news", "x", "u", datetime.now(timezone.utc))], []))
    with pytest.raises(Stop):
        run_live(client, LiveConfig(max_usd=10, max_positions=max_positions), StrategyParams(rsi_max_entry=101),
                 BacktestConfig(), [Market("XRPUSD", "XRP/USDT"), Market("LINKUSD", "LINK/USDT")],
                 signal_exchange="fakeex", state_dir=tmp_path, news_cfg=desk.cfg, news_desk=desk)
    return {p.name: json.loads((p / "status.json").read_text()) for p in tmp_path.iterdir()}


def test_live_loop_respects_position_limit_and_records_shadow_news(tmp_path, monkeypatch):
    client = FakeClient()
    status = _run_once(tmp_path, monkeypatch, client, "shadow", -0.9)
    # Both coins signalled, but only one long is allowed; shadow news doesn't block it.
    assert client.opened == [("XRPUSD", 10)]
    link = status["evedex_demo_LINKUSD_1h"]
    assert link["reason"] == "position limit reached"
    xrp = status["evedex_demo_XRPUSD_1h"]
    assert xrp["action"] == "open" and xrp["news"]["score"] == -0.9 and xrp["news"]["effect"] == "block"
    assert xrp["news"]["mode"] == "shadow" and xrp["state"]["position"]["side"] == "BUY"
    news_log = pd.read_csv(tmp_path / "evedex_demo_XRPUSD_1h" / "news.csv")
    assert news_log.iloc[0]["effect"] == "block" and news_log.iloc[0]["action"] == "open"


def test_live_loop_news_on_blocks_entries(tmp_path, monkeypatch):
    client = FakeClient()
    status = _run_once(tmp_path, monkeypatch, client, "on", -0.9, max_positions=2)
    assert client.opened == []
    assert status["evedex_demo_XRPUSD_1h"]["reason"] == "news negative; entry skipped"


def test_live_loop_news_on_exits_on_very_bad_news(tmp_path, monkeypatch):
    client = FakeClient(positions={"XRPUSD": {"side": "BUY", "quantity": 1, "avgPrice": 1.0, "unRealizedPnL": 0.1}})
    status = _run_once(tmp_path, monkeypatch, client, "on", -0.9, max_positions=2)
    assert client.closed == ["XRPUSD"]
    assert status["evedex_demo_XRPUSD_1h"]["reason"] == "news very negative; closing"


def test_live_loop_good_news_trades_normally(tmp_path, monkeypatch):
    client = FakeClient()
    _run_once(tmp_path, monkeypatch, client, "on", 0.4, max_positions=2)
    assert [n for n, _ in client.opened] == ["XRPUSD", "LINKUSD"]
