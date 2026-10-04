"""Integration tests with fake Claude and fake Bitpanda servers (no real money, no real tokens)."""
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty.brokers.bitpanda import BitpandaBroker  # noqa: E402
from tradingbotty.db import DB  # noqa: E402
from tradingbotty.llm import LLM, Budget  # noqa: E402


class FakeMessages:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
            stop_reason="end_turn",
        )


def test_llm_records_cost_and_parses(tmp_path):
    db = DB(tmp_path / "l.db")
    llm = LLM("sk-test", Budget(db, {"total_usd": 20, "spread_over_days": 30, "reinvest_profit_share": 0.1}),
              db, "claude-haiku-4-5", "claude-opus-5-5")
    fake = FakeMessages({"items": [], "takeaway": "calm"})
    llm.client = SimpleNamespace(messages=fake)
    res = asyncio.run(llm.json_call("News Hunter", "s", "p", {"type": "object"}))
    assert res["takeaway"] == "calm"
    cost = db.query("SELECT cost_usd FROM llm_calls")[0]["cost_usd"]
    assert abs(cost - (1000 / 1e6 * 1 + 200 / 1e6 * 5)) < 1e-9
    assert fake.calls[0]["model"] == "claude-haiku-4-5"


def test_news_hunter_with_ai(tmp_path):
    from tradingbotty.agents.base import Blackboard
    from tradingbotty.agents.team import NewsHunter
    from tradingbotty.data.social import Headline

    db = DB(tmp_path / "n.db")
    llm = LLM("sk-test", Budget(db, {"total_usd": 20, "spread_over_days": 30, "reinvest_profit_share": 0.1}),
              db, "claude-haiku-4-5", "claude-opus-5-5")
    llm.client = SimpleNamespace(messages=FakeMessages({
        "items": [{"index": 0, "symbols": ["SOL", "FAKE"], "sentiment": 0.8, "impact": 0.9, "event": "etf"}],
        "takeaway": "SOL ETF news",
    }))
    ctx = SimpleNamespace(db=db, llm=llm, bus=SimpleNamespace(publish=lambda *a: None),
                          prices=SimpleNamespace(quotes={"SOL": 1, "BTC": 1}), settings={"ai": {"news_ai": True}})
    agent = NewsHunter(ctx)
    agent.queue([Headline("Test", "Solana ETF approved", "x", time.time(), ["SOL"])])
    bb = Blackboard()
    asyncio.run(agent.step(bb))
    assert agent.status == "ok"
    assert bb.news["SOL"] > 0.4
    assert bb.news_events[0]["symbols"] == ["SOL"]  # unknown tickers dropped


def test_bitpanda_buy_sell_flow():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path, req.content))
        p = req.url.path
        if p.endswith("/assets"):
            return httpx.Response(200, json={"data": [{"id": "a-btc", "symbol": "BTC"}, {"id": "a-sol", "symbol": "SOL"}]})
        if p.endswith("/currencies"):
            return httpx.Response(200, json={"data": [{"id": "c-eur", "symbol": "EUR"}]})
        if p.endswith("/portfolio"):
            return httpx.Response(200, json={"data": [
                {"asset_id": "a-sol", "available_balance": {"value": "2.0"}, "currency_balance": {"value": "50", "currency_id": "c-eur"}}]})
        if p.endswith("/quotes"):
            return httpx.Response(200, json={"quote_id": "q1", "status": "OPEN"})
        if p.endswith("/quotes/q1/accept"):
            return httpx.Response(200, json={"data": {"trade_id": "t1", "execution": {"notional": "10", "quantity": "0.1"}}})
        return httpx.Response(404)

    b = BitpandaBroker("key", "EUR")
    b.client = httpx.AsyncClient(base_url="https://api.public.bitpanda.com/v1", transport=httpx.MockTransport(handler))

    async def run():
        await b.connect()
        r = await b.buy("BTC", 10)
        assert r["trade_id"] == "t1"
        body = json.loads([c for c in calls if c[1].endswith("/quotes")][0][2])
        assert body == {"asset_id": "a-btc", "currency_id": "c-eur", "side": "BUY", "notional": "10.00"}
        try:
            await b.buy("BTC", 60)  # more than the 50 EUR available
            assert False, "should refuse"
        except Exception as e:
            assert "not enough fiat" in str(e)
        await b.sell_fraction("SOL", 0.5)
        sell = json.loads([c for c in calls if c[1].endswith("/quotes")][-1][2])
        assert sell["side"] == "SELL" and sell["quantity"] == "1"
        assert await b.sell_fraction("BTC", 1.0) is None  # nothing held: no short

    asyncio.run(run())


