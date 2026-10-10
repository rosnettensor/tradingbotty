"""Operational mode truth and the separation of the real-money books."""
import asyncio
import math
import time
import json

import pytest

from fakes import _engine, FakeFusion
from test_fastpot import _setup, _pump_candles, PUMP
from tradingbotty.operations import snapshot

STRAT = "Breakout 20/10 days, 3 slots, BTC filter 50d"


def ready_engine(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles(False))
    e.settings.simulate = False
    e.wallet = {"total": 300, "fiat": 200, "currency": "CHF", "ts": time.time()}
    e.db.set("brain", {"on": True, "strategy": STRAT})
    c = e.fast.cfg(); c.update(on=True, strategy=PUMP, chf=80); e.fast.save(c)
    return e, m


def test_global_live_strategy_switch_and_evidence_are_separate(tmp_path, monkeypatch):
    e, _ = ready_engine(tmp_path, monkeypatch)
    d = snapshot(e)
    assert d["execution_allowed"] and all(l["status"] == "armed" for l in d["lanes"])
    e.db.set("mode", "paper")
    assert all(l["status"] == "standby" for l in snapshot(e)["lanes"])
    e.db.set("mode", "live"); e.db.set("fastlab", {})
    assert snapshot(e)["lanes"][1]["status"] == "blocked"
    e.db.set("brain", {"on": False, "strategy": STRAT})
    assert snapshot(e)["lanes"][0]["status"] == "off"
    e.db.set("kill_switch", True)
    assert not snapshot(e)["execution_allowed"]


def test_bank_probe_is_real_but_needs_global_permission_and_brain(tmp_path, monkeypatch):
    e, _ = ready_engine(tmp_path, monkeypatch)
    c = e.bank.cfg(); c.update(mode="probe", report={"split": {STRAT: 1}, "probe_split": {STRAT: 1}}); e.bank.save(c)
    assert snapshot(e)["bank"]["effective"]
    assert "Echtgeld-Probe" in snapshot(e)["bank"]["label"]
    e.db.set("mode", "paper")
    assert not snapshot(e)["bank"]["effective"]


def test_conflicts_do_not_silently_change_saved_budgets(tmp_path, monkeypatch):
    e, _ = ready_engine(tmp_path, monkeypatch)
    c = e.fast.cfg(); c.update(chf=400); e.fast.save(c)
    d = snapshot(e)
    assert d["lanes"][0]["budget"] == 0 and d["conflicts"]
    assert e.fast.cfg()["chf"] == 400


def test_twelve_hour_report_excludes_simulations_tests_and_old_orders(tmp_path, monkeypatch):
    e, _ = ready_engine(tmp_path, monkeypatch)
    now = time.time(); started = now - 50000
    e.db.set("observation_started_v1", started)
    for ts, book, mode, pnl in ((now-10, "fast", "live", 5), (now-20, "test", "live", -1),
                                (now-30, "fast", "paper", 90), (now-49000, "fast", "live", 100)):
        e.db.execute("INSERT INTO trades(ts,variant_id,mode,symbol,side,qty,price,notional,fee,pnl) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (ts, book, mode, "SOL", "SELL", 1, 10, 10, .1, pnl))
    w = snapshot(e, now)["window"]
    assert w["orders"] == w["sells"] == 1 and w["realized"] == 5 and w["fees"] == .1
    assert w["complete"] and w["hours"] == 12
    restarted = _engine(tmp_path, monkeypatch, FakeFusion())
    assert restarted.db.get("observation_started_v1") == started


def test_concurrent_traders_cannot_claim_the_same_coin(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    e.set_controls({"live.max_invest": 100})
    async def run():
        return await asyncio.gather(e._live_buy("SOL", 30, "daily", book="brain"),
                                    e._live_buy("SOL", 30, "fast", book="fast"), return_exceptions=True)
    results = asyncio.run(run())
    assert len(e.live.buys) == 1 and isinstance(results[1], ValueError)
    assert "overlapping" in str(results[1])
    # Transient system-check ownership stays separate and may use BTC like either strategy.
    asyncio.run(e._live_buy("SOL", 26, "test", book="test"))
    assert len(e.live.buys) == 2


def test_stale_fast_exit_uses_a_fresh_quote_not_the_old_dashboard(tmp_path, monkeypatch):
    e, m = ready_engine(tmp_path, monkeypatch)
    c = e.fast.cfg(); c["strategy"] = e.fast.cfg()["strategy"]
    c["pos"] = {"SOL": {"entry": 10, "cost": 30, "qty": 3}}
    e.fast.save(c); e.db.set("fast_qty", {"SOL": 3}); e.db.set("fast_cost", {"SOL": 30}); m.bal["SOL"] = 3
    e.wallet["ts"] = time.time() - 300
    e.fusion_prices = {"SOL": 5}  # obsolete crash quote must not force an exit
    m.px["SOL"] = 10
    asyncio.run(e.fast.watch())
    assert not m.sells
    m.px["SOL"] = 8.9
    asyncio.run(e.fast.watch())
    assert m.sells == [("SOL", 3)]


def test_fast_settings_reject_nonfinite_values(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    for key in ("chf", "pct", "floor"):
        for value in (math.nan, math.inf):
            with pytest.raises(ValueError, match="finite"):
                asyncio.run(e.fast.set(**{key: value}))


def test_order_trace_reports_confirmed_fills_without_inventing_a_benchmark(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    asyncio.run(e._live_buy("BTC", 20, "manual decision"))
    before = e.db.query("SELECT * FROM orders")
    result = snapshot(e)["order_trace"][0]
    assert result["state"] == "filled" and result["reason"] == "manual decision"
    assert result["requested_amount"] == result["execution"]["notional"] == 20
    assert result["reference_price"] is None
    assert e.db.query("SELECT * FROM orders") == before
    assert len(e.live.buys) == 1


def test_order_trace_limits_history_and_does_not_expose_raw_broker_payload(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    for i in range(35):
        e.db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?)", (
            str(i), i, i, "brain", "BTC", "BUY", "uncertain",
            json.dumps({"reason": "x" * 600, "private": "secret"}),
            json.dumps({"error": "private exchange payload", "execution": []})))
    rows = snapshot(e)["order_trace"]
    assert len(rows) == 30 and rows[0]["id"] == "34"
    assert len(rows[0]["reason"]) == 500
    assert rows[0]["execution"]["quantity"] is None
    assert "private" not in json.dumps(rows)


def test_damaged_journal_metadata_does_not_break_read_only_dashboard(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    e.db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?)",
                 ("bad", 1, 1, "brain", "BTC", "BUY", "submitting", "not json", "[]"))
    row = snapshot(e)["order_trace"][0]
    assert row["state"] == "submitting" and row["execution"]["fee"] is None
