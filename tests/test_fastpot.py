"""The fast pot: real money for one fast rule, next to the daily brain, with its own money and coins."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty import fastlab, research  # noqa: E402

from fakes import FakeFusion, _engine  # noqa: E402

PUMP = "Pump rider: up 15%+ in 1d, 2 slots, take +20% / stop -8%, max 2d, volume 3x"  # the tests drive a pump

BAR = fastlab.BAR


class Market(FakeFusion):
    """A fake Fusion account whose prices can move."""

    def __init__(self):
        super().__init__()
        self.pairs = {s: {"minOrderAmount": "25"} for s in ("BTC", "SOL", "ETH", "XRP", "ADA", "DOGE")}
        self.px = {s: 10.0 for s in self.pairs}
        self.bal = {"FIAT": 200.0, "BTC": 5.0}             # 5 BTC (50) are the user's own

    async def buy(self, symbol, amount):
        if amount > self.bal["FIAT"] * 0.9975 + 1e-9:
            raise RuntimeError("422: The order size/value is too big.")
        q = amount / self.px[symbol]
        self.buys.append((symbol, amount))
        self.bal["FIAT"] -= amount
        self.bal[symbol] = self.bal.get(symbol, 0) + q
        return {"execution": {"quantity": q, "price": self.px[symbol], "notional": amount, "fee": 0}}

    async def sell_fraction(self, symbol, fraction, owned=None):
        held = self.bal.get(symbol, 0)
        q = (held if owned is None else min(held, owned)) * fraction
        self.sells.append((symbol, round(q, 6)))
        self.bal[symbol] -= q
        self.bal["FIAT"] += q * self.px[symbol]
        return {"execution": {"quantity": q, "price": self.px[symbol], "notional": q * self.px[symbol], "fee": 0}}

    async def prices(self):
        return dict(self.px)


def _pump_candles(pump=True, bars=420):
    """Bitcoin rising, quiet coins, and SOL jumping 20% in the last day on 3.2x its usual volume."""
    t0 = (time.time() // BAR - 1) * BAR - (bars - 1) * BAR
    rows = {}
    for s in ("BTC", "SOL", "ETH", "XRP", "ADA", "DOGE", "LINK"):
        out = []
        for k in range(bars):
            p = 100 * (1 + k * 0.001) if s == "BTC" else 10.0
            v = 1e6
            if pump and s == "SOL" and k >= bars - 6:
                p = 10.0 * (1 + 0.035 * (k - bars + 7))
                v = 3.2e6
            out.append((t0 + k * BAR, p, p * 1.001, p * 0.999, p, v))
        rows[s] = out
    return research.Candles.from_rows(rows)


def _setup(tmp_path, monkeypatch, cd):
    m = Market()
    e = _engine(tmp_path, monkeypatch, m)
    e.wallet = {"total": 250.0, "currency": "CHF"}
    # Stub a passed lab for execution tests; evidence rejection is tested separately.
    e.db.set("fastlab", {"ts": time.time(), "simulated": False, "rows": [{"name": PUMP, "robust": True}]})

    async def candles(held):
        return cd
    monkeypatch.setattr(e.fast, "_candles", candles)
    return e, m


def test_pot_needs_room_for_fusions_minimum(tmp_path, monkeypatch):
    e, _ = _setup(tmp_path, monkeypatch, _pump_candles())
    with pytest.raises(ValueError):
        asyncio.run(e.fast.set(on=True, chf=10))
    st = asyncio.run(e.fast.set(on=False, chf=40))
    assert st["pot"] == 40 and st["slots"] == 1


def test_fast_pot_buys_a_pump_and_takes_profit_without_touching_brain_or_your_coins(tmp_path, monkeypatch):
    cd = _pump_candles()
    e, m = _setup(tmp_path, monkeypatch, cd)
    e.db.set("live_qty", {"ETH": 3.0})             # the daily brain's coin
    e.db.set("live_cost", {"ETH": 30.0})
    m.bal["ETH"] = 3.0
    c = e.fast.cfg()
    c.update(on=True, chf=40.0, strategy=PUMP)
    e.fast.save(c)
    bar = cd.days[-1]
    asyncio.run(e.fast._decide(e.fast.cfg(), bar))
    assert m.buys == [("SOL", 40.0)]
    assert e.db.get("fast_qty")["SOL"] > 0 and "SOL" not in e.db.get("live_qty")
    st = e.fast.status()
    assert "SOL" in st["pos"] and st["pos"]["SOL"]["entry"] == cd.c["SOL"][-1]
    # next candle: SOL is up more than 20% from the entry: take profit
    nxt = research.Candles(cd.days + [cd.days[-1] + BAR])
    for s in cd.coins:
        p = cd.c[s][-1] * (1.25 if s == "SOL" else 1.0)
        for k, val in (("o", p), ("h", p), ("l", p), ("c", p)):
            getattr(nxt, k)[s] = getattr(cd, k)[s] + [val]
        nxt.v[s] = cd.v[s] + [1e6]
    m.px["SOL"] = 10.0 * 1.25

    async def candles2(held):
        return nxt
    monkeypatch.setattr(e.fast, "_candles", candles2)
    asyncio.run(e.fast._decide(e.fast.cfg(), nxt.days[-1]))
    assert m.sells and m.sells[-1][0] == "SOL"
    st = e.fast.status()
    assert not st["pos"] and st["realized"] == pytest.approx(10.0, abs=0.01) and st["wins"] == 1
    assert st["pot"] == pytest.approx(50.0, abs=0.01)                # gains stay in the pot: it compounds
    assert m.bal["BTC"] == 5.0 and m.bal["ETH"] == 3.0               # your coins and the brain's untouched
    rows = e.db.query("SELECT variant_id, side FROM trades WHERE symbol='SOL'")
    assert {r["variant_id"] for r in rows} == {"fast"}


def test_daily_brain_leaves_the_pots_cash_and_coins_alone(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles())
    e.set_controls({"live.max_invest": 2000, "live.max_order": 300, "live.use_my_coins": False})
    c = e.fast.cfg()
    c.update(on=True, chf=60.0, strategy=PUMP)
    e.fast.save(c)
    e.db.set("fast_qty", {"XRP": 2.0})
    e.db.set("fast_cost", {"XRP": 20.0})
    m.bal["XRP"] = 2.0
    assert e.fast.cash_reserve() == 40.0
    spent, _ = asyncio.run(e._live_buy("SOL", 300.0, "daily brain: test"))
    assert spent == pytest.approx(min(100.0, (200.0 - 40.0) * 0.995))            # 40 of the pot's cash stays free
    asyncio.run(e._brain_rebalance({}, "test"))                       # the brain wants nothing: sells only its own
    assert ("XRP", 2.0) not in m.sells and e.db.get("fast_qty") == {"XRP": 2.0}
    assert all(s != "BTC" for s, _ in m.sells)


def test_fast_buys_never_sell_your_coins_for_cash(tmp_path, monkeypatch):
    cd = _pump_candles()
    e, m = _setup(tmp_path, monkeypatch, cd)
    e.set_controls({"live.use_my_coins": True})
    m.bal["FIAT"] = 10.0
    c = e.fast.cfg()
    c.update(on=True, chf=40.0, strategy=PUMP)
    e.fast.save(c)
    asyncio.run(e.fast._decide(e.fast.cfg(), cd.days[-1]))
    assert not m.buys and not m.sells and m.bal["BTC"] == 5.0
    assert any("not bought" in x for x in e.fast.status()["steps"])


def test_wallet_counts_the_pots_coins_apart_from_yours(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles())
    monkeypatch.setattr(e.settings, "simulate", False, raising=False)
    e.db.set("fast_qty", {"XRP": 4.0})
    e.db.set("fast_cost", {"XRP": 36.0})
    m.bal["XRP"] = 4.0
    e._viewer = m
    e.live = m
    asyncio.run(e._poll_wallet())
    w = e.wallet
    assert w["fast_value"] == 40.0 and w["fast_coins"][0]["pnl"] == 4.0
    assert "XRP" not in w["yours"] and w["total"] == pytest.approx(200 + 50 + 40)


def test_deposits_and_withdrawals_are_not_the_bots_gain(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles())
    monkeypatch.setattr(e.settings, "simulate", False, raising=False)
    e._viewer = m
    e.live = m
    e.set_controls({"live.max_invest": 2000, "live.max_order": 300})
    asyncio.run(e._poll_wallet())
    assert e.wallet["bot_edge"] == 0
    asyncio.run(e._live_buy("SOL", 50.0, "daily brain: test"))       # a real bot trade: no flow
    m.px["SOL"] = 12.0                                               # it gains 10
    asyncio.run(e._poll_wallet())
    assert e.wallet["bot_edge"] == pytest.approx(10.0) and not e.db.get("flows")
    m.bal["FIAT"] += 50.0                                            # you pay in 50
    asyncio.run(e._poll_wallet())
    assert e.wallet["bot_edge"] == pytest.approx(10.0) and e.db.get("flows")[-1]["amount"] == pytest.approx(50.0)
    assert e.wallet["change"] == pytest.approx(10.0)
    m.bal["FIAT"] -= 30.0                                            # you buy 3 XRP by hand in the app
    m.bal["XRP"] = 3.0
    asyncio.run(e._poll_wallet())
    assert len(e.db.get("flows")) == 1 and e.wallet["bot_edge"] == pytest.approx(10.0)
    e.book_flow(-5.0, "test")                                         # booked directly
    asyncio.run(e._poll_wallet())
    assert e.wallet["bot_edge"] == pytest.approx(15.0)


def test_a_deposit_entered_twice_counts_once(tmp_path, monkeypatch):
    from tradingbotty.db import DB
    db = DB(tmp_path / "l.db")
    t = time.time()
    db.set("hold_start", {"ts": t - 9e4, "fiat": 130.0, "coins": {}, "values": {}})   # 30 + 50 + 50
    db.set("account_start", {"ts": t - 9e4, "total": 408.0})
    db.set("flows", [{"ts": t - 60, "amount": 50.0, "how": "entered by you"},
                     {"ts": t - 50, "amount": 50.0, "how": "entered by you"}])
    db.set("wallet_hist", [[t - 900, 308.0, 2.0], [t - 600, 358.0, 52.0], [t - 30, 358.0, -48.0]])
    e = _engine(tmp_path, monkeypatch, Market())
    assert e.db.get("hold_start")["fiat"] == 80.0 and e.db.get("account_start")["total"] == 358.0
    assert len(e.db.get("flows")) == 1
    assert [h[2] for h in e.db.get("wallet_hist")] == [2.0, 2.0, 2.0]


def test_live_exits_sell_at_the_stop_between_two_4h_candles(tmp_path, monkeypatch):
    e, m = _setup(tmp_path, monkeypatch, _pump_candles(pump=False))
    c = e.fast.cfg()
    c.update(on=True, chf=60.0)                       # default rule: dip buyer, +10% / -10%
    c["pos"] = {"SOL": {"entry": 10.0, "since": time.time(), "cost": 30.0}, "ADA": {"entry": 10.0, "since": time.time(), "cost": 30.0}}
    e.fast.save(c)
    e.db.set("fast_qty", {"SOL": 3.0, "ADA": 3.0})
    e.db.set("fast_cost", {"SOL": 30.0, "ADA": 30.0})
    m.bal.update(SOL=3.0, ADA=3.0)
    e.fusion_prices = {"SOL": 9.5, "ADA": 10.2}      # -5% and +2%: nothing yet
    asyncio.run(e.fast.watch())
    assert not m.sells
    e.fusion_prices = {"SOL": 8.95, "ADA": 11.1}     # -10.5% and +11%: both go now
    m.px.update(SOL=8.95, ADA=11.1)
    asyncio.run(e.fast.watch())
    assert {s for s, _ in m.sells} == {"SOL", "ADA"}
    st = e.fast.status()
    assert not st["pos"] and st["realized"] == pytest.approx(-3.15 + 3.3, abs=0.02)
    sell = next(d for d in e.diary() if d["symbol"] == "SOL")
    assert "Live-Stopp" in sell["why"][0][1]
    c = e.fast.cfg()
    c["live_exits"] = False
    e.fast.save(c)
    asyncio.run(e.fast.watch())                       # switched off: no more checks
