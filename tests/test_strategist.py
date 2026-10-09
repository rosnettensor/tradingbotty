"""Bauplan 2, phase 4: the strategist thinks when something happens, proposes, and sends ideas to the think tank."""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from test_fastpot import _pump_candles, _setup  # noqa: E402


def run(c):
    return asyncio.run(c)


ANSWER = {
    "diagnosis": "Zwei Breakouts verloren in einer Seitwärtsphase; das ist für die Regel normal.",
    "regime_view": "Seitwärts: Breakouts schwach, Dip-Käufe stark.",
    "actions": [{"kind": "course_bunker", "why": "Gewinne sichern"}, {"kind": "bank_probe", "why": "Mood switch testen"}],
    "ideas": [{"name": "Herzschlag", "inspiration": "Kardiologie", "theory": "Ruhepuls.", "score": "rank(neg(vol(20)))",
               "top": 3, "hold": 7, "btc_filter": True},
              {"name": "kaputt", "inspiration": "x", "theory": "y", "score": "nonsense((", "top": 1, "hold": 1, "btc_filter": False}],
    "lab_test": "Breakouts nur bei Bull-Regime.", "watch": "Bitcoin am 50-Tage-Schnitt.", "confidence": "mittel",
}


class FakeLLM:
    available = True
    last_error = None
    fast_model, deep_model = "fast", "deep"

    def __init__(self):
        self.calls = []

    async def json_call(self, agent, system, prompt, schema, **kw):
        self.calls.append((agent, prompt))
        return {**ANSWER, "_cost": 0.12}


def _strat(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    e.llm = FakeLLM()
    return e, e.agent("strategist")


def test_it_waits_for_an_event_then_thinks_once_and_feeds_the_think_tank(tmp_path, monkeypatch):
    e, s = _strat(tmp_path, monkeypatch)
    e.db.set("tt_queue", [{"name": "old"}])

    async def go():
        await s.step(e.bb)                      # first start: remembers the present
        await s.step(e.bb)
        assert not e.llm.calls
        e.db.set("diary", [{"id": "x", "ts": time.time(), "closed": True, "book": "brain", "symbol": "NEAR",
                            "pnl": -11.2, "pnl_pct": -11.2, "side": "SELL", "reason": "rule"}])
        await s.step(e.bb)
        await asyncio.sleep(0.05)               # it thinks in the background
        await s.step(e.bb)                      # the same event doesn't wake it twice
        await asyncio.sleep(0.05)
    run(go())
    assert len(e.llm.calls) == 1 and "Trade geschlossen: NEAR" in e.llm.calls[0][1]
    memo = e.strategist()["memo"]
    assert memo["ideas"] == ["Herzschlag"] and memo["ideas_dropped"] == 1      # the broken formula never reaches the lab
    assert e.db.get("tt_queue")[0]["name"] == "Herzschlag" and e.db.get("tt_queue")[0]["origin"] == "Stratege"
    assert [a["kind"] for a in memo["actions"]] == ["course_bunker", "bank_probe"]


def test_proposals_only_change_things_on_your_tap(tmp_path, monkeypatch):
    e, s = _strat(tmp_path, monkeypatch)
    assert e.stance.get()["key"] == "normal"
    run(e.strategist_act("course_bunker"))
    assert e.stance.get()["key"] == "bunker"
    try:
        run(e.strategist_act("bank_probe"))     # no bank round yet: nothing to probe
        raise AssertionError("must refuse")
    except ValueError:
        assert e.bank.mode() == "shadow"


def test_daily_cap_keeps_events_for_the_next_review(tmp_path, monkeypatch):
    from tradingbotty.agents import strategist
    e, s = _strat(tmp_path, monkeypatch)
    day = time.strftime("%Y-%m-%d", strategist._swiss())
    e.db.set("strategist", {"seen": {"closed": 0, "regime": "bull", "stages": {}, "edge": None},
                            "runs": {day: strategist.EVENT_RUNS_PER_DAY}})
    e.db.set("regime", {"label": "bear"})
    run(s.step(e.bb))
    assert not e.llm.calls and "Marktlage wechselt" in e.db.get("strategist")["pending"][0]
    s.run_now()                                  # you ask: it thinks anyway, and sees what it missed

    async def go():
        await s.step(e.bb)
        await asyncio.sleep(0.05)
    run(go())
    assert len(e.llm.calls) == 1 and "(vorher) Marktlage wechselt" in e.llm.calls[0][1]
