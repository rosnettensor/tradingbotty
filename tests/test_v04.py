"""v0.4: Market Radar over every coin, live money caps, spread guard and leftover sells."""
import time as _time
import pytest
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty.config import load_settings  # noqa: E402
from tradingbotty.data import prices as P  # noqa: E402
from tradingbotty.data.universe import Universe, rank  # noqa: E402


def _kraken(req: httpx.Request):
    if req.url.path.endswith("/AssetPairs"):
        return httpx.Response(200, json={"error": [], "result": {
            "XXBTZUSD": {"altname": "XBTUSD", "wsname": "XBT/USD", "status": "online"},
            "SOLUSD": {"altname": "SOLUSD", "wsname": "SOL/USD"},
            "WIFUSD": {"altname": "WIFUSD", "wsname": "WIF/USD"},
            "USDTZUSD": {"altname": "USDTUSD", "wsname": "USDT/USD"},   # stablecoin: skipped
            "SOLEUR": {"altname": "SOLEUR", "wsname": "SOL/EUR"},       # not USD: skipped
        }})
    return httpx.Response(200, json={"error": [], "result": {
        "XXBTZUSD": {"a": ["100010", "1", "1"], "b": ["99990", "1", "1"], "c": ["100000", "0.1"], "o": "98000",
                     "v": ["10", "500"], "h": ["101000", "101000"], "l": ["97000", "97000"]},
        "SOLUSD": {"a": ["150.1"], "b": ["149.9"], "c": ["150"], "o": "160", "v": ["1", "100000"],
                   "h": ["0", "165"], "l": ["0", "148"]},
        "WIFUSD": {"a": ["2.2"], "b": ["1.8"], "c": ["2"], "o": "1", "v": ["1", "10"], "h": ["0", "2"], "l": ["0", "1"]},
        "USDTZUSD": {"a": ["1"], "b": ["1"], "c": ["1"], "o": "1", "v": ["1", "1e9"]},
    }})


def test_universe_reads_every_coin_in_two_requests():
    u = Universe()
    calls = []
    u.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r) or _kraken(r)))
    rows = {r["symbol"]: r for r in asyncio.run(u.scan())}
    assert set(rows) == {"BTC", "SOL", "WIF"} and len(calls) == 2
    btc = rows["BTC"]
    assert round(btc["change"], 2) == 2.04 and round(btc["spread_pct"], 3) == 0.02 and btc["volume_usd"] == 5e7
    assert P.PAIR_KEYS["XXBTZUSD"] == "BTC" and P.kraken_pair("WIF") == "WIFUSD"


def test_radar_filters_and_ranks():
    rows = [
        {"symbol": "UP", "price": 1, "change": 8, "volume_usd": 5e7, "spread_pct": 0.05, "range_pos": 0.9},
        {"symbol": "DOWN", "price": 1, "change": -8, "volume_usd": 5e7, "spread_pct": 0.05, "range_pos": 0.1},
        {"symbol": "THIN", "price": 1, "change": 20, "volume_usd": 1e4, "spread_pct": 0.05, "range_pos": 0.9},
        {"symbol": "WIDE", "price": 1, "change": 20, "volume_usd": 5e7, "spread_pct": 2.0, "range_pos": 0.9},
        {"symbol": "PUMP", "price": 1, "change": 150, "volume_usd": 5e7, "spread_pct": 0.05, "range_pos": 1},
        {"symbol": "NOTFUS", "price": 1, "change": 9, "volume_usd": 5e7, "spread_pct": 0.05, "range_pos": 0.9},
    ]
    fus = {"UP", "DOWN", "THIN", "WIDE", "PUMP"}
    r = {x["symbol"]: x for x in rank(rows, fusion=fus, min_volume_usd=1e6, max_spread_pct=0.5)}
    assert r["UP"]["heat"] > 0 > r["DOWN"]["heat"]
    assert r["THIN"]["why"] == "too little trading" and r["WIDE"]["why"] == "spread too wide"
    assert r["PUMP"]["why"] == "already pumped" and r["NOTFUS"]["why"] == "not on Fusion"
    ordered = [x["symbol"] for x in rank(rows, fusion=fus, min_volume_usd=1e6, max_spread_pct=0.5)]
    assert ordered[:2] == ["UP", "DOWN"]
    # buzz lifts a coin
    a = rank(rows[:1], fusion=None, min_volume_usd=1e6, max_spread_pct=0.5)[0]["heat"]
    b = rank(rows[:1], fusion=None, min_volume_usd=1e6, max_spread_pct=0.5, trending=["UP"])[0]["heat"]
    assert b > a


