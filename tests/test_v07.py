"""Regime Radar and AI Manager."""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeFusion, _engine  # noqa: E402
from tradingbotty import regime, research  # noqa: E402


def test_regime_uses_only_the_past_and_names_every_day():
    cd = research.Candles.from_rows(research.synthetic_rows(research.UNIVERSE[:8], days=900))
    full = regime.classify(cd)
    cut = research.Candles.from_rows({s: rows[:600] for s, rows in
                                      research.synthetic_rows(research.UNIVERSE[:8], days=900).items()})
    part = regime.classify(cut)
    assert part[599] == full[599]                       # adding later days never changes an earlier verdict
    assert {r["label"] for r in full if r} <= set(regime.LABELS)
    s = regime.summary(cd)
    assert s["label"] in regime.LABELS and len(s["recent"]) == 90 and abs(sum(v["share_pct"] for v in s["stats"].values()) - 100) < 1


def test_ai_manager_saves_on_news_that_never_mattered(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    for _ in range(3):
        e.db.execute("INSERT INTO llm_calls(ts,agent,model,input_tokens,output_tokens,cost_usd) VALUES(?,?,?,?,?,?)",
                     (time.time(), "News Hunter", "claude-haiku-4-5", 1000, 200, 0.25))
    e.bb.news_events = [{"ts": time.time(), "ai": True, "event": "other", "impact": 0.2, "symbols": [], "sentiment": 0.1,
                         "title": "calm day", "source": "x"}]
    asyncio.run(e.agent("aimanager").run(e.bb))
    t = e.db.get("ai_throttle")
    assert t["news_coin_only"] is True and t["thinktank_hours"] == 6
    # in saving mode only headlines that name a coin go to Claude
    from tradingbotty.data.social import Headline
    news = e.agent("news")
    sent = []

    async def fake_ai(batch):
        sent.extend(batch)
        return None
    news._score_ai = fake_ai
    news.pending = [Headline("x", "Bitcoin jumps", "l1", time.time(), ["BTC"]),
                    Headline("x", "Markets are calm", "l2", time.time(), [])]
    asyncio.run(news.run(e.bb))
    assert [h.title for h in sent] == ["Bitcoin jumps"]
