"""One engine/account, atomic fills, failure recovery and migration. Fake exchange only."""
import asyncio
import json
import time
import ast
from pathlib import Path

import pytest

from fakes import _engine, FakeFusion
from tradingbotty.brokers.fusion import FusionError, FusionPendingOrder
from tradingbotty.engine import Engine
from tradingbotty.config import load_settings


def test_unknown_book_and_research_never_reach_exchange(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    for book in ("speculation", "think", "typo"):
        with pytest.raises(ValueError, match="unknown portfolio book"):
            asyncio.run(e.execution.buy("SOL", 25, "invalid", book=book))
    assert not e.live.buys


def test_standby_blocks_buys_and_sells_at_the_gateway(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    asyncio.run(e._live_buy("BTC", 20, "manual"))
    e.db.set("mode", "paper")
    with pytest.raises(ValueError, match="off"):
        asyncio.run(e._live_buy("SOL", 5, "manual"))
    assert asyncio.run(e._live_sell("BTC", "exit")) is None
    assert not e.live.sells and len(e.live.buys) == 1


def test_mode_change_during_await_is_rechecked_before_post(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    async def spread(sym):
        e.db.set("mode", "paper")
        return .1
    e.live.spread_pct = spread
    with pytest.raises(ValueError, match="off"):
        asyncio.run(e._live_buy("BTC", 20, "manual"))
    assert not e.live.buys and not e.db.query("SELECT * FROM orders")


def test_automatic_entry_rechecks_evidence_after_network_wait(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    name = e.db.get("research")["rows"][0]["name"]
    e.db.set("brain", {"on": True, "strategy": name})
    e.wallet = {"total": 100}
    async def spread(sym):
        e.db.set("research", {})
        return .1
    e.live.spread_pct = spread
    with pytest.raises(ValueError, match="evidence"):
        asyncio.run(e._live_buy("BTC", 20, "automatic", automatic=True))
    assert not e.live.buys


def test_changed_risk_limit_during_network_wait_requires_new_decision(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    async def spread(sym):
        e.set_controls({"live.max_order": 10})
        return .1
    e.live.spread_pct = spread
    with pytest.raises(ValueError, match="settings changed"):
        asyncio.run(e._live_buy("BTC", 20, "manual"))
    assert not e.live.buys and not e.db.query("SELECT * FROM orders")


def test_old_strategy_decision_is_rejected_after_settings_change(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    name = e.db.get("research")["rows"][0]["name"]
    e.db.set("brain", {"on": True, "strategy": name})
    revision = e.strategies.revision("brain")
    e.db.set("brain", {"on": True, "strategy": "different"})
    with pytest.raises(ValueError, match="settings changed"):
        asyncio.run(e._live_buy("BTC", 20, "old signal", automatic=True, revision=revision))
    assert not e.live.buys


def test_confirmed_fill_and_position_are_committed_together(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    amount, qty = asyncio.run(e._live_buy("BTC", 20, "manual"))
    row = e.db.query("SELECT * FROM orders")[0]
    assert row["state"] == "filled" and row["book"] == "brain"
    assert json.loads(row["result_json"])["execution"]["quantity"] == qty
    assert e.db.get("live_cost") == {"BTC": amount}
    assert len(e.db.query("SELECT * FROM trades")) == 1


def test_booking_failure_retains_uncertainty_and_rolls_back_positions(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    def broken_record(*args, **kwargs):
        raise RuntimeError("disk booking failed")
    monkeypatch.setattr(e, "_record", broken_record)
    with pytest.raises(RuntimeError, match="booking failed"):
        asyncio.run(e._live_buy("BTC", 20, "manual"))
    assert len(e.live.buys) == 1
    assert not e.db.get("live_qty") and not e.db.get("live_cost")
    assert e.db.query("SELECT state FROM orders")[0]["state"] == "uncertain"
    assert e.kill_switch and e.mode == "paper"
    restarted = Engine(load_settings())
    assert restarted.kill_switch and restarted.mode == "paper"
    assert restarted.db.get("unresolved_order")["order"]["local_orders"]


def test_interrupted_post_is_persisted_and_not_retried(tmp_path, monkeypatch):
    class Interrupted(FakeFusion):
        async def buy(self, symbol, amount):
            self.buys.append((symbol, amount))
            raise asyncio.CancelledError()
    e = _engine(tmp_path, monkeypatch, Interrupted())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(e._live_buy("BTC", 20, "manual"))
    assert e.db.query("SELECT state FROM orders")[0]["state"] == "uncertain"
    assert len(e.live.buys) == 1 and e.kill_switch


def test_definite_rejection_does_not_create_false_uncertainty(tmp_path, monkeypatch):
    class Rejected(FakeFusion):
        async def buy(self, *args):
            raise FusionError("minimum rejected", http_status=422)
    e = _engine(tmp_path, monkeypatch, Rejected())
    with pytest.raises(FusionError):
        asyncio.run(e._live_buy("BTC", 20, "manual"))
    assert e.db.query("SELECT state FROM orders")[0]["state"] == "rejected"
    assert not e.db.get("unresolved_order") and not e.kill_switch


def test_old_pending_order_after_restart_blocks_execution(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    e.db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?)", ("lost", time.time(), time.time(), "brain", "BTC", "BUY", "submitting", "{}", None))
    restarted = Engine(load_settings())
    assert restarted.mode == "paper" and restarted.kill_switch
    assert restarted.db.get("unresolved_order")["order"]["local_orders"][0]["id"] == "lost"


def test_fast_attribution_is_booked_once_even_if_a_caller_repeats_hook(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    asyncio.run(e._live_buy("SOL", 30, "manual", book="fast"))
    assert e.fast.cfg()["pos"]["SOL"]["qty"] == 3
    ex = asyncio.run(e._live_sell("SOL", "exit", book="fast", fraction=.5))
    before = e.fast.cfg()
    e.fast.booked_sell("SOL", ex)
    assert e.fast.cfg() == before and before["trades"] == 1
    assert before["pos"]["SOL"]["qty"] == 1.5
    assert e.db.get("fast_qty")["SOL"] == 1.5


def test_fees_are_proportional_on_partial_sells(tmp_path, monkeypatch):
    class Fees(FakeFusion):
        async def buy(self, *args):
            r = await super().buy(*args); r["execution"]["fee"] = 1.0; return r
    e = _engine(tmp_path, monkeypatch, Fees())
    e.set_controls({"live.max_invest": 100})
    asyncio.run(e._live_buy("BTC", 50, "manual"))
    asyncio.run(e._live_sell("BTC", "partial", fraction=.4))
    assert e.diary()[0]["pnl"] == -.4
    asyncio.run(e._live_sell("BTC", "rest"))
    assert e.diary()[0]["pnl"] == -.6
    assert e.diary()[0]["entry_ts"] is not None
    assert sum(t["pnl"] or 0 for t in e.db.query("SELECT pnl FROM trades")) == pytest.approx(-1)


def test_legacy_fee_charge_is_not_charged_again_after_upgrade(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    for side, qty, amount, fee, pnl in (("BUY", 5, 50, 1, None), ("SELL", 2, 20, 0, -1)):
        e.db.execute("INSERT INTO trades(ts,variant_id,mode,symbol,side,qty,price,notional,fee,pnl) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (time.time(), "brain", "live", "BTC", side, qty, 10, amount, fee, pnl))
    assert e.portfolio.fee_basis("brain", "BTC", 3) == 0


def test_disabled_fast_positions_do_not_become_daily_capital(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    e.wallet = {"total": 100}
    e.set_controls({"live.max_invest": 100})
    e.db.set("fast_qty", {"SOL": 4}); e.db.set("fast_cost", {"SOL": 40})
    assert not e.fast.on()
    assert e.portfolio.allocation()["daily"] == 58.8
    assert e.portfolio.holdings() == ({"SOL": 4}, {"SOL": 40})


def test_fast_cannot_exceed_its_allocation_via_manual_orders(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    amount, _ = asyncio.run(e._live_buy("SOL", 100, "manual", book="fast"))
    assert amount == 40
    with pytest.raises(ValueError, match="cap is full"):
        asyncio.run(e._live_buy("BTC", 10, "manual", book="fast"))


def test_two_concurrent_exits_cannot_book_one_position_twice(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    asyncio.run(e._live_buy("SOL", 30, "manual", book="fast"))
    async def run():
        return await asyncio.gather(e._live_sell("SOL", "guardian", book="fast"), e._live_sell("SOL", "strategy", book="fast"))
    result = asyncio.run(run())
    assert sum(x is not None for x in result) == 1
    assert e.fast.cfg()["trades"] == 1 and len(e.live.sells) == 1


def test_broker_mutations_have_one_owner():
    root = Path(__file__).resolve().parents[1] / "backend" / "tradingbotty"
    found = set()
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Attribute) and node.attr in {"buy", "sell_fraction"}
                    and isinstance(node.value, ast.Attribute) and node.value.attr == "live"):
                found.add(path.name)
    assert found == {"execution.py"}


def test_filled_and_canceled_is_a_final_partial_fill():
    import httpx
    from tradingbotty.brokers.fusion import FusionBroker
    async def run():
        b = FusionBroker("fake")
        await b.client.aclose()
        b.client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"id": "partial", "status": "filled-and-canceled",
                                                 "filledQuantity": "2", "filledAveragePrice": "10",
                                                 "filledAmount": "20", "fee": {"amount": ".05"}})))
        try:
            result = await b._order({"pair": "SOL-CHF", "side": "Buy"})
            assert result["execution"]["quantity"] == 2 and result["execution"]["notional"] == 20
        finally:
            await b.client.aclose()
    asyncio.run(run())