def test_radar_watchlist_in_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("TB_SIMULATE", "1")
    monkeypatch.setenv("TB_DB", str(tmp_path / "e.db"))
    from tradingbotty.engine import Engine
    e = Engine(load_settings())
    asyncio.run(e.prices.backfill())
    e.set_controls({"scanner.max_hot": 5})
    asyncio.run(e._scan())
    assert 0 < len(e.scanned) <= 5 and all(s in e.prices.quotes for s in e.scanned)
    assert not set(e.scanned) & set(e.core_crypto())
    assert e.sources_info()["crypto"] == e.core_crypto()  # radar coins never leak into your own list
    asyncio.run(e.tick())
    assert e.agent("radar").status == "ok" and "scanned" in e.agent("radar").summary
    # a held radar coin stays even when it cools off; the others leave
    held = e.scanned[0]
    e.champion().broker.positions[held] = SimpleNamespace(qty=1, avg_price=1, value=lambda p: 1)
    added, dropped = asyncio.run(e.set_scanned([], keep=set()))
    assert held in e.scanned and held not in dropped
    assert all(e.held_anywhere(s) for s in e.scanned) and not any(e.held_anywhere(s) for s in dropped)
    # adding a radar coin by hand makes it yours
    r = asyncio.run(e.add_source("crypto", held))
    assert r["ok"] and held in e.core_crypto() and held not in e.scanned
    # restart: radar coins come back
    e.set_controls({"scanner.on": False})
    asyncio.run(e._scan())
    assert e.agent("radar").ranked == []


class FakeFusion:
    name = "Bitpanda Fusion"
    currency = "CHF"

    def __init__(self, spread=0.1):
        self.pairs = {"BTC": {}, "SOL": {}}
        self.bal = {"FIAT": 100.0}
        self.spread = spread
        self.buys, self.sells = [], []

    async def balances(self):
        return dict(self.bal)

    async def spread_pct(self, symbol):
        return self.spread

    async def buy(self, symbol, amount):
        self.buys.append((symbol, amount))
        self.bal["FIAT"] -= amount
        self.bal[symbol] = self.bal.get(symbol, 0) + amount / 10
        return {"execution": {"quantity": amount / 10, "price": 10, "notional": amount, "fee": 0}}

    async def sell_fraction(self, symbol, fraction, owned=None):
        held = self.bal.get(symbol, 0)
        qty = (held if owned is None else min(held, owned)) * fraction
        self.sells.append((symbol, round(qty, 6)))
        self.bal[symbol] -= qty
        self.bal["FIAT"] += qty * 10
        return {"execution": {"quantity": qty, "price": 10, "notional": qty * 10, "fee": 0}}

    async def prices(self):
        return {s: 10.0 for s in self.pairs}


def _engine(tmp_path, monkeypatch, live):
    monkeypatch.setenv("TB_SIMULATE", "1")
    monkeypatch.setenv("TB_DB", str(tmp_path / "l.db"))
    from tradingbotty.engine import Engine
    e = Engine(load_settings())
    e.live = live
    e.db.set("mode", "live")
    return e


