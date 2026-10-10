"""Automatic entries need evidence; exits must remain available without it."""
import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest

from fakes import _engine, FakeFusion
from test_fastpot import _setup, _pump_candles, PUMP
from tradingbotty.readiness import evidence
from tradingbotty.speculation import paper_step
from tradingbotty.brokers.fusion import FusionBroker, FusionPendingOrder, _floor


def test_evidence_fails_closed_and_has_an_expiry():
    now = time.time()
    good = {"ts": now, "simulated": False, "rows": [{"name": "rule", "robust": True}]}
    assert evidence(good, "rule", now)["ready"]
    for result in ({}, {**good, "simulated": True}, {**good, "simulated": None},
                   {**good, "ts": now - 48 * 3600 - 1}, {**good, "ts": now + 1},
                   {**good, "ts": float("nan")}, {**good, "rows": [{"name": "rule", "robust": False}]}):
        assert not evidence(result, "rule", now)["ready"]
    assert not evidence(good, "other", now)["ready"]


def test_fast_rechecks_same_bar_when_lab_first_passes(tmp_path, monkeypatch):
    cd = _pump_candles()
    e, m = _setup(tmp_path, monkeypatch, cd)
    c = e.fast.cfg()
    c.update(on=True, chf=40, strategy=PUMP)
    e.fast.save(c)
    e.db.set("fastlab", {})
    asyncio.run(e.fast._decide(c, cd.days[-1]))
    assert not m.buys and not e.fast.cfg()["entry_check"]["ready"]
    e.db.set("fastlab", {"ts": time.time(), "simulated": False, "rows": [{"name": PUMP, "robust": True}]})
    monkeypatch.setattr("tradingbotty.fasttrader.time.time", lambda: cd.days[-1] + 14400 + 180)
    # Timestamp must also lie before the observed clock in this fixture.
    result = e.db.get("fastlab"); result["ts"] = cd.days[-1] + 14400
    e.db.set("fastlab", result)
    asyncio.run(e.fast.tick())
    assert m.buys == [("SOL", 40)]
    asyncio.run(e.fast.tick())
    assert len(m.buys) == 1


def test_daily_missing_evidence_blocks_buy_but_sells_unwanted_coin(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    e.wallet = {"total": 120}
    e.db.set("research", {})
    e.live.bal["BTC"] = 2
    e.db.set("live_qty", {"BTC": 2})
    e.db.set("live_cost", {"BTC": 20})
    _, steps = asyncio.run(e._brain_rebalance({"SOL": 1}, "rule", strat=SimpleNamespace(name="rule")))
    assert e.live.sells == [("BTC", 2)] and not e.live.buys
    assert any("gesperrt" in step for step in steps)


def test_paper_daily_loss_survives_truncated_journal_and_stale_marks():
    r = {"symbol": "SOL", "price": 100, "eligible": True, "breakout": True, "momentum_pct": 3, "min_order": 25}
    p = paper_step({}, [r], 1000, .25, .05)
    p = paper_step(p, [], 1120, .25, .05)
    assert p["stale_positions"] == ["SOL"]
    assert len(p["history"]) == 2 and p["fees_paid"] == .25
    p.update(daily_loss=200, loss_day=0, trades=[])
    p = paper_step(p, [{**r, "symbol": "BTC"}], 1240, .25, .05)
    assert "BTC" not in p["positions"] and p["paused"]
    p = paper_step(p, [{**r, "symbol": "BTC"}], 86401, .25, .05)
    assert "BTC" in p["positions"]


@pytest.mark.parametrize("amount", [12.345678, 0, "NaN"])
def test_broker_uses_filled_amount_and_rejects_invalid_accounting(amount):
    async def run():
        b = FusionBroker("test")
        await b.client.aclose()
        order = {"id": "1", "status": "filled", "filledQuantity": "2", "filledAveragePrice": "6.17",
                 "filledAmount": amount, "fee": {"amount": ".03"}}
        b.client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=order)))
        try:
            if amount == 12.345678:
                result = await b._order({"pair": "SOL-CHF", "side": "Buy"})
                assert result["execution"]["notional"] == amount
            else:
                with pytest.raises(FusionPendingOrder):
                    await b._order({"pair": "SOL-CHF", "side": "Buy"})
        finally:
            await b.client.aclose()
    asyncio.run(run())


def test_quantity_rounding_never_exceeds_available_coins():
    assert _floor(.29999999999999, .1) == .2
    assert _floor(.3, .1) == .3
    assert _floor(.74, .25) == .5
