"""The trade diary, the Sunday report card and the fast pot's floor."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeFusion, _engine  # noqa: E402
from test_fastpot import Market  # noqa: E402
from test_phone_server import FakeHTTP  # noqa: E402


class FeeMarket(Market):
    """Market with a 0.10 fee on every order, so "after fees" really means something."""

    async def buy(self, symbol, amount):
        r = await super().buy(symbol, amount)
        r["execution"]["fee"] = 0.1
        return r

    async def sell_fraction(self, symbol, fraction, owned=None):
        r = await super().sell_fraction(symbol, fraction, owned)
        r["execution"]["fee"] = 0.1
        return r


def _setup(tmp_path, monkeypatch, m=None):
    m = m or FeeMarket()
    e = _engine(tmp_path, monkeypatch, m)
    e.wallet = {"total": 250.0, "currency": "CHF"}
    e.set_controls({"live.max_invest": 2000, "live.max_order": 300})
    return e, m


def _ntfy(e, monkeypatch):
    http = FakeHTTP()
    monkeypatch.setattr(e.prices, "client", http)
    e.settings.simulate = False
    e.settings.ntfy_topic = "tradingbotty-test"
    return http


# ---------------------------------------------------------------- trade diary
def test_diary_books_both_traders_and_closes_a_position_after_fees(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch)
    asyncio.run(e._live_buy("SOL", 40.0, "fast pot: Dip buyer", book="fast"))
    asyncio.run(e._live_buy("ETH", 30.0, "daily brain: Breakout"))
    d = e.diary()
    assert [(x["side"], x["symbol"], x["book"]) for x in d] == [("BUY", "ETH", "brain"), ("BUY", "SOL", "fast")]
    assert d[1]["amount"] == 40.0 and d[1]["reason"] == "fast pot: Dip buyer" and not d[1].get("closed")
    m.px["SOL"] = 12.5                                   # +25%
    ex = asyncio.run(e._live_sell("SOL", "fast pot: take profit", book="fast"))
    e.fast.booked_sell("SOL", ex)
    s = e.diary()[0]
    assert s["side"] == "SELL" and s["book"] == "fast" and s["closed"]
    assert s["entry_price"] == pytest.approx(10.0) and s["entry_ts"] == pytest.approx(d[1]["ts"])
    assert s["entry_reason"] == "fast pot: Dip buyer"
    # sold 4 SOL for 50.00 - 0.10 fee, paid 40.00 + 0.10 fee: 9.80 after fees
    assert s["cost"] == pytest.approx(40.1) and s["pnl"] == pytest.approx(9.8) and s["pnl_pct"] == pytest.approx(24.44, abs=0.01)
    assert s["lesson_by"] == "math" and "Gewinn +9.80 CHF" in s["lesson"]
    assert e.db.get("fast_cost") == {} and e.db.get("live_cost") == {"ETH": 30.0}   # the brain's book untouched
    m.px["ETH"] = 8.0
    asyncio.run(e._live_sell("ETH", "daily brain: exit"))
    b = e.diary()[0]
    assert b["book"] == "brain" and b["pnl"] == pytest.approx(24.0 - 0.1 - 30.1) and "Verlust" in b["lesson"]


def test_diary_asks_claude_for_a_lesson_without_blocking_the_trade(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch)
    asyncio.run(e._live_buy("SOL", 40.0, "fast pot: Dip buyer", book="fast"))
    calls = []

    async def fake_json(agent, system, prompt, schema, deep=False, max_tokens=1500, model=None):
        calls.append(max_tokens)
        await asyncio.sleep(0.01)                        # Claude is slow: the sell must not wait for it
        return {"lesson": "Der Dip wurde sauber gekauft, das Take-Profit hat geliefert.", "_cost": 0.001}
    e.llm.client = object()                              # "a key is set"
    monkeypatch.setattr(e.llm, "json_call", fake_json)
    m.px["SOL"] = 11.0

    async def go():
        await e._live_sell("SOL", "fast pot: take profit", book="fast")
        assert e.diary()[0]["lesson_by"] == "math"       # booked at once with the plain-math line
        await asyncio.sleep(0.05)
    asyncio.run(go())
    assert calls == [300]
    assert e.diary()[0]["lesson_by"] == "claude" and e.diary()[0]["lesson"].startswith("Der Dip")

    async def broke(*a, **k):
        return {"_over_budget": True}
    monkeypatch.setattr(e.llm, "json_call", broke)
    asyncio.run(e._live_buy("SOL", 40.0, "fast pot: Dip buyer", book="fast"))

    async def go2():
        await e._live_sell("SOL", "fast pot: stop", book="fast")
        await asyncio.sleep(0.02)
    asyncio.run(go2())
    assert e.diary()[0]["lesson_by"] == "math"           # over budget: the plain-math line stays


def test_diary_is_capped_and_your_coins_sold_for_cash_have_no_result(tmp_path, monkeypatch):
    e, _ = _setup(tmp_path, monkeypatch)
    e.db.set("diary", [{"id": str(i), "ts": 1.0, "book": "brain", "side": "BUY"} for i in range(300)])
    e._record("BTC", "SELL", {"quantity": 1, "price": 10, "notional": 10}, 10.0, "your coin, sold for cash (you allowed it)")
    d = e.diary()
    assert len(d) == 300 and d[0]["symbol"] == "BTC" and not d[0].get("closed") and "pnl" not in d[0]


# ---------------------------------------------------------------- Sunday report card
def _sunday(hour):
    return time.struct_time((2026, 10, 11, hour, 5, 0, 6, 284, 1))   # Sunday 11.10.2026


def test_week_grades_follow_the_documented_rule():
    from tradingbotty.engine import Engine
    g = Engine.week_grade
    assert [g(x) for x in (3, 2, 1, 0.5, 0.2, 0, -0.4, -0.5, -2, -2.1, None)] == \
        ["A", "A", "B", "B", "C", "C", "C", "D", "D", "F", "–"]


def test_weekly_report_card_numbers(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch)
    now = time.time()
    e.db.set("wallet_hist", [[now - 9 * 86400, 240.0, 0.0], [now - 6.9 * 86400, 245.0, 1.0], [now - 60, 250.0, 4.0]])
    e.wallet = {"total": 250.0, "currency": "CHF", "bot_edge": 5.0}
    e.db.executemany("INSERT OR REPLACE INTO candles(symbol, ts, close) VALUES(?,?,?)", [("BTC", int(now - 6.9 * 86400), 100.0)])
    e.prices.quotes["BTC"].price = 104.0
    asyncio.run(e._live_buy("SOL", 40.0, "fast pot: Dip buyer", book="fast"))
    m.px["SOL"] = 12.0
    asyncio.run(e._live_sell("SOL", "fast pot: take profit", book="fast"))
    asyncio.run(e._live_buy("ETH", 50.0, "daily brain: Breakout"))
    m.px["ETH"] = 9.0
    asyncio.run(e._live_sell("ETH", "daily brain: exit"))
    e.tt = {"counts": {"tested": 1200, "candidate": 2, "promising": 5}}
    e.db.set("weekly_tt", {"tested": 1000})
    r = e.weekly()
    assert r["week"] == pytest.approx(4.0) and r["pct"] == pytest.approx(1.6) and r["grade"] == "B"
    assert r["title"] == "📊 Wochen-Zeugnis · Note B" and r["btc_pct"] == pytest.approx(4.0)
    t = r["text"]
    assert "+4.00 CHF (+1.60%)" in t and "Bitcoin diese Woche: +4.00%" in t
    assert "🧠 Daily Brain: 2 Trades (1 Käufe, 1 Verkäufe)" in t and "0 gewonnen · 1 verloren" in t
    assert "⚡ Fast-Topf: 2 Trades" in t and "1 gewonnen · 0 verloren" in t
    assert "Bester Trade: SOL ⚡ +7.80 CHF" in t and "Schlechtester: ETH -5.20 CHF" in t
    assert "1200 Ideen getestet (+200 diese Woche) · 2 Kandidaten" in t and "Notenregel" in t


def test_weekly_report_goes_out_once_on_sunday_evening(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    http = _ntfy(e, monkeypatch)
    e.tt = {"counts": {"tested": 50, "candidate": 0}}
    clock = {"t": _sunday(18)}
    monkeypatch.setattr(e, "_local_now", lambda: clock["t"])
    asyncio.run(e.weekly_tick())
    assert not http.calls                                # 18:05: not yet
    clock["t"] = _sunday(19)
    asyncio.run(e.weekly_tick())
    asyncio.run(e.weekly_tick())
    assert len(http.calls) == 1 and http.calls[0][2]["title"].startswith("📊 Wochen-Zeugnis")
    assert e.db.get("weekly_tt")["tested"] == 50
    clock["t"] = time.struct_time((2026, 10, 12, 19, 5, 0, 0, 285, 1))  # Monday: nothing
    asyncio.run(e.weekly_tick())
    e.settings.raw["phone"]["weekly"] = False
    e.db.set("weekly_day", None)
    clock["t"] = _sunday(20)
    asyncio.run(e.weekly_tick())
    assert len(http.calls) == 1


def test_weekly_endpoint_preview_and_send(tmp_path, monkeypatch):
    monkeypatch.setenv("TB_SIMULATE", "1")
    monkeypatch.setenv("TB_DB", str(tmp_path / "srv.db"))
    from fastapi.testclient import TestClient
    from tradingbotty import server
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    monkeypatch.setattr(server, "engine", e)
    monkeypatch.setattr(server.settings, "password", None, raising=False)
    client = TestClient(server.app)
    r = client.get("/api/report/weekly?send=0")
    assert r.status_code in (200, 401)
    if r.status_code == 401:                             # a TB_PASSWORD in this shell: the gate is already built
        pytest.skip("password gate active")
    assert "Note" in r.json()["text"] and r.json()["sent"] is False
    assert client.post("/api/report/weekly?send=1").status_code == 400   # no phone set up
    http = _ntfy(e, monkeypatch)
    assert client.post("/api/report/weekly?send=1").json()["sent"] is True and len(http.calls) == 1
    assert client.get("/api/diary").json() == {"entries": []}


# ---------------------------------------------------------------- fast pot floor
def _pot_holding_sol(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, Market())
    e.db.set("live_qty", {"ETH": 3.0})                   # the daily brain's coin
    e.db.set("live_cost", {"ETH": 30.0})
    m.bal["ETH"] = 3.0
    c = e.fast.cfg()
    c.update(on=True, chf=40.0)
    e.fast.save(c)
    asyncio.run(e._live_buy("SOL", 40.0, "fast pot: Dip buyer", book="fast"))
    c = e.fast.cfg()
    c["pos"]["SOL"] = {"entry": 10.0, "bar": 0, "ts": time.time(), "cost": 40.0, "qty": 4.0}
    e.fast.save(c)
    return e, m


def test_fast_pot_stops_itself_below_the_floor_and_sells_only_its_own_coins(tmp_path, monkeypatch):
    e, m = _pot_holding_sol(tmp_path, monkeypatch)
    http = _ntfy(e, monkeypatch)
    assert e.fast.status()["floor"] == 30.0
    m.px["SOL"] = 8.0                                    # worth 32: above the floor, nothing happens
    bar = e.fast.cfg()
    bar["bar"] = (time.time() // 14400 - 1) * 14400      # this candle is decided already
    e.fast.save(bar)
    asyncio.run(e.fast.tick())
    assert e.fast.on() and not m.sells
    m.px["SOL"] = 6.0                                    # worth 24 < 30

    async def go():
        await e.fast.tick()
        await asyncio.sleep(0)
    asyncio.run(go())
    st = e.fast.status()
    assert not st["on"] and m.sells == [("SOL", 4.0)] and e.db.get("fast_qty") == {}
    assert st["stopped"]["value"] == pytest.approx(24.0) and st["stopped"]["floor"] == 30.0
    assert st["realized"] == pytest.approx(-16.0) and st["pot"] == pytest.approx(24.0)
    assert m.bal["BTC"] == 5.0 and m.bal["ETH"] == 3.0 and e.db.get("live_qty") == {"ETH": 3.0}  # yours and the brain's
    msg = [c for c in http.calls if c[2].get("title") == "⚡ Fast-Topf gestoppt"]
    assert len(msg) == 1 and "24.00" in msg[0][2]["message"] and "30.00" in msg[0][2]["message"]
    assert e.diary()[0]["side"] == "SELL" and e.diary()[0]["pnl"] == pytest.approx(-16.0)
    asyncio.run(e.fast.tick())                           # off: it stays off, nothing more is sold
    assert len(m.sells) == 1 and not e.fast.on()
    with pytest.raises(ValueError):                      # 24 is too small to switch on again
        asyncio.run(e.fast.set(on=True))
    st = asyncio.run(e.fast.set(on=True, chf=60.0))      # you raise the amount and press on
    assert st["on"] and st["pot"] == pytest.approx(44.0) and not st["stopped"]


def test_floor_is_editable_and_blocks_switching_on_below_it(tmp_path, monkeypatch):
    e, _ = _setup(tmp_path, monkeypatch, Market())
    st = asyncio.run(e.fast.set(floor=50, chf=40))
    assert st["floor"] == 50.0
    with pytest.raises(ValueError, match="floor"):
        asyncio.run(e.fast.set(on=True))
    st = asyncio.run(e.fast.set(on=True, floor=0))       # 0 = no floor
    assert st["on"]


def test_fast_pot_without_coins_stops_on_booked_losses(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, Market())
    c = e.fast.cfg()
    c.update(on=True, chf=40.0, realized=-12.0)          # lost 12 so far: worth 28
    e.fast.save(c)
    asyncio.run(e.fast.tick())
    assert not e.fast.on() and not m.sells and e.fast.status()["stopped"]["value"] == 28.0
