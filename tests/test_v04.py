"""v0.4/v0.5: Kraken scan, Fusion broker, the daily brain on real money, history test."""
import time as _time
import pytest
import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty.config import load_settings  # noqa: E402
from tradingbotty.data import prices as P  # noqa: E402
from tradingbotty.data.universe import Universe  # noqa: E402

from fakes import FakeFusion, _engine  # noqa: E402


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
    f.bal["ADA"] = 3.0
    e = _engine(tmp_path, monkeypatch, f)
    e.set_controls({"live.max_invest": 100, "live.max_order": 35})
    e.db.set("live_qty", {"ADA": 3.0}); e.db.set("live_cost", {"ADA": 30.0})
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
    assert b["steps"][0].startswith("Daily candle of") and "sold ADA" in b["steps"]


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


def test_brain_uses_the_whole_account_and_leaves_unsellable_dust(tmp_path, monkeypatch):
    from tradingbotty import research
    f = FakeFusion()
    f.pairs = {s: {"minOrderAmount": "25"} for s in ("BTC", "SOL", "AKT")}
    f.bal.update({"FIAT": 400.0, "AKT": 2.0})              # AKT worth 20 CHF: under Fusion's minimum
    e = _engine(tmp_path, monkeypatch, f)
    e.db.set("live_qty", {"AKT": 2.0}); e.db.set("live_cost", {"AKT": 25.0})
    monkeypatch.setattr(research, "current_target", lambda cd, s: {"SOL": 0.5, "BTC": 0.5})
    asyncio.run(e.set_brain(True, "Breakout 20/10 days, 3 slots, BTC filter 50d"))
    asyncio.run(e.brain_tick())
    assert not f.buys                                       # balance not read yet: no guessing with a 2000 cap
    e.wallet = {"total": 420.0, "currency": "CHF"}
    e.set_controls({"live.max_invest": 2000, "live.max_order": 150, "live.use_my_coins": True})
    asyncio.run(e.brain_tick())
    assert not f.sells and e.live_errors == 0               # dust isn't sent to Fusion to be rejected
    assert "sell it in the Bitpanda app" in e.brain()["note"]
    # half of the account each (minus 2% for fees) in orders under the 150 cap; the last one gets what cash is left
    assert [(s, round(a, 2)) for s, a in f.buys] == [("SOL", 102.9), ("SOL", 102.9), ("BTC", 102.9), ("BTC", 90.84)]
    e.set_controls({"live.max_order": 250})                 # changing a money limit re-decides now
    assert "day" not in e.brain()
    f.bal["AKT"] = 0.0                                      # you sold the dust in the app: the bot forgets it
    asyncio.run(e.brain_tick())
    assert "AKT" not in e.db.get("live_qty") and e.live_errors == 0


def test_live_account_gets_tuned_once(tmp_path, monkeypatch):
    monkeypatch.setenv("TB_SIMULATE", "1")
    monkeypatch.setenv("TB_DB", str(tmp_path / "t.db"))
    from tradingbotty.engine import Engine
    e = Engine(load_settings())
    assert e.settings["live"]["max_invest"] == 25           # fresh install in paper: untouched
    e.db.set("mode", "live")
    e.set_controls({"live.max_order": 35})
    e2 = Engine(load_settings())
    assert e2.settings["live"]["max_invest"] == 2000 and e2.settings["live"]["max_order"] == 150
    e2.set_controls({"live.max_order": 60})                 # your later change wins after the next start
    e3 = Engine(load_settings())
    assert e3.settings["live"]["max_order"] == 60


def test_brain_finishes_the_day_when_cash_runs_short(tmp_path, monkeypatch):
    from tradingbotty import research
    f = FakeFusion()
    f.pairs = {s: {"minOrderAmount": "30"} for s in ("BTC", "SOL")}
    f.bal.update({"FIAT": 60.0})                            # plus 6 CHF of a coin Fusion can't sell
    e = _engine(tmp_path, monkeypatch, f)
    e.set_controls({"live.max_invest": 2000, "live.max_order": 150, "live.use_my_coins": True})
    e.wallet = {"total": 66.0, "currency": "CHF"}
    monkeypatch.setattr(research, "current_target", lambda cd, s: {"SOL": 0.5, "BTC": 0.5})
    asyncio.run(e.set_brain(True, "Breakout 20/10 days, 3 slots, BTC filter 50d"))
    asyncio.run(e.brain_tick())
    b = e.brain()
    assert len(f.buys) == 1                                 # SOL 32.34; only 27.66 is left for BTC
    assert all(a >= 30 for _, a in f.buys)                    # no order under Fusion's minimum is ever sent
    assert b.get("day") and "not bought" in b["note"]         # the day is decided, not retried every 5 minutes


def test_refused_orders_never_block_the_daily_decision(tmp_path, monkeypatch):
    from tradingbotty import research
    from tradingbotty.brokers.fusion import FusionError

    class Picky(FakeFusion):
        async def sell_fraction(self, symbol, fraction, owned=None):
            if symbol == "AKT":
                raise FusionError('POST /v1/account/orders -> 422: {"errors":[{"code":"ORDER_CREATION_ERROR"}]}')
            return await super().sell_fraction(symbol, fraction, owned)

        async def buy(self, symbol, amount):
            if amount <= 30:
                raise FusionError('POST /v1/account/orders -> 400: {"errors":[{"title":"Enter a higher amount than 30 CHF to create the order."}]}')
            return await super().buy(symbol, amount)

    f = Picky()
    f.pairs = {s: {"minOrderAmount": "25"} for s in ("BTC", "SOL", "ETH", "AKT", "VSN")}
    f.bal.update({"FIAT": 100.0, "AKT": 5.0, "VSN": 1.5})    # AKT: Fusion refuses it; VSN: yours, 15 CHF, too small
    e = _engine(tmp_path, monkeypatch, f)
    e.set_controls({"live.max_invest": 2000, "live.max_order": 150, "live.use_my_coins": True})
    e.db.set("live_qty", {"AKT": 5.0}); e.db.set("live_cost", {"AKT": 50.0})
    e.wallet = {"total": 165.0, "currency": "CHF"}
    monkeypatch.setattr(research, "current_target", lambda cd, s: {"SOL": 0.34, "BTC": 0.33, "ETH": 0.33})
    asyncio.run(e.set_brain(True, "Breakout 20/10 days, 3 slots, BTC filter 50d"))
    for _ in range(3):
        asyncio.run(e.brain_tick())
    b = e.brain()
    assert e.mode == "live" and b.get("day")                       # decided once, still live
    assert "Fusion refused to sell AKT" in b["note"]
    assert ("VSN", 1.5) not in f.sells                             # 15 CHF of VSN is never sent to be refused
    assert [s for s, _ in f.buys] == ["SOL", "BTC"]                # ETH: no cash left today
    assert "ETH not bought" in b["note"]
    f.bal["FIAT"] = 40.0                                           # Fusion's list says 25, it really wants > 30
    with pytest.raises(ValueError, match="noted"):
        asyncio.run(e._live_buy("ETH", 28.0, "t"))
    assert e.db.get("fusion_min")["ETH"] == 30.6
    with pytest.raises(ValueError, match="minimum"):               # next time it isn't even sent
        asyncio.run(e._live_buy("ETH", 28.0, "t"))