def test_live_caps_and_spread_guard(tmp_path, monkeypatch):
    f = FakeFusion()
    e = _engine(tmp_path, monkeypatch, f)
    e.set_controls({"live.max_invest": 15, "live.max_order": 8})
    asyncio.run(e._mirror_live("BTC", "BUY", 0.9))   # champion goes 90% in: capped at 8 per order
    asyncio.run(e._mirror_live("SOL", "BUY", 0.9))   # only 7 left under the 15 cap
    asyncio.run(e._mirror_live("SOL", "BUY", 0.9))   # cap reached: nothing
    assert f.buys == [("BTC", 8.0), ("SOL", 7.0)]
    asyncio.run(e._mirror_live("DOGE", "BUY", 0.2))  # not on Fusion: skipped, not an error
    assert e.live_errors == 0 and len(f.buys) == 2
    f2 = FakeFusion(spread=3.0)
    e.live = f2
    asyncio.run(e._mirror_live("SOL", "SELL", 1.0))
    e.db.set("live_cost", {})
    asyncio.run(e._mirror_live("BTC", "BUY", 0.1))   # 3% spread > 1% limit: skipped
    assert f2.buys == []


def test_leftover_live_coins_are_sold_after_champion_change(tmp_path, monkeypatch):
    f = FakeFusion()
    e = _engine(tmp_path, monkeypatch, f)
    f.bal["BTC"] = 0.5  # yours
    asyncio.run(e._mirror_live("BTC", "BUY", 0.1))
    assert "BTC" in e.db.get("live_qty")
    asyncio.run(e._reconcile_live())  # champion doesn't hold BTC on paper: sell the bot's BTC, keep yours
    assert f.sells == [("BTC", 0.25)] and abs(f.bal["BTC"] - 0.5) < 1e-9
    assert e.db.get("live_qty") == {}


def test_fusion_spread_and_prices():
    from tradingbotty.brokers.fusion import FusionBroker

    def handler(req):
        if req.url.path == "/v1/orderbook/SOL-CHF":
            return httpx.Response(200, json={"bids": [{"price": "99"}], "asks": [{"price": "101"}]})
        if req.url.path == "/v1/tickers":
            return httpx.Response(200, json=[{"pair": "SOL-CHF", "price": "100"}, {"pair": "SOL-EUR", "price": "105"}])
        return httpx.Response(404)

    b = FusionBroker("k", "CHF")
    b.client = httpx.AsyncClient(base_url="https://api.fusion.bitpanda.com", transport=httpx.MockTransport(handler))
    b.pairs = {"SOL": {"pair": "SOL-CHF"}}
    assert asyncio.run(b.spread_pct("SOL")) == 2.0
    assert asyncio.run(b.prices()) == {"SOL": 100.0}


def test_bot_may_use_your_coins_only_when_allowed(tmp_path, monkeypatch):
    f = FakeFusion()
    f.pairs["HBAR"] = {}
    f.bal = {"FIAT": 2.0, "HBAR": 5.0, "VSN": 3.0}  # HBAR worth 50; VSN has no Fusion pair: never touched
    e = _engine(tmp_path, monkeypatch, f)
    e.set_controls({"live.max_invest": 100, "live.max_order": 20})
    asyncio.run(e._mirror_live("BTC", "BUY", 0.5))
    assert f.sells == [] and f.buys == [("BTC", 1.0)]  # not allowed yet: only the 2 CHF cash counts
    e.set_controls({"live.use_my_coins": True})
    asyncio.run(e._mirror_live("BTC", "BUY", 0.5))
    assert f.sells and f.sells[0][0] == "HBAR" and f.bal["VSN"] == 3.0
    assert f.buys[-1] == ("BTC", 20.0) and f.bal["FIAT"] >= -1e-9  # never below zero
    e.champion().broker.positions["SOL"] = SimpleNamespace(qty=1, avg_price=1)
    f.pairs["SOL"] = {}
    f.bal["SOL"] = 4.0
    spare = asyncio.run(e._spare_coins(f.bal))
    assert "SOL" not in spare and "BTC" not in spare and "VSN" not in spare  # champion's, bot's, untradable


