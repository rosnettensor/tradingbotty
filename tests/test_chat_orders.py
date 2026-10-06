"""Orders typed in the chat bar: read by plain rules, proposed with checks, sent only with a one-time confirm."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty.chatorders import parse  # noqa: E402

from test_fastpot import _pump_candles, _setup  # noqa: E402

SYMS = {"BTC", "SOL", "ETH", "XRP", "ADA", "DOGE", "RLC", "ONE"}


def run(c):
    return asyncio.run(c)


def test_parse_reads_german_and_english_orders():
    assert parse("kaufe ADA für 30 CHF im Fast Pot", SYMS) == {"side": "BUY", "coins": ["ADA"], "amount": 30.0,
                                                               "book": "fast", "all": False}
    assert parse("buy sol for 27.5 in the fast pot", SYMS)["amount"] == 27.5
    assert parse("kauf mir 40 Franken doge", SYMS)["coins"] == ["DOGE"]
    assert parse("verkaufe RLC", SYMS) == {"side": "SELL", "coins": ["RLC"], "amount": None, "book": None, "all": False}
    assert parse("verkaufe alles im Fast Pot", SYMS)["all"] is True
    assert parse("verkaufe NEAR vom Brain", SYMS | {"NEAR"})["book"] == "brain"


def test_questions_and_chatter_are_not_orders():
    for q in ("Soll ich ADA kaufen?", "Was hat der Daily Brain heute gekauft", "Wie geht's dem Konto?",
              "why did you sell RLC", "kaufen", "hallo"):
        assert parse(q, SYMS) is None, q


def test_ten_francs_is_below_fusions_minimum(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    c = e.fast.cfg()
    c.update(on=True, chf=60.0)
    e.fast.save(c)
    r = run(e.chat("kaufe ADA für 10 Franken im Fast Pot", []))
    assert not r["ok"] and "25" in r["answer"] and "order" not in r
    assert m.buys == []


def test_buy_waits_for_confirm_then_the_pot_owns_the_coin(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    c = e.fast.cfg()
    c.update(on=True, chf=60.0)
    e.fast.save(c)
    r = run(e.chat("kaufe ADA für 30 CHF im Fast Pot", []))
    assert r["ok"] and r["order"]["side"] == "BUY" and m.buys == []   # a proposal, nothing sent yet
    assert not run(e.orders.confirm("wrong"))["ok"]
    done = run(e.orders.confirm(r["order"]["token"]))
    assert done["ok"], done
    assert m.buys == [("ADA", 30.0)]
    assert e.db.get("fast_qty")["ADA"] > 0 and "ADA" in e.fast.cfg()["pos"]
    assert e.fast.cfg()["pos"]["ADA"]["entry"] == 10.0                  # the candle price the rule watches
    assert not run(e.orders.confirm(r["order"]["token"]))["ok"]         # a token works once
    # and sell it again, with its result booked into the pot
    r2 = run(e.chat("verkaufe ADA", []))
    assert r2["order"]["book"] == "fast"
    assert run(e.orders.confirm(r2["order"]["token"]))["ok"]
    assert "ADA" not in e.db.get("fast_qty", {}) and e.fast.cfg()["trades"] == 1


def test_no_orders_on_standby_or_with_the_kill_switch(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    c = e.fast.cfg()
    c.update(on=True, chf=60.0)
    e.fast.save(c)
    e.db.set("kill_switch", True)
    r = run(e.chat("kaufe ADA für 30 CHF im Fast Pot", []))
    assert not r["ok"] and "Kill" in r["answer"]
    e.db.set("kill_switch", False)
    r = run(e.chat("kaufe ADA für 30 CHF im Fast Pot", []))
    e.db.set("mode", "paper")
    assert not run(e.orders.confirm(r["order"]["token"]))["ok"] and m.buys == []


def test_your_own_coins_are_never_sold_from_the_chat(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    r = run(e.chat("verkaufe BTC", []))                                  # the 5 BTC are the user's own
    assert not r["ok"] and "Bitpanda-App" in r["answer"] and m.sells == []