def test_reddit_rss_fallback():
    from tradingbotty.data.social import SocialFeed, parse_atom

    atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
    <entry><title>Solana to the moon</title><content type="html">&lt;p&gt;SOL is pumping, Bitcoin too&lt;/p&gt;</content></entry>
    <entry><title>Daily discussion</title><content>nothing</content></entry></feed>"""
    assert parse_atom(atom)[0][0] == "Solana to the moon"

    def handler(req):
        if req.url.path.endswith(".json"):
            return httpx.Response(403, text="<html>blocked</html>")
        if req.url.path.endswith(".rss"):
            return httpx.Response(200, content=atom)
        if "coingecko" in req.url.host:
            return httpx.Response(200, json={"coins": [{"item": {"symbol": "sol"}}]})
        return httpx.Response(200, json={"data": [{"value": "60", "value_classification": "Greed"}]})

    feed = SocialFeed(["SOL", "BTC", "ETH"])
    feed.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(feed.poll_hype())
    assert feed.healthy["reddit"]
    assert feed.mention_counts["SOL"] > 0 and feed.mention_counts["BTC"] > 0
    assert feed.trending == ["SOL"] and feed.fear_greed == 60


def test_professor_waits_for_data(tmp_path):
    from tradingbotty.agents.base import Blackboard
    from tradingbotty.agents.team import Professor

    calls = []
    llm = SimpleNamespace(json_call=lambda *a, **k: calls.append(1))
    ctx = SimpleNamespace(db=DB(tmp_path / "p.db"), llm=llm, bus=SimpleNamespace(publish=lambda *a: None),
                          social=SimpleNamespace(fear_greed=None), settings={"ai": {"professor_on": True}})
    prof = Professor(ctx)
    prof.next_due = 0  # pretend warm-up is over
    asyncio.run(prof.step(Blackboard()))
    assert not calls and "waiting" in prof.summary


def test_news_skips_stale_and_duplicate_headlines():
    from email.utils import formatdate
    from tradingbotty.data.social import SocialFeed

    now, old = formatdate(time.time()), formatdate(time.time() - 3 * 86400)
    rss = f"""<rss><channel>
    <item><title>Bitcoin jumps 5%</title><link>a</link><pubDate>{now}</pubDate></item>
    <item><title>Bitcoin jumps 5%!</title><link>b</link><pubDate>{now}</pubDate></item>
    <item><title>Old Solana story</title><link>c</link><pubDate>{old}</pubDate></item>
    </channel></rss>""".encode()
    feed = SocialFeed(["BTC", "SOL"])
    feed.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=rss)))
    first = asyncio.run(feed.poll_news())
    assert [h.title for h in first] == ["Bitcoin jumps 5%"]  # one copy, stale one dropped
    assert asyncio.run(feed.poll_news()) == []  # nothing re-read on the next poll


def test_fusion_buy_sell_flow():
    from tradingbotty.brokers.fusion import FusionBroker

    sent = []
    state = {"polls": 0}

    def handler(req: httpx.Request):
        p = req.url.path
        if p == "/v1/pairs":
            return httpx.Response(200, json=[
                {"pair": "BTC-EUR", "baseAsset": "BTC", "quoteAsset": "EUR", "sizeIncrement": "0.00001",
                 "amountIncrement": "0.01", "minOrderAmount": "1"},
                {"pair": "BTC-USD", "baseAsset": "BTC", "quoteAsset": "USD"}])
        if p == "/v1/account/balances":
            return httpx.Response(200, json=[{"symbol": "EUR", "available": "50", "locked": "0"},
                                             {"symbol": "BTC", "available": "0.0012345", "locked": "0"}])
        if p == "/v1/account/orders" and req.method == "POST":
            sent.append(json.loads(req.content))
            return httpx.Response(202, json={"id": "o1", "status": "new"})
        if p == "/v1/account/orders/o1":
            state["polls"] += 1
            return httpx.Response(200, json={"id": "o1", "status": "filled", "filledQuantity": "0.0002",
                                             "filledAveragePrice": "50000", "fee": {"amount": "0.03", "currency": "EUR"}})
        return httpx.Response(404)

    b = FusionBroker("key", "EUR")
    b.client = httpx.AsyncClient(base_url="https://api.fusion.bitpanda.com", transport=httpx.MockTransport(handler))

    async def run():
        assert (await b.connect())["assets"] == 1  # only EUR pairs
        r = await b.buy("BTC", 10.009)
        assert sent[-1] == {"pair": "BTC-EUR", "side": "Buy", "type": "Market", "amount": "10.00"}
        assert r["execution"]["notional"] == 10.0 and r["execution"]["fee"] == 0.03
        try:
            await b.buy("BTC", 60)  # more than the 50 EUR available
            assert False, "should refuse"
        except Exception as e:
            assert "not enough fiat" in str(e)
        try:
            await b.buy("BTC", 0.5)  # below the pair minimum
            assert False, "should refuse"
        except Exception as e:
            assert "minimum" in str(e)
        await b.sell_fraction("BTC", 1.0)
        assert sent[-1] == {"pair": "BTC-EUR", "side": "Sell", "type": "Market", "quantity": "0.00123"}
        await b.sell_fraction("BTC", 1.0, owned=0.0002)  # the bot only bought 0.0002: your other BTC stays
        assert sent[-1]["quantity"] == "0.00020"
        n = len(sent)
        assert await b.sell_fraction("BTC", 1.0, owned=0.0) is None and len(sent) == n
    asyncio.run(run())


def test_live_mirror_never_sells_your_own_coins():
    from types import SimpleNamespace
    from tradingbotty.engine import Engine

    class FakeLive:
        currency = "CHF"
        def __init__(self):
            self.bal = {"FIAT": 30.0, "BTC": 0.00345}  # 0.00345 BTC was yours before the bot
            self.sells = []
        async def balances(self):
            return dict(self.bal)
        async def buy(self, symbol, amount):
            self.bal["FIAT"] -= amount
            self.bal[symbol] = self.bal.get(symbol, 0) + 0.0001
            return {"execution": {"quantity": 0.0001, "price": amount / 0.0001, "notional": amount, "fee": 0}}
        async def sell_fraction(self, symbol, fraction, owned=None):
            qty = min(self.bal.get(symbol, 0), owned) * fraction
            self.sells.append(qty)
            self.bal[symbol] -= qty
            return {"execution": {"quantity": qty, "price": 1, "notional": qty, "fee": 0}}

    store = {}
    eng = Engine.__new__(Engine)
    eng.live = FakeLive()
    eng.live_errors = 0
    eng.settings = {"risk": {"min_order_usd": 1}}
    eng.db = SimpleNamespace(get=lambda k, d=None: store.get(k, d), set=store.__setitem__, execute=lambda *a: None)
    eng.champion = lambda: SimpleNamespace(id="v1")
    eng._log = lambda *a: None
    eng.bus = SimpleNamespace(publish=lambda *a: None)

    async def run():
        await eng._mirror_live("BTC", "SELL", 1.0)  # bot owns no BTC yet: nothing sold
        assert eng.live.sells == []
        await eng._mirror_live("BTC", "BUY", 0.2)
        await eng._mirror_live("BTC", "SELL", 1.0)
        assert eng.live.sells == [0.0001]
        assert abs(eng.live.bal["BTC"] - 0.00345) < 1e-12  # your own BTC untouched
        await eng._mirror_live("BTC", "SELL", 1.0)
        assert len(eng.live.sells) == 1
    asyncio.run(run())
