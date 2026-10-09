"""The whole real-money chain in one go, the way it runs on the server: wallet, daily brain, bank probation, fast pot
with live exits, chat orders, the Guardian, the diary, the scoreboard, the strategist, the system check and its test
round trip. Every part must hand on to the next, and the account must never see a phantom deposit."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty import bank, research, syscheck  # noqa: E402

from test_fastpot import _pump_candles, _setup  # noqa: E402
from test_strategist import FakeLLM  # noqa: E402

STRAT = "Breakout 20/10 days, 3 slots, BTC filter 50d"


def run(c):
    return asyncio.run(c)


def test_the_whole_chain(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    monkeypatch.setattr(e.settings, "simulate", False, raising=False)
    e._viewer = e.live = m
    m.bal["FIAT"] = 400.0
    e.llm = FakeLLM()
    e.set_controls({"live.max_invest": 2000, "live.max_order": 150, "live.use_my_coins": False})
    cd = research.Candles.from_rows(research.synthetic_rows(["BTC", "ETH", "SOL", "ADA", "XRP", "DOGE"], days=900))

    async def daily(force=False):
        return cd
    monkeypatch.setattr(e, "_daily_candles", daily)
    probe = bank.NEW[0]
    monkeypatch.setattr(research, "current_target",
                        lambda c, s: {"ADA": 1.0} if s.name == probe else {"SOL": 0.5, "ETH": 0.5} if s.name == STRAT else {})
    run(e._poll_wallet())
    assert e.wallet["total"] == pytest.approx(400 + 50)
    run(e.fast.set(on=True, chf=60.0))  # on the server the fast pot is on: its 60 CHF stay out of the brain's budget

    # 1. the daily brain buys its two coins
    run(e.set_brain(True, STRAT))
    run(e.brain_tick())
    assert {s for s, _ in m.buys} == {"SOL", "ETH"} and set(e.db.get("live_qty")) == {"SOL", "ETH"}
    run(e._poll_wallet())

    # 2. the bank's round, then probation: the incumbent keeps its coins, the candidate gets a small real share
    rows = [{"name": n, "robust": True, "fees2x": {"return_pct": 10}, "full": {"sharpe": 1.0, "cagr_pct": 20},
             "years_won": 6, "years_total": 9, "skill_prob": 0.9, "group": "x"} for n in [STRAT, *bank.NEW]]
    rep = e.bank.refresh(cd, {"rows": rows})
    assert rep["probe"] in bank.NEW
    monkeypatch.setattr(research, "current_target",
                        lambda c, s: {"ADA": 1.0} if s.name == rep["probe"] else {"SOL": 0.5, "ETH": 0.5} if s.name == STRAT else {})
    m.buys.clear()
    e.set_bank_mode("probe")  # what the Probe switch does: the brain decides again right away
    run(e.brain_tick())
    assert "ADA" in {s for s, _ in m.buys}
    # no spare cash: the brain trims only the slice above SOL's new share, it keeps both of its coins
    assert set(e.db.get("live_qty")) == {"SOL", "ETH", "ADA"} and any("trimmed" in x for x in e.brain()["steps"])
    ada = next(a for s, a in m.buys if s == "ADA")
    b = e.brain()                                      # the cockpit can tell which strategy bought which coin
    assert b["probe"] == rep["probe"] and b["owners"]["ADA"] == rep["probe"] and b["owners"]["SOL"] == STRAT
    assert ada >= 25  # at least Fusion's minimum: the slice above SOL's share paid for it, ETH's was too small to sell
    run(e._poll_wallet())

    # 3. the fast pot: a coin bought, then sold by the live target between two 4-hour candles
    run(e._live_buy("XRP", 30.0, "fast pot: test dip", book="fast"))
    c = e.fast.cfg()
    c["pos"] = {"XRP": {"entry": 10.0, "since": time.time(), "cost": 30.0}}
    e.fast.save(c)
    run(e._poll_wallet())
    m.px["XRP"] = 11.2
    e.fusion_prices = dict(m.px)
    run(e.fast.watch())
    assert ("XRP" not in e.db.get("fast_qty", {})) and e.fast.status()["realized"] > 2.5
    run(e._poll_wallet())

    # 4. a chat sell of a brain coin, a Guardian block that becomes a ghost
    r = run(e.chat("verkaufe SOL", []))
    assert run(e.orders.confirm(r["order"]["token"]))["ok"]
    e.ghosts.add("brain", "DOGE", "guardian", "Kauf gesperrt: hack", 50.0)
    run(e._poll_wallet())

    # 5. the strategist wakes on the closed trades and sends ideas to the think tank
    s = e.agent("strategist")

    async def think():
        await s.step(e.bb)          # first start: remembers the present
        e.db.set("diary", [{"id": "z", "ts": time.time() + 1, "closed": True, "book": "brain", "symbol": "ETH",
                            "pnl": -3.0, "pnl_pct": -3.0, "side": "SELL", "reason": "rule"}] + e.diary())
        await s.step(e.bb)
        await asyncio.sleep(0.05)
    run(think())
    memo = e.strategist()["memo"]
    assert memo and e.db.get("tt_queue")[0]["origin"] == "Stratege"
    run(e.strategist_act("course_bunker"))
    assert e.stance.get()["key"] == "bunker"

    # 6. the scoreboard sees every trader, the diary every order
    board = {r["id"]: r for r in e.scoreboard()["rows"]}
    assert board["fast"]["score"] > 2 and "you" in board and "brain" in board
    assert board["guardian"]["ghosts"] == 1 and "ai:Stratege" not in board  # the fake call cost nothing in llm_calls
    books = {d["book"] for d in e.diary()}
    assert {"brain", "fast"} <= books

    # 7. the system check: ready, and its round trip goes through without touching anyone's coins
    m.px["BTC"] = 10.0
    before = (dict(e.db.get("live_qty")), dict(e.db.get("fast_qty", {})), m.bal["BTC"])
    chk = syscheck.checks(e)
    assert chk["trade_ready"], [r for r in chk["rows"] if r["state"] == "fail"]
    out = run(syscheck.roundtrip(e))
    assert out["ok"], out
    assert (dict(e.db.get("live_qty")), dict(e.db.get("fast_qty", {})), m.bal["BTC"]) == before
    run(e._poll_wallet())

    # 8. none of it looked like money paid in or out
    assert not e.db.get("flows"), e.db.get("flows")
