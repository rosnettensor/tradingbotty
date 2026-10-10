"""Safety and forward signals: fake exchange only, never real account credentials."""
import asyncio
import math
import time

import httpx
import pytest

from fakes import FakeFusion, _engine
from tradingbotty import risk
from tradingbotty.brokers.fusion import FusionBroker, FusionPendingOrder
from tradingbotty.speculation import rank, paper_step


def test_risk_gate_rejects_debt_invalid_amounts_and_limits():
    for pair in ({"productType": "FUTURE"}, {"leverage": 10}, {"borrowed_funds": True},
                 {"can_create_margin": True}, {"baseAssetType": "option"}):
        with pytest.raises(ValueError):
            risk.spot_only(pair)
    for amount in (0, -1, math.nan, math.inf):
        with pytest.raises(ValueError):
            risk.approve(amount, 100, 0, set(), "BTC", 5)
    assert risk.approve(1000, 500, 0, set(), "BTC", 5) == 100
    with pytest.raises(ValueError, match="daily loss"):
        risk.approve(10, 100, 200, set(), "BTC", 5)
    with pytest.raises(ValueError, match="positions"):
        risk.approve(10, 100, 0, {"BTC"}, "SOL", 1)


def test_all_books_share_order_and_daily_loss_limits(tmp_path, monkeypatch):
    f = FakeFusion()
    f.bal["FIAT"] = 1000
    e = _engine(tmp_path, monkeypatch, f)
    e.set_controls({"live.max_order": 100, "live.max_invest": 1000})
    for book, sym in (("brain", "BTC"), ("fast", "SOL"), ("test", "BTC")):
        spent, _ = asyncio.run(e._live_buy(sym, 180, "test", book=book))
        assert spent == 100
    e.db.execute("INSERT INTO trades(ts,variant_id,mode,symbol,side,qty,price,notional,fee,pnl) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?)", (time.time(), "fast", "live", "SOL", "SELL", 1, 1, 1, 0, -200))
    for book in ("brain", "fast", "test"):
        with pytest.raises(ValueError, match="daily loss"):
            asyncio.run(e._live_buy("SOL" if book == "fast" else "BTC", 10, "test", book=book))
    # A purchase stop must still permit liquidation of owned assets.
    assert asyncio.run(e._live_sell("BTC", "exit", book="brain"))["quantity"] == 10


def test_missing_risk_marks_and_spread_fail_closed(tmp_path, monkeypatch):
    class Broken(FakeFusion):
        async def spread_pct(self, symbol):
            raise RuntimeError("offline")
    f = Broken()
    e = _engine(tmp_path, monkeypatch, f)
    with pytest.raises(ValueError, match="spread unavailable"):
        asyncio.run(e._live_buy("BTC", 5, "test"))
    e.db.set("fast_qty", {"MISSING": 1})
    e.db.set("fast_cost", {"MISSING": 10})
    with pytest.raises(ValueError, match="risk price"):
        asyncio.run(e._live_buy("BTC", 5, "test"))
    assert not f.buys


def test_partial_and_empty_sells_retain_unsold_ownership(tmp_path, monkeypatch):
    class Partial(FakeFusion):
        async def sell_fraction(self, symbol, fraction, owned=None):
            return {"execution": {"quantity": owned / 2, "notional": owned * 5, "fee": 0}}
    e = _engine(tmp_path, monkeypatch, Partial())
    e.live.bal["BTC"] = 4
    e.db.set("live_qty", {"BTC": 4})
    e.db.set("live_cost", {"BTC": 40})
    asyncio.run(e._live_sell("BTC", "exit"))
    assert e.db.get("live_qty") == {"BTC": 2}
    assert e.db.get("live_cost") == {"BTC": 20}
    async def empty(*args, **kw):
        return {"execution": {"quantity": 0}}
    e.live.sell_fraction = empty
    assert asyncio.run(e._live_sell("BTC", "exit")) is None
    assert e.db.get("live_qty") == {"BTC": 2}


def test_uncertain_submission_stops_bot_without_retry(tmp_path, monkeypatch):
    class Uncertain(FakeFusion):
        async def buy(self, symbol, amount):
            raise FusionPendingOrder({"id": "pending", "status": "new"})
    e = _engine(tmp_path, monkeypatch, Uncertain())
    with pytest.raises(FusionPendingOrder):
        asyncio.run(e._live_buy("BTC", 5, "test"))
    assert e.kill_switch and e.mode == "paper"
    assert e.db.get("unresolved_order")["order"]["id"] == "pending"
    assert not e.db.get("live_qty")


def test_broker_post_timeout_is_not_resubmitted():
    calls = []
    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("unknown outcome", request=req)
    async def run():
        b = FusionBroker("test")
        await b.client.aclose()
        b.client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))
        with pytest.raises(FusionPendingOrder):
            await b._order({"pair": "BTC-EUR", "side": "Buy"})
        await b.client.aclose()
    asyncio.run(run())
    assert len(calls) == 1


