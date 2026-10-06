"""Your course (Kurs): Mutig, Bunkern, Pause, Normal: sizes and gain locks, never the signals."""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty import stance  # noqa: E402

from test_fastpot import PUMP, _pump_candles, _setup  # noqa: E402


def run(c):
    return asyncio.run(c)


def test_parse_reads_the_owners_words():
    p = stance.parse("heute will ich mal ein bisschen mehr risiko und grössere trades")
    assert p["stance"] == "bold" and p["today"]
    assert stance.parse("versuche möglichst viel gewinn in cash zu bunkern")["stance"] == "bunker"
    assert stance.parse("Pause bitte, keine neuen Käufe")["stance"] == "pause"
    assert stance.parse("zurück auf normal")["stance"] == "normal"
    assert stance.parse("Gewinne sichern für 6 Stunden")["hours"] == 6
    for q in ("Soll ich mutig sein?", "Wie viel Cash habe ich", "hallo"):
        assert stance.parse(q) is None, q


def test_today_ends_at_swiss_midnight():
    u = stance.until_for({"stance": "bold", "hours": None, "today": True})
    assert 0 < u - time.time() <= 24 * 3600 and stance.swiss(u).endswith("00:00")


def test_chat_proposes_and_confirm_sets_the_course(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    r = run(e.chat("Gewinne bunkern", []))
    assert r["ok"] and r["order"]["kind"] == "stance" and e.stance.get()["key"] == "normal"
    done = run(e.orders.confirm(r["order"]["token"]))
    assert done["ok"] and e.stance.get()["key"] == "bunker" and e.stance.size() == 0.5
    e.wallet["fiat"] = 200.0
    assert e.state()["stance"]["key"] == "bunker" and "Bunkern" in e.daily_report()
    assert m.buys == [] and m.sells == []                                 # setting a course trades nothing


def test_a_course_runs_out_by_itself(tmp_path, monkeypatch):
    e, _ = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    e.stance.set("bold", time.time() - 1)
    assert e.stance.get()["key"] == "normal"
    run(e.stance.tick())
    assert (e.db.get("stance") or {})["key"] == "normal"


def test_bunker_locks_a_gain_that_falls_back(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    e.db.set("live_qty", {"SOL": 3.0})
    e.db.set("live_cost", {"SOL": 30.0})
    m.bal["SOL"] = 3.0
    e.stance.set("bunker")
    e.wallet = {"total": 250.0, "currency": "CHF", "coins": [{"symbol": "SOL", "qty": 3, "cost": 30, "price": 11.2, "value": 33.6}]}
    run(e.stance.tick())
    assert m.sells == []                                                  # +12%: the lock is armed
    e.wallet["coins"] = [{"symbol": "SOL", "qty": 3, "cost": 30, "price": 10.5, "value": 30.5}]
    run(e.stance.tick())                                                  # back to +1.7%: sold, gain kept
    assert m.sells and m.sells[0][0] == "SOL" and "SOL" not in e.db.get("live_qty", {})
    assert "SOL" in e.stance.locked()


def test_fast_pot_buys_half_in_bunker_and_nothing_in_pause(tmp_path, monkeypatch):
    cd = _pump_candles()
    for key, want in (("pause", []), ("bunker", [("SOL", 30.0)])):
        e, m = _setup(tmp_path / key, monkeypatch, cd)
        c = e.fast.cfg()
        c.update(on=True, chf=60.0, strategy=PUMP)
        e.fast.save(c)
        e.stance.set(key)
        run(e.fast._decide(e.fast.cfg(), cd.days[-1]))
        assert m.buys == want, key


def _brain(tmp_path, monkeypatch, course):
    from fakes import FakeFusion, _engine
    from tradingbotty import research
    f = FakeFusion()
    f.pairs = {s: {"minOrderAmount": "25"} for s in ("BTC", "SOL", "ADA")}
    e = _engine(tmp_path, monkeypatch, f)
    f.bal["FIAT"] = 500.0
    e.set_controls({"live.max_invest": 200, "live.max_order": 100})
    e.wallet = {"total": 300.0, "currency": "CHF"}
    monkeypatch.setattr(research, "current_target", lambda cd, s: {"SOL": 0.5, "BTC": 0.5})
    e.stance.set(course)
    run(e.set_brain(True, "Breakout 20/10 days, 3 slots, BTC filter 50d"))
    run(e.brain_tick())
    return f, e


def test_brain_sizes_new_coins_by_the_course_and_pause_buys_nothing(tmp_path, monkeypatch):
    f, _ = _brain(tmp_path / "n", monkeypatch, "normal")
    assert sorted(f.buys) == [("BTC", 100.0), ("SOL", 100.0)]
    f, _ = _brain(tmp_path / "b", monkeypatch, "bunker")
    assert sorted(f.buys) == [("BTC", 50.0), ("SOL", 50.0)]
    f, e = _brain(tmp_path / "p", monkeypatch, "pause")
    assert f.buys == [] and "your course is ⏸ Pause" in " ".join(e.brain()["steps"])
    f, _ = _brain(tmp_path / "m", monkeypatch, "bold")
    assert sum(a for _, a in f.buys) == 200.0                            # bigger, but never above the 200 cap
