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
                          prices=SimpleNamespace(quotes={"SOL": 1, "BTC": 1}))
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
