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
