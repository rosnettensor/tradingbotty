"""Safety tests: the bot must never borrow, short, or spend past its AI budget."""
import asyncio
import random
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty.brokers.paper import PaperBroker  # noqa: E402
from tradingbotty.config import load_settings  # noqa: E402
from tradingbotty.db import DB  # noqa: E402
from tradingbotty.llm import LLM, Budget  # noqa: E402
from tradingbotty.strategy import SEED_VARIANTS, StrategyConfig, technical_signals  # noqa: E402


def test_paper_cannot_spend_more_than_cash():
    b = PaperBroker(100, 1.5, 0.05)
    b.buy("BTC", 60, 50_000)
    with pytest.raises(ValueError):
        b.buy("ETH", 41, 2_000)  # only 40 left
    assert b.cash >= 0


def test_paper_cannot_short_or_oversell():
    b = PaperBroker(100, 1.5, 0.05)
    with pytest.raises(ValueError):
        b.sell("BTC", 0.001, 50_000)  # nothing held: no short selling
    b.buy("BTC", 50, 50_000)
    qty = b.positions["BTC"].qty
    with pytest.raises(ValueError):
        b.sell("BTC", qty * 2, 50_000)


def test_round_trip_pays_fees_both_ways():
    b = PaperBroker(100, 1.5, 0.0)
    b.buy("BTC", 100, 50_000)
    fill = b.sell("BTC", b.positions["BTC"].qty, 50_000)
    assert b.cash == pytest.approx(100 * 0.985 * 0.985)
    assert fill.pnl == pytest.approx(b.cash - 100)


def test_equity_never_negative_under_random_trading():
    rng = random.Random(7)
    b = PaperBroker(100, 1.5, 0.05)
    price = {"A": 10.0, "B": 3.0}
    for _ in range(2000):
        for k in price:
            price[k] *= 1 + rng.gauss(0, 0.05)
        s = rng.choice(list(price))
        if rng.random() < 0.5 and b.cash > 1:
            b.buy(s, rng.uniform(0.5, b.cash), price[s])
        elif s in b.positions:
            b.sell(s, b.positions[s].qty * rng.uniform(0.1, 1), price[s])
        assert b.cash >= -1e-9
        assert b.equity(price) >= -1e-9


def _risk_ctx(tmp_path, kill=False):
    from tradingbotty.agents.team import RiskOfficer
    s = load_settings()
    db = DB(tmp_path / "t.db")
    ctx = SimpleNamespace(settings=s, db=db, kill_switch=kill, bus=SimpleNamespace(publish=lambda *a: None))
    return RiskOfficer(ctx)


def _variant(cash=100.0):
    return SimpleNamespace(broker=PaperBroker(cash, 1.5, 0.05), day_start_equity=100.0)


def test_risk_caps_position_size(tmp_path):
    r = _risk_ctx(tmp_path)
    usd, why = r.check_buy(_variant(), "BTC", 80, {"BTC": 50_000})
    assert why is None and usd == pytest.approx(25.0)  # 25% of equity


def test_risk_kill_switch_blocks_buys(tmp_path):
    r = _risk_ctx(tmp_path, kill=True)
    usd, why = r.check_buy(_variant(), "BTC", 10, {"BTC": 50_000})
    assert usd == 0 and "kill" in why


def test_risk_daily_loss_stop(tmp_path):
    r = _risk_ctx(tmp_path)
    v = _variant(cash=65.0)  # down 35% vs 100 at day start
    usd, why = r.check_buy(v, "BTC", 10, {"BTC": 50_000})
    assert usd == 0 and "daily loss" in why


def test_budget_blocks_calls_past_cap(tmp_path):
    db = DB(tmp_path / "b.db")
    budget = Budget(db, {"total_usd": 1.0, "spread_over_days": 1, "reinvest_profit_share": 0.1})
    assert budget.can_spend(0.5)
    db.execute("INSERT INTO llm_calls(ts,agent,model,input_tokens,output_tokens,cost_usd) VALUES(?,?,?,?,?,?)",
               (time.time(), "x", "m", 1, 1, 0.9))
    assert not budget.can_spend(0.2)
    llm = LLM("sk-test", budget, db, "claude-haiku-4-5", "claude-opus-5-5")
    res = asyncio.run(llm.json_call("x", "sys", "p" * 100, {"type": "object"}, max_tokens=100_000))
    assert res == {"_over_budget": True}  # refused before any network call


def test_mutations_stay_in_bounds():
    rng = random.Random(1)
    cfg = StrategyConfig()
    for _ in range(500):
        cfg = cfg.mutate(rng, 0.6)
        assert 5 <= cfg.position_pct <= 50
        assert cfg.exit_score < cfg.entry_score
        assert all(-2 <= w <= 2 for w in cfg.weights().values())


def test_signals_bounded():
    rng = random.Random(3)
    p, closes = 100.0, []
    for _ in range(600):
        p *= 1 + rng.gauss(0, 0.01)
        closes.append(p)
    sig = technical_signals(closes)
    for k in ("momentum", "trend", "reversion", "breakout"):
        assert -1 <= sig[k] <= 1
    assert set(SEED_VARIANTS) >= {"Balanced"}


def test_mutation_keeps_exit_threshold_negative_side():
    rng = random.Random(5)
    neg = sum(StrategyConfig().mutate(rng).exit_score < 0 for _ in range(200))
    assert neg > 150  # default exit -0.15 must not flip positive by scaling
    assert all(StrategyConfig().mutate(rng).min_edge_pct == 0 for _ in range(50))


def test_fee_guard_blocks_quiet_coins():
    from tradingbotty.agents.base import Blackboard
    from tradingbotty.agents.team import expected_move_pct
    bb = Blackboard(tech={"BTC": {"vol": 0.0005}})
    quiet = SimpleNamespace(symbol="BTC", change_24h_pct=1.0)
    assert expected_move_pct(bb, quiet) < 6.0
    wild = SimpleNamespace(symbol="BTC", change_24h_pct=-9.0)
    assert expected_move_pct(bb, wild) >= 6.0
