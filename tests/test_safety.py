"""Safety tests: the bot must never borrow, short, sell your coins without permission, or spend past its AI budget."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty import controls  # noqa: E402
from tradingbotty.config import load_settings  # noqa: E402
from tradingbotty.db import DB  # noqa: E402
from tradingbotty.llm import LLM, Budget  # noqa: E402

from fakes import FakeFusion, _engine  # noqa: E402


def test_controls_are_clamped_and_never_unlock_debt():
    assert controls.coerce("live.max_order", 9999) == 500
    assert controls.coerce("live.max_invest", -5) == 2
    for key in ("risk.allow_margin", "risk.allow_short", "paper.fee_pct"):
        with pytest.raises(ValueError):
            controls.coerce(key, True)  # not a dashboard setting at all
    raw = load_settings().raw
    controls.apply(raw, {"live.max_order": 40, "nope.nope": 1})
    assert raw["live"]["max_order"] == 40 and raw["risk"]["allow_margin"] is False


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
    budget.profit = lambda: 50.0          # 10% of the bot's real gain grows the budget
    assert budget.cap_total() == pytest.approx(6.0)


def test_kill_switch_blocks_every_real_buy(tmp_path, monkeypatch):
    f = FakeFusion()
    e = _engine(tmp_path, monkeypatch, f)
    e.set_controls({"live.max_invest": 100, "live.max_order": 50})
    e.set_kill_switch(True)
    with pytest.raises(ValueError, match="kill switch"):
        asyncio.run(e._live_buy("BTC", 20, "t"))
    assert not f.buys and "not sent" in e.risk_log[-1]["result"]


def test_caps_spread_and_cash_are_checked_on_every_buy(tmp_path, monkeypatch):
    f = FakeFusion()
    e = _engine(tmp_path, monkeypatch, f)
    e.set_controls({"live.max_invest": 30, "live.max_order": 50, "live.use_my_coins": False})
    amount, _ = asyncio.run(e._live_buy("BTC", 25, "t"))
    assert amount == 25 and e.risk_log[-1]["result"] == "sent"
    assert [c[0] for c in e.risk_log[-1]["checks"]] == ["kill switch", "cap on money in coins", "Fusion minimum",
                                                       "spread", "cash incl. fee room"]
    amount, _ = asyncio.run(e._live_buy("SOL", 25, "t"))           # only 5 left under the 30 cap
    assert amount == 5
    with pytest.raises(ValueError, match="minimum|cap"):           # cap full
        asyncio.run(e._live_buy("SOL", 25, "t"))
    e.db.set("live_cost", {})
    f.spread = 3.0
    with pytest.raises(ValueError, match="spread"):                # 3% > 1% limit
        asyncio.run(e._live_buy("BTC", 10, "t"))
    f.spread = 0.1
    f.bal["FIAT"] = 12.0
    amount, _ = asyncio.run(e._live_buy("BTC", 20, "t"))           # never more than 99.5% of the cash
    assert amount == pytest.approx(12 * 0.995) and f.bal["FIAT"] >= 0


def test_bot_only_sells_its_own_coins(tmp_path, monkeypatch):
    f = FakeFusion()
    f.bal.update({"BTC": 0.5})                                     # yours, from before the bot
    e = _engine(tmp_path, monkeypatch, f)
    e.set_controls({"live.max_invest": 100, "live.max_order": 50})
    asyncio.run(e._live_sell("BTC", "t"))                          # the bot owns no BTC: nothing sold
    assert f.sells == []
    asyncio.run(e._live_buy("BTC", 20, "t"))
    asyncio.run(e._live_sell("BTC", "t"))
    assert f.sells == [("BTC", 2.0)] and f.bal["BTC"] == pytest.approx(0.5)
    assert "BTC" not in e.db.get("live_qty")


def test_your_coins_are_only_used_when_allowed(tmp_path, monkeypatch):
    f = FakeFusion()
    f.pairs.update({"HBAR": {}, "BTC": {"minOrderAmount": "15"}})
    f.bal = {"FIAT": 2.0, "HBAR": 5.0, "VSN": 3.0}                 # HBAR worth 50; VSN has no Fusion pair
    e = _engine(tmp_path, monkeypatch, f)
    e.set_controls({"live.max_invest": 100, "live.max_order": 20, "live.use_my_coins": False})
    with pytest.raises(ValueError, match="may not sell your coins"):
        asyncio.run(e._live_buy("BTC", 20, "t"))
    assert f.sells == []
    e.set_controls({"live.use_my_coins": True})
    asyncio.run(e._live_buy("BTC", 20, "t"))
    assert f.sells and f.sells[0][0] == "HBAR" and f.bal["VSN"] == 3.0 and f.bal["FIAT"] >= -1e-9
    e._brain_target = {"SOL": 0.5}
    f.pairs["SOL"] = {}
    f.bal["SOL"] = 4.0
    spare = asyncio.run(e._spare_coins(f.bal))
    assert "SOL" not in spare and "BTC" not in spare and "VSN" not in spare  # about to be bought, the bot's, untradable


def test_three_failed_orders_stop_live_trading(tmp_path, monkeypatch):
    class Broken(FakeFusion):
        async def sell_fraction(self, symbol, fraction, owned=None):
            raise RuntimeError("503")
    f = Broken()
    e = _engine(tmp_path, monkeypatch, f)
    e.db.set("live_qty", {"BTC": 1.0, "SOL": 1.0, "ETH": 1.0})
    for s in ("BTC", "SOL", "ETH"):
        asyncio.run(e._live_sell(s, "t"))
    assert e.mode == "paper"                                       # standby: nothing trades until you switch it on