def test_forward_scan_requires_fresh_baseline_and_spot():
    tickers = [{"pair": "SOL-CHF", "price": "110", "high": "110", "low": "90", "volume": "10"}]
    pairs = {"SOL": {"pair": "SOL-CHF", "minOrderAmount": "25"}}
    assert rank(tickers, pairs, {}, 1000)[0]["momentum_pct"] is None
    prev = {"SOL": {"ts": 880, "price": 100, "high": 105}}
    r = rank(tickers, pairs, prev, 1000)[0]
    assert r["breakout"] and r["momentum_pct"] == 10
    assert rank(tickers, pairs, prev, 2000)[0]["momentum_pct"] is None
    pairs["SOL"]["leverage"] = 10
    assert rank(tickers, pairs, prev, 1000) == []


def test_paper_positions_pay_costs_exit_and_never_reenter_same_scan():
    r = {"symbol": "SOL", "price": 100, "eligible": True, "breakout": True,
         "momentum_pct": 3, "min_order": 25}
    p = paper_step({}, [r], 1000, 0.25, 0.05)
    assert p["cash"] == pytest.approx(899.75)
    assert p["equity"] < 1000
    p = paper_step(p, [{**r, "price": 50}], 1120, 0.25, 0.05)
    assert not p["positions"] and p["realized"] < -50 and len(p["trades"]) == 2
    assert not paper_step({}, [r], 1000, 0.25, 0.05, paused=True)["positions"]


def test_concurrent_buy_paths_cannot_spend_same_cash(tmp_path, monkeypatch):
    class Slow(FakeFusion):
        async def balances(self):
            result = dict(self.bal)
            await asyncio.sleep(0.01)
            return result
    f = Slow()
    e = _engine(tmp_path, monkeypatch, f)
    e.set_controls({"live.max_order": 100, "live.max_invest": 200})
    async def run():
        return await asyncio.gather(e._live_buy("BTC", 70, "first"), e._live_buy("SOL", 70, "second"))
    results = asyncio.run(run())
    assert results[0][0] == 70 and results[1][0] == pytest.approx(29.85)
    assert f.bal["FIAT"] >= 0


def test_partial_buy_books_actual_notional(tmp_path, monkeypatch):
    class Partial(FakeFusion):
        async def buy(self, symbol, amount):
            return {"execution": {"quantity": amount / 20, "price": 10, "notional": amount / 2, "fee": 0}}
    e = _engine(tmp_path, monkeypatch, Partial())
    e.set_controls({"live.max_invest": 100})
    spent, qty = asyncio.run(e._live_buy("BTC", 20, "partial"))
    assert spent == 10 and qty == 1 and e.db.get("live_cost") == {"BTC": 10}


def test_fast_pot_partial_sale_keeps_remaining_cost_and_position(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    cfg = e.fast.cfg()
    cfg["pos"] = {"SOL": {"qty": 4, "cost": 40}}
    e.fast.save(cfg)
    pnl = e.fast.booked_sell("SOL", {"quantity": 2, "notional": 24, "fee": 0})
    assert pnl == 4 and e.fast.cfg()["pos"]["SOL"] == {"qty": 2, "cost": 20}


def test_paper_loss_budget_blocks_new_entries():
    r = {"symbol": "SOL", "price": 100, "eligible": True, "breakout": True,
         "momentum_pct": 3, "min_order": 25}
    portfolio = {"trades": [{"ts": 100, "side": "SELL", "symbol": "BTC", "pnl": -200}]}
    assert not paper_step(portfolio, [r], 1000, 0.25, 0.05)["positions"]


def test_non_power_of_ten_increment_keeps_exact_precision():
    from tradingbotty.brokers.fusion import _fmt
    assert _fmt(0.25, 0.25) == "0.25"


def test_scanner_reads_fusion_and_keeps_experiment_out_of_live_orders(tmp_path, monkeypatch):
    class ScannerFusion(FakeFusion):
        def __init__(self):
            super().__init__()
            self.pairs = {"SOL": {"pair": "SOL-CHF", "minOrderAmount": "25"}}
            self.price, self.high = 100, 100
        async def tickers(self):
            return [{"pair": "SOL-CHF", "price": self.price, "high": self.high, "low": 80, "volume": 10}]
        async def liquidity(self, symbol):
            return {"spread_pct": 0.1, "depth_quote": 10000}
    f = ScannerFusion()
    e = _engine(tmp_path, monkeypatch, f)
    e.settings.simulate = False
    monkeypatch.setattr("tradingbotty.speculation.time.time", lambda: 1000)
    asyncio.run(e.speculation.scan())
    assert not e.speculation.status()["paper"]["positions"]
    f.price, f.high = 110, 110
    monkeypatch.setattr("tradingbotty.speculation.time.time", lambda: 1120)
    asyncio.run(e.speculation.scan())
    assert "SOL" in e.speculation.status()["paper"]["positions"]
    assert not f.buys and not f.sells and not e.db.query("SELECT * FROM trades")