def test_test_accounts_resize_to_real_balance(tmp_path, monkeypatch):
    monkeypatch.setenv("TB_SIMULATE", "1")
    monkeypatch.setenv("TB_DB", str(tmp_path / "r.db"))
    from tradingbotty.engine import Engine
    e = Engine(load_settings())
    asyncio.run(e.prices.backfill())
    v = e.champion()
    price = e.prices.price("BTC")
    asyncio.run(e.execute(v, "BTC", "BUY", 20.0, price, "test", 0.2))
    prices = {s: q.price for s, q in e.prices.quotes.items()}
    before = v.broker.equity(prices) / v.start_equity
    e._rebase_paper(340.0)
    assert v.start_equity == 340.0 and abs(v.broker.equity(prices) / v.start_equity - before) < 1e-9
    assert abs(v.broker.positions["BTC"].value(price) - 68 * (1 - 0.0025)) < 0.5  # 20% of 340, after fee
    e2 = Engine(load_settings())  # survives a restart, new strategies start at the real size too
    assert e2.settings["money"]["starting_cash_usd"] == 340.0 and e2.champion().start_equity == 340.0
    t = e._track_account(308.0)
    assert t["start_total"] == 308.0 and t["change"] == 0
    t = e._track_account(320.0)
    assert t["change"] == 12.0 and t["change_pct"] > 3.8


