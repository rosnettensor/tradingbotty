"""The system check: every link of the chain, and the real round trip that proves it."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty import syscheck  # noqa: E402

from test_fastpot import _pump_candles, _setup  # noqa: E402


def run(c):
    return asyncio.run(c)


def test_round_trip_buys_and_sells_bitcoin_in_its_own_book(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    e.db.set("live_qty", {"ETH": 3.0})
    e.db.set("live_cost", {"ETH": 30.0})
    m.bal["ETH"] = 3.0
    out = run(syscheck.roundtrip(e))
    assert out["ok"] and m.buys == [("BTC", 26.25)] and m.sells and m.sells[-1][0] == "BTC"
    assert not e.db.get("test_qty") and e.db.get("live_qty") == {"ETH": 3.0}
    assert m.bal["BTC"] == 5.0                                   # your own 5 BTC untouched
    d = e.diary()
    assert d[0]["book"] == "test" and d[0]["closed"] and "Systemcheck" in d[0]["why"][0][1]
    assert all(r["trades"] == 0 for r in e.scoreboard()["rows"] if r["id"] in ("brain", "fast"))
    assert e.scoreboard()["losses"]["n"] == 0                    # a test is not a strategy result
    rows = {r["id"]: r for r in syscheck.checks(e)["rows"]}
    assert rows["test"]["state"] == "ok" and rows["mode"]["state"] == "ok"


def test_checks_name_what_blocks_real_trading(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    e.db.set("kill_switch", True)
    e.db.set("mode", "paper")
    c = syscheck.checks(e)
    rows = {r["id"]: r for r in c["rows"]}
    assert c["overall"] == "fail" and not c["trade_ready"]
    assert rows["mode"]["state"] == "fail" and rows["kill"]["state"] == "fail" and rows["mode"]["fix"]
    try:
        run(syscheck.roundtrip(e))
        raise AssertionError("must refuse")
    except ValueError as ex:
        assert "LIVE" in str(ex)
    assert not m.buys
