"""Bauplan 2, phase 1: why each trade happened, ghost trades for every veto, and the scoreboard in your currency."""
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty import scoreboard  # noqa: E402

from test_fastpot import _pump_candles, _setup  # noqa: E402
from test_stance import _brain  # noqa: E402


def run(c):
    return asyncio.run(c)


def _closed(pnl_pct, cost=100.0, reason="daily brain: x no longer holds it", book="brain", **kw):
    return {"closed": True, "book": book, "pnl": cost * pnl_pct / 100, "pnl_pct": pnl_pct, "cost": cost,
            "reason": reason, "ts": time.time(), "entry_ts": time.time() - 3600 * 24, **kw}


def test_loss_check_finds_the_hit_rate_small_wins_and_big_losses_need():
    out = scoreboard.losses([_closed(1.1), _closed(-11.2), _closed(-13.7, reason="fast pot: stop (dip)"),
                             _closed(-13.8, reason="daily brain: sold by you (chat)")])
    assert out["n"] == 4 and out["won"] == 1 and out["hit_pct"] == 25
    assert out["need_pct"] == 92                       # 12.9 average loss against 1.1 average win
    assert "braucht es über 92%" in out["verdict"] and "Erst 4 Trades" in out["verdict"]
    assert {k["kind"] for k in out["by_exit"]} == {"rule", "stop", "chat"}


def test_a_blocked_buy_becomes_a_ghost_that_credits_the_guard_when_the_coin_falls(tmp_path, monkeypatch):
    e, _ = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    e.fusion_prices = {"ADA": 10.0}
    g = e.ghosts.add("fast", "ADA", "guardian", "Kauf gesperrt: hack", 30.0, tp=0.1, stop=0.1)
    assert g and e.ghosts.add("fast", "ADA", "guardian", "again", 30.0) is None   # one ghost per veto
    e.fusion_prices = {"ADA": 9.5}
    run(e.ghosts.tick())
    g = e.ghosts.all()[0]
    assert not g["closed"] and g["credit"] == round(30 * 0.05 + 30 * scoreboard.FEE, 2)   # running: -5% plus fees saved
    e.fusion_prices = {"ADA": 8.9}                     # below the rule's -10% stop: the ghost ends there
    run(e.ghosts.tick())
    g = e.ghosts.all()[0]
    assert g["closed"] and g["credit"] > 3
    row = next(r for r in e.scoreboard()["rows"] if r["id"] == "guardian")
    assert row["score"] == g["credit"]


def test_selling_by_chat_follows_the_coin_as_if_the_rule_had_kept_it(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    c = e.fast.cfg()
    c.update(on=True, chf=60.0)
    e.fast.save(c)
    e.fusion_prices = {"ADA": 10.0}
    r = run(e.chat("kaufe ADA für 30 CHF im Fast Pot", []))
    assert run(e.orders.confirm(r["order"]["token"]))["ok"]
    buy = e.diary()[0]
    assert buy["side"] == "BUY" and "per Chat gekauft" in buy["why"][0][1]
    assert any("Risk Officer" in x[1] for x in buy["why"])
    r2 = run(e.chat("verkaufe ADA", []))
    assert run(e.orders.confirm(r2["order"]["token"]))["ok"]
    sell = e.diary()[0]
    assert sell["closed"] and sell["entry_why"] == buy["why"] and "per Chat verkauft" in sell["why"][0][1]
    assert not e.ghosts.all()                          # you bought it yourself: nothing to compare with the rule
    you = next(r for r in e.scoreboard()["rows"] if r["id"] == "you")
    assert you["score"] == round(sell["pnl"], 2)       # your chat trade counts for you, not for the fast pot
    fast = next(r for r in e.scoreboard()["rows"] if r["id"] == "fast")
    assert fast["trades"] == 0


def test_brain_buys_carry_their_facts_and_a_guardian_block_becomes_a_ghost(tmp_path, monkeypatch):
    e0 = {}

    def blocked(eng):
        eng.db.set("guard", {"SOL": {"until": time.time() + 3600, "reason": "hack", "titles": ["SOL bridge hacked"],
                                     "sources": ["CoinDesk"]}})
        e0["e"] = eng
    from tradingbotty import engine as eng_mod
    orig = eng_mod.Engine.brain_tick

    async def tick(self):
        if "e" not in e0:
            blocked(self)
        return await orig(self)
    monkeypatch.setattr(eng_mod.Engine, "brain_tick", tick)
    f, e = _brain(tmp_path, monkeypatch, "normal")
    assert f.buys == [("BTC", 100.0)]
    buy = e.diary()[0]
    assert buy["why"][0][1].startswith("Strategie: Breakout 20/10") and any("Grösse" in x[1] for x in buy["why"])
    g = e.ghosts.all()
    assert len(g) == 1 and g[0]["blocker"] == "guardian" and g[0]["symbol"] == "SOL" and g[0]["amount"] == 100.0


def test_ai_agents_pay_for_their_calls_on_the_scoreboard(tmp_path, monkeypatch):
    e, _ = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    e.db.execute("INSERT INTO llm_calls(ts,agent,model,input_tokens,output_tokens,cost_usd) VALUES(?,?,?,?,?,?)",
                 (time.time(), "Think Tank", "m", 1, 1, 2.5))
    e.db.execute("INSERT INTO llm_calls(ts,agent,model,input_tokens,output_tokens,cost_usd) VALUES(?,?,?,?,?,?)",
                 (time.time(), "News Hunter", "m", 1, 1, 1.0))
    rows = {r["id"]: r for r in e.scoreboard()["rows"]}
    assert rows["ai:Think Tank"]["score"] == -2.0 and rows["ai:Think Tank"]["role"] == "Parlament"
    assert rows["guardian"]["score"] == -0.8 and "ai:News Hunter" not in rows
