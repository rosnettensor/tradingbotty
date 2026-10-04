"""v0.2: dashboard controls, backtests, stock market hours, editable sources."""
import asyncio
import math
import random
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty import backtest, controls  # noqa: E402
from tradingbotty.config import load_settings  # noqa: E402
from tradingbotty.data.prices import Quote, us_market_open  # noqa: E402
from tradingbotty.strategy import SEED_VARIANTS, StrategyConfig  # noqa: E402


def _series(n=900, seed=1):
    rng = random.Random(seed)
    now = int(time.time() // 60 * 60)
    out = {}
    for sym, p in (("BTC", 60000.0), ("ETH", 2500.0), ("SOL", 140.0), ("DOGE", 0.1)):
        rows = []
        for i in range(n):
            p *= math.exp(rng.gauss(0.0001, 0.003))
            rows.append((now - (n - i) * 60, p))
        out[sym] = rows
    return out


def test_controls_are_clamped_and_never_unlock_debt():
    assert controls.coerce("risk.max_position_pct", 500) == 50
    assert controls.coerce("risk.max_open_positions", 3.7) == 4
    with pytest.raises(ValueError):
        controls.coerce("risk.allow_margin", True)  # not a dashboard setting at all
    raw = load_settings().raw
    controls.apply(raw, {"paper.fee_pct": 0.1, "nope.nope": 1})
    assert raw["paper"]["fee_pct"] == 0.1 and raw["risk"]["allow_margin"] is False


def test_strategy_edits_are_clamped():
    cfg = StrategyConfig.from_dict({"entry_score": 5, "exit_score": 3, "position_pct": 99, "max_positions": 0,
                                    "trade_crypto": False, "trade_stocks": False}).clamped()
    assert cfg.entry_score == 0.9 and cfg.exit_score <= 0.8 and cfg.position_pct == 50 and cfg.max_positions == 1
    assert cfg.trade_crypto  # a strategy must trade something


def test_backtest_runs_and_never_goes_negative():
    raw = load_settings().raw
    controls.apply(raw, {})
    ds = backtest.build_dataset(_series(), {s: "crypto" for s in ("BTC", "ETH", "SOL", "DOGE")}, hours=10)
    assert ds and ds.hours > 5
    for name, cfg in SEED_VARIANTS.items():
        res = backtest.run(ds, cfg, raw)
        assert res["final_equity"] >= 0 and res["max_drawdown_pct"] >= 0
    aggressive = StrategyConfig(entry_score=0.1, exit_score=0.0, max_buys_per_hour=20, cooldown_minutes=0,
                                min_hold_minutes=0, max_positions=10)
    res = backtest.run(ds, aggressive, raw)
    assert res["trades"] > 0 and res["fees"] > 0
    again = backtest.run(ds, aggressive, raw)
    assert again["return_pct"] == res["return_pct"]  # deterministic on the same data
    ranked = backtest.autotune(ds, StrategyConfig(), raw, n=8, seed=3)
    assert len(ranked) == 9 and ranked[0]["fitness"] >= ranked[-1]["fitness"]


def test_hourly_buy_limit_is_respected():
    raw = load_settings().raw
    controls.apply(raw, {})
    ds = backtest.build_dataset(_series(seed=4), {s: "crypto" for s in ("BTC", "ETH", "SOL", "DOGE")}, hours=10)
    cfg = StrategyConfig(entry_score=0.1, exit_score=0.0, max_buys_per_hour=1, cooldown_minutes=0, min_hold_minutes=0,
                         take_profit_pct=0.5, stop_loss_pct=0.5)
    buys = [t["ts"] for t in backtest.run(ds, cfg, raw, keep_trades=10_000)["trade_list"] if t["side"] == "BUY"]
    assert all(b - a >= 3600 for a, b in zip(buys, buys[1:]))


def test_stocks_only_trade_in_us_session():
    assert not us_market_open(1790438400)  # Saturday 2026-09-26, noon in New York
    assert us_market_open(1790780400)  # Wednesday 2026-09-30, 11:00 in New York
    assert not us_market_open(1790780400 + 7 * 3600)  # same day 18:00: after the close
    q = Quote("AAPL", "stock", price=100, updated=time.time())
    assert q.tradable == us_market_open()
    assert Quote("BTC", "crypto", price=1).tradable


def test_engine_controls_sources_and_backtest(tmp_path, monkeypatch):
    monkeypatch.setenv("TB_SIMULATE", "1")
    monkeypatch.setenv("TB_DB", str(tmp_path / "e.db"))
    from tradingbotty.engine import Engine
    e = Engine(load_settings())
    asyncio.run(e.prices.backfill())
    e.set_controls({"risk.max_position_pct": 10, "engine.tick_seconds": 30})
    assert e.settings["risk"]["max_position_pct"] == 10
    e.set_controls({"risk.max_position_pct": None})
    assert e.settings["risk"]["max_position_pct"] == 25.0
    # survives a restart
    e2 = Engine(load_settings())
    assert e2.settings["engine"]["tick_seconds"] == 30
    r = asyncio.run(e.add_source("crypto", "arb"))
    assert r["ok"] and "ARB" in e.prices.quotes
    assert not asyncio.run(e.add_source("crypto", "ARB"))["ok"]
    assert e.remove_source("crypto", "ARB")["ok"] and "ARB" not in e.prices.quotes
    champ = e.champion()
    v = e.set_variant_config(champ.id, {"entry_score": 0.5}, as_new=True, name="My test")
    assert v.name == "My test" and v.config.entry_score == 0.5 and champ.config.entry_score != 0.5
    res = e.backtest_sync(champ.config, hours=12)
    assert res["hours"] > 6 and "curve" in res
    with pytest.raises(ValueError):
        e.set_agent("buyer", enabled=False)  # essential agents can't be switched off
    e.set_agent("hype", enabled=False)
    asyncio.run(e.tick())
    assert e.agent("hype").status == "off" and e.bb.hype == {}


def test_buy_and_hold_benchmark_never_sells():
    raw = load_settings().raw
    controls.apply(raw, {})
    ds = backtest.build_dataset(_series(), {s: "crypto" for s in ("BTC", "ETH", "SOL", "DOGE")}, hours=10)
    res = backtest.run(ds, SEED_VARIANTS["Buy & Hold"], raw, keep_trades=100)
    assert [t["side"] for t in res["trade_list"]] == ["BUY"] * 4 and res["open_positions"] == 4


def test_swing_signal_follows_multi_day_trend():
    from tradingbotty.strategy import swing_signal
    up = [100 * math.exp(0.0008 * i + 0.003 * math.sin(i)) for i in range(24 * 25)]
    down = [100 * math.exp(-0.0008 * i + 0.003 * math.sin(i)) for i in range(24 * 25)]
    assert swing_signal(up) > 0.3 and swing_signal(down) < -0.3 and swing_signal(up[:50]) == 0.0


def test_swiss_and_german_market_hours():
    from tradingbotty.data.prices import exchange_of, market_open
    wed_10_zurich = 1790755200 + 3600 * 0  # 2026-09-30 08:00 UTC = 10:00 Zurich
    assert exchange_of("NESN.SW")[0] == "SIX Swiss" and exchange_of("SAP.DE")[0] == "Xetra"
    assert market_open("NESN.SW", wed_10_zurich) and market_open("SAP.DE", wed_10_zurich)
    assert not market_open("AAPL", wed_10_zurich)  # 04:00 in New York


def test_minimum_fee_and_fx_conversion():
    from tradingbotty.brokers.paper import PaperBroker
    from tradingbotty.data.prices import PriceFeed
    import httpx
    b = PaperBroker(100, 0.05, 0)
    fill = b.buy("NESN.SW", 10, 100, min_fee=1.0)
    assert fill.fee == 1.0  # the minimum beats 0.05% of 10 USD
    with pytest.raises(ValueError):
        b.buy("NOVN.SW", 0.8, 100, min_fee=1.0)  # fee would eat the order

    def handler(req):
        if "CHFUSD" in req.url.path:
            return httpx.Response(200, json={"chart": {"result": [{"meta": {"regularMarketPrice": 1.25}}]}})
        return httpx.Response(200, json={"chart": {"result": [{"meta": {"currency": "CHF", "regularMarketPrice": 80,
            "chartPreviousClose": 79, "regularMarketTime": time.time()}, "timestamp": [time.time() - 60],
            "indicators": {"quote": [{"close": [79.5]}]}}]}})
    feed = PriceFeed([], ["NESN.SW"])
    feed.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(feed.poll_stocks())
    assert feed.quotes["NESN.SW"].price == pytest.approx(100.0)  # 80 CHF at 1.25 USD