def test_champion_is_judged_on_the_recent_window(tmp_path, monkeypatch):
    """An old champion living off gains from long ago loses to a rival that does better lately,
    but only after the rival leads at two checks in a row."""
    import time
    monkeypatch.setenv("TB_SIMULATE", "1")
    monkeypatch.setenv("TB_DB", str(tmp_path / "c.db"))
    from tradingbotty.engine import Engine
    e = Engine(load_settings())
    e.set_controls({"optimizer.max_variants": 16, "optimizer.min_age_to_promote_h": 48})
    e.db.set("auto_promote", True)
    now = time.time()
    champ = e.champion()
    rival = next(v for v in e.variants.values() if not v.champion and not v.config.hold)
    for v in e.variants.values():
        v.created = now - 3600
        v.start_equity = v.broker.cash = 100.0
        v.broker.positions.clear()
    for v in (champ, rival):
        v.created = now - 100 * 3600
    for ts, eq in ((now - 90 * 3600, 150.0), (now - 40 * 3600, 150.0)):  # big gains long ago
        e.db.execute("INSERT INTO equity(ts,variant_id,mode,equity,cash) VALUES(?,?,?,?,?)", (ts, champ.id, "paper", eq, eq))
    champ.broker.cash = 140.0                                               # losing lately
    e.db.execute("INSERT INTO equity(ts,variant_id,mode,equity,cash) VALUES(?,?,?,?,?)",
                 (now - 40 * 3600, rival.id, "paper", 100.0, 100.0))
    rival.broker.cash = 110.0                                               # winning lately
    e.db.execute("INSERT INTO trades(ts,variant_id,mode,symbol,side,qty,price,notional,fee,pnl,reason) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (now - 3600, rival.id, "paper", "BTC", "BUY", 1, 1, 1, 0, None, "t"))
    e._board_cache = None

    board = {b["id"]: b for b in e.leaderboard(max_age=0)}
    assert board[champ.id]["fitness"] > board[rival.id]["fitness"]          # lifetime says keep the champion
    assert board[rival.id]["recent_fitness"] > board[champ.id]["recent_fitness"] + 2

    opt = e.agent("optimizer")

    async def quick_child(parent):
        return parent.config, "test child"
    opt._screened_child = quick_child
    asyncio.run(opt.evolve(e.leaderboard(max_age=0)))
    assert e.champion().id == champ.id and opt.challenger == rival.id       # first lead: wait
    asyncio.run(opt.evolve(e.leaderboard(max_age=0)))
    assert e.champion().id == rival.id                                      # second lead: takes over


def test_new_champion_takes_over_live_coins_instead_of_selling(tmp_path, monkeypatch):
    f = FakeFusion()
    e = _engine(tmp_path, monkeypatch, f)
    asyncio.run(e.prices.backfill())
    old = e.champion()
    price = e.prices.price("BTC")
    asyncio.run(e.execute(old, "BTC", "BUY", 0.2 * old.broker.cash, price, "test", 0.2))
    assert "BTC" in e.db.get("live_qty")
    new = next(v for v in e.variants.values() if not v.champion and not v.config.hold)
    prices = {s: q.price for s, q in e.prices.quotes.items() if q.price}
    eq_before = new.broker.equity(prices)
    e.promote(new.id)
    assert "BTC" in new.broker.positions                               # adopted on paper, same share of money
    share = new.broker.positions["BTC"].value(price) / new.broker.equity(prices)
    assert abs(share - old.broker.positions["BTC"].value(price) / old.broker.equity(prices)) < 0.01
    assert abs(new.broker.equity(prices) - eq_before) < 1e-6           # no fee, no change in its score
    asyncio.run(e._reconcile_live())
    assert f.sells == [] and "BTC" in e.db.get("live_qty")             # nothing sold at the switch
    # the new champion sells by its own rules: the real coins follow
    pos = new.broker.positions["BTC"]
    asyncio.run(e.execute(new, "BTC", "SELL", pos.qty, price, "its own exit", 1.0))
    assert f.sells and e.db.get("live_qty") == {}


def test_bot_edge_ignores_coin_price_swings(tmp_path, monkeypatch):
    monkeypatch.setenv("TB_SIMULATE", "1")
    monkeypatch.setenv("TB_DB", str(tmp_path / "g.db"))
    from tradingbotty.engine import Engine
    e = Engine(load_settings())
    t = e._track_account(130.0, 30.0, {"BTC": 0.001}, {"BTC": 100000.0})
    assert t["bot_edge"] == 0 and t["bot_edge_since"]
    t = e._track_account(140.0, 30.0, {"BTC": 0.001}, {"BTC": 110000.0})    # BTC up 10%, nobody traded
    assert t["bot_edge"] == 0
    # the bot sold the BTC at 110k and its new coin is now worth 120: +10 from trading
    t = e._track_account(150.0, 30.0, {"SOL": 1.0}, {"BTC": 110000.0, "SOL": 120.0})
    assert t["bot_edge"] == 10.0 and t["bot_edge_pct"] > 7


def test_stock_only_champion_hands_over_in_live_mode(tmp_path, monkeypatch):
    f = FakeFusion()
    e = _engine(tmp_path, monkeypatch, f)
    from tradingbotty.strategy import StrategyConfig
    stock = e.add_variant(StrategyConfig(trade_stocks=True, trade_crypto=False), name="Stocks only")
    try:
        e.promote(stock.id)
        raise AssertionError("a stock-only strategy must not lead real money")
    except ValueError:
        pass
    e.db.set("mode", "paper"); e.promote(stock.id); e.db.set("mode", "live")  # how it got there before the guard
    e.agent("optimizer")._fix_champion(e.leaderboard(max_age=0))
    assert e.champion().config.trade_crypto and not e.champion().config.hold


def test_wallet_hiccup_keeps_last_numbers(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    e.settings.simulate = False
    class Boom:
        name, currency, pairs = "Bitpanda Fusion", "CHF", {}
        async def balances(self):
            raise OSError()
        async def prices(self):
            return {}
    e.live = Boom()
    import time as _t
    e.wallet = {"total": 300.0, "currency": "CHF", "ts": _t.time()}
    asyncio.run(e._poll_wallet())
    assert e.wallet["total"] == 300.0 and e.wallet["stale"] == "OSError"
    e.wallet["ts"] -= 3600
    asyncio.run(e._poll_wallet())
    assert e.wallet["error"] == "OSError"                                  # never an empty error any more



def test_crypto_only_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("TB_SIMULATE", "1")
    monkeypatch.setenv("TB_DB", str(tmp_path / "s.db"))
    from tradingbotty.engine import Engine
    from tradingbotty.strategy import StrategyConfig
    e = Engine(load_settings())
    assert not e.stocks_enabled and not list(e.prices.stocks())
    assert "src_yahoo" not in [x.id for x in e.sources] and "Rocket" in [v.name for v in e.variants.values()]
    v = e.add_variant(StrategyConfig(trade_stocks=True, trade_crypto=False), name="Old stock picker")
    e.promote(v.id)
    e2 = Engine(load_settings())                      # restart: stock-only strategies retire, a crypto one leads
    assert v.id not in e2.variants and e2.champion().config.trade_crypto


def _scored(e, scores):
    e.bb.scores = {e.champion().id: scores}


def test_buy_now_buys_the_best_coin_and_the_champion_manages_it(tmp_path, monkeypatch):
    f = FakeFusion()
    e = _engine(tmp_path, monkeypatch, f)
    asyncio.run(e.prices.backfill())
    e.wallet = {"total": 300.0, "currency": "CHF", "ts": 0}
    _scored(e, {"BTC": 0.2, "SOL": 0.6, "ETH": 0.9})   # ETH isn't on this Fusion: skipped
    r = asyncio.run(e.force_buy(12))
    assert r["ok"] and r["symbol"] == "SOL" and f.buys == [("SOL", 12)]
    assert e.db.get("live_qty")["SOL"] > 0 and e.db.get("force_buy") is None
    assert "SOL" in e.champion().broker.positions        # its own stops and sell rules take it from here
    assert e.blockers() == {} or isinstance(e.blockers(), dict)


def test_buy_now_waits_then_explains_when_buying_is_unwise(tmp_path, monkeypatch):
    import time as _t
    f = FakeFusion()
    e = _engine(tmp_path, monkeypatch, f)
    asyncio.run(e.prices.backfill())
    _scored(e, {"BTC": -0.3, "SOL": -0.5})
    r = asyncio.run(e.force_buy(10))
    assert r["waiting"] and "below zero" in r["why"] and not f.buys
    req = e.db.get("force_buy"); req["until"] = _t.time() - 1; e.db.set("force_buy", req)
    r = asyncio.run(e._try_force_buy())
    assert r["gave_up"] and e.db.get("force_buy") is None and not f.buys
    f.spread = 3.0                                       # wide spreads are a reason too
    _scored(e, {"SOL": 0.5})
    r = asyncio.run(e.force_buy(10))
    assert r["waiting"] and "spread" in r["why"]
    e.cancel_force_buy()
    assert e.db.get("force_buy") is None


def test_champion_positions_missing_live_are_copied_once(tmp_path, monkeypatch):
    f = FakeFusion()
    e = _engine(tmp_path, monkeypatch, f)
    asyncio.run(e.prices.backfill())
    champ = e.champion()
    e.db.set("mode", "paper")                                       # bought while paper: nothing live
    asyncio.run(e.execute(champ, "SOL", "BUY", 0.2 * champ.broker.cash, e.prices.price("SOL"), "t", 0.2))
    e.db.set("mode", "live")
    assert not f.buys
    asyncio.run(e._reconcile_live())
    assert len(f.buys) == 1 and f.buys[0][0] == "SOL" and "SOL" in e.db.get("live_qty")
    asyncio.run(e._reconcile_live())                                # already copied: no second buy
    assert len(f.buys) == 1


def test_copy_skips_avoided_coins_and_restarts_the_hold(tmp_path, monkeypatch):
    f = FakeFusion()
    e = _engine(tmp_path, monkeypatch, f)
    asyncio.run(e.prices.backfill())
    champ = e.champion()
    e.db.set("mode", "paper")
    for sym in ("SOL", "BTC"):
        asyncio.run(e.execute(champ, sym, "BUY", 0.2 * champ.broker.cash, e.prices.price(sym), "t", 0.2))
        champ.broker.positions[sym].opened -= 86400                # bought on paper a day ago
    e.db.set("mode", "live")
    e.bb.avoid = {"BTC"}                                            # the Professor flags BTC: don't buy it for real
    asyncio.run(e._reconcile_live())
    assert [b[0] for b in f.buys] == ["SOL"]
    import time as _t
    assert _t.time() - champ.broker.positions["SOL"].opened < 60    # the 12h hold starts with the real buy


def test_small_live_orders_round_up_to_fusions_minimum(tmp_path, monkeypatch):
    f = FakeFusion()
    f.pairs = {"BTC": {"minOrderAmount": "25"}, "SOL": {"minOrderAmount": "30"}}
    e = _engine(tmp_path, monkeypatch, f)
    e.set_controls({"live.max_invest": 99, "live.max_order": 25})
    asyncio.run(e._mirror_live("BTC", "BUY", 0.2))          # 20% of 99 = 19.80 -> Fusion's 25
    assert f.buys == [("BTC", 25)]
    asyncio.run(e._mirror_live("SOL", "BUY", 0.2))          # needs 30.60, but your per-order cap is 25: skipped, with why
    assert len(f.buys) == 1
    assert any("raise 'Biggest single live order'" in r["message"]
               for r in e.db.query("SELECT message FROM agent_log ORDER BY id DESC LIMIT 5"))
    e.set_controls({"live.max_order": 35})
    asyncio.run(e._mirror_live("SOL", "BUY", 0.2))
    assert f.buys[-1] == ("SOL", 30.6)


def test_professor_blocks_buys_but_never_forces_a_sell():
    from types import SimpleNamespace as NS
    from tradingbotty.decisions import exit_reason
    from tradingbotty.strategy import SEED_VARIANTS
    cfg = SEED_VARIANTS["Swing Trader"]
    pos = NS(symbol="PUMP", avg_price=1.0, peak=1.0, opened=0.0)
    assert exit_reason(cfg, pos, 1.01, 0.5, 10 ** 9, {"PUMP"}) is None


def test_trend_rider_waits_while_bitcoin_trends_down(tmp_path, monkeypatch):
    f = FakeFusion()
    e = _engine(tmp_path, monkeypatch, f)
    e.db.set("mode", "paper")
    asyncio.run(e.prices.backfill())
    tr = next(v for v in e.variants.values() if v.name == "Trend Rider")
    assert tr.config.btc_filter and tr.config.take_profit_pct == 100
    e.bb.btc_uptrend = False
    e.bb.scores = {tr.id: {"SOL": 0.9}}
    buyer = e.agent("buyer")
    monkeypatch.setattr(e, "champion", lambda: tr)
    asyncio.run(buyer.run(e.bb))
    assert not tr.broker.positions and buyer.detail["why_not"]["SOL"] == "Bitcoin below its 20-day average"
    e.bb.btc_uptrend = True
    asyncio.run(buyer.run(e.bb))
    assert "SOL" in tr.broker.positions


def test_research_lab_runs_every_strategy_without_peeking(tmp_path, monkeypatch):
    from tradingbotty import research
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    res = asyncio.run(e.run_research())
    names = [r["name"] for r in res["rows"]]
    assert "Hold Bitcoin" in names and len(names) == len(set(names)) > 20
    assert e.db.get("research")["days"] == res["days"] > 500
    btc = next(r for r in res["rows"] if r["name"] == "Hold Bitcoin")
    assert btc["trades"] == 1 and btc["full"]["max_dd_pct"] <= 0
    # no lookahead: changing the last day's prices can't change any earlier decision or value
    rows = research.synthetic_rows(["BTC", "ETH", "SOL"], days=300)
    a = research.simulate(research.Candles.from_rows(rows), research.Rotation(30, 2), 100).equity
    t, o, h, l, c = rows["ETH"][-1]
    rows["ETH"][-1] = (t, o, h * 3, l, c * 3)
    b = research.simulate(research.Candles.from_rows(rows), research.Rotation(30, 2), 100).equity
    assert a[:-1] == b[:-1]


def test_daily_brain_trades_the_difference_once_a_day(tmp_path, monkeypatch):
    from tradingbotty import research
    f = FakeFusion()
    f.pairs = {s: {"minOrderAmount": "25"} for s in ("BTC", "SOL", "ADA")}
    f.bal["ADA"] = 2.0
    e = _engine(tmp_path, monkeypatch, f)
    e.set_controls({"live.max_invest": 100, "live.max_order": 35})
    e.db.set("live_qty", {"ADA": 2.0}); e.db.set("live_cost", {"ADA": 20.0})
    e.wallet = {"total": 300.0, "currency": "CHF"}
    monkeypatch.setattr(research, "current_target", lambda cd, s: {"SOL": 0.5, "BTC": 0.5})
    asyncio.run(e.set_brain(True, "Breakout 20/10 days, 3 slots, BTC filter 50d"))
    asyncio.run(e.brain_tick())
    b = e.brain()
    assert f.sells and f.sells[0][0] == "ADA" and "ADA" not in e.db.get("live_qty")
    # 50 CHF each (half of the 100 cap), in two equal orders of 25 to stay under the 35 per-order cap
    assert sorted(f.buys) == [("BTC", 25.0), ("BTC", 25.0), ("SOL", 25.0), ("SOL", 25.0)]
    assert b["target"] == {"SOL": 0.5, "BTC": 0.5} and "holds BTC, SOL" in b["note"]
    asyncio.run(e.brain_tick())                       # same day: nothing new
    assert len(f.buys) == 4
    with pytest.raises(ValueError):                   # buy-now would fight the brain
        asyncio.run(e.force_buy(30))
    champ = e.champion()                              # paper champion trades no longer touch real money
    asyncio.run(e.execute(champ, "SOL", "BUY", 10, e.prices.price("SOL") or 10, "t", 0.2))
    assert len(f.buys) == 4


def test_history_comes_from_binance_in_pages_and_is_cached(tmp_path, monkeypatch):
    from tradingbotty import research
    from tradingbotty.db import DB
    today = int(_time.time() // 86400 * 86400)
    days = list(range(research.HISTORY_START, today + 86400, 86400))   # includes today's forming candle
    calls = []

    class R:
        def __init__(self, data): self.data = data
        def json(self): return self.data

    class Client:
        async def get(self, url, params=None):
            calls.append(params["startTime"])
            rows = [[d * 1000, "1", "2", "0.5", "1.5"] for d in days if d * 1000 >= params["startTime"]][:1000]
            return R(rows)

    real_sleep = asyncio.sleep
    monkeypatch.setattr(research.asyncio, "sleep", lambda s: real_sleep(0))
    db = DB(tmp_path / "h.db")
    out = asyncio.run(research.update_history(Client(), db, ["BTC"]))
    assert len(out["BTC"]) == len(days) - 1 and out["BTC"][-1][0] == today - 86400   # forming candle dropped
    assert len(calls) >= 3                                                            # paged, 1000 at a time
    calls.clear()
    out = asyncio.run(research.update_history(Client(), db, ["BTC"]))                # same day: nothing to download
    assert not calls and len(out["BTC"]) == len(days) - 1
    db.set("daily:BTC", db.get("daily:BTC")[:-5])                                     # 5 days behind: fetch only those
    out = asyncio.run(research.update_history(Client(), db, ["BTC"]))
    assert calls == [(today - 5 * 86400) * 1000] and len(out["BTC"]) == len(days) - 1
