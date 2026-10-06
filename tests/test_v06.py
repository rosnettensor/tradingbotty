"""Profit taking, win-rate stats, the action rules and tolerant news feeds."""
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from tradingbotty import fastlab, research
from tradingbotty.data import social


def _cd(days=900):
    return research.Candles.from_rows(research.synthetic_rows(research.UNIVERSE[:9], days=days))


def test_profit_lock_and_take_profit_trade_more_often_and_report_win_rates():
    cd = _cd()
    base = research.simulate(cd, research.Donchian(20, 10), 60)
    take = research.simulate(cd, research.Donchian(20, 10, tp=0.25), 60)
    lock = research.simulate(cd, research.Donchian(20, 10, lock=(0.1, 0.02)), 60)
    for r in (base, take, lock):
        st = r.round_stats()
        assert st["rounds"] > 0 and 0 <= st["win_pct"] <= 100 and st["per_week"] > 0
    assert take.round_stats()["avg_win_pct"] <= base.round_stats()["avg_win_pct"] + 30  # gains capped near +25%
    # the base strategy's name (the live brain's) is unchanged, the variants say what they do
    assert research.Donchian(20, 10).name == "Breakout 20/10 days, 3 slots, BTC filter 50d"
    assert research.by_name("Breakout 20/10 days, 3 slots, BTC filter 50d, lock +2% after +10%")


def test_action_rules_are_selectable_for_the_fast_pot():
    action = [s for s in fastlab.fast_strategies() if s.group == "Fast: action"]
    assert len(action) == 5
    for s in action:
        assert fastlab.by_name(s.name).group == "Fast: action"
        assert s.name.startswith(("Fast", "Pump", "Dip"))


def test_feeds_tolerate_wordpress_quirks_atom_and_explain_refusals():
    wp = (b'\n <?xml version="1.0"?><rss><channel><item><title>Bitcoin&nbsp;jumps</title><link>https://x/1</link>'
          b'<pubDate>Tue, 06 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>')
    assert social.parse_items(wp)[0]["link"] == "https://x/1"
    atom = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>ETH up</title><link href="https://y"/>'
            b'<updated>2026-10-06T10:00:00Z</updated></entry></feed>')
    item = social.parse_items(atom)[0]
    assert item["link"] == "https://y" and social.parse_any_date(item["date"])

    def handler(req):
        return httpx.Response(400, text="<html>Bad Request</html>")

    feed = social.SocialFeed(["BTC"])
    feed.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    feed.feeds = {"Broken": "https://broken.example/rss"}
    why = asyncio.run(feed.check_feed("https://broken.example/rss"))
    assert "HTTP 400" in why
    for _ in range(3):
        asyncio.run(feed.poll_news())
    st = feed.source_status["Broken"]
    assert not st["ok"] and "HTTP 400" in st["error"] and st["pause_until"] > 0


def test_think_tank_parses_safely_judges_honestly_and_finds_nothing_in_noise():
    from tradingbotty import thinktank as tt
    import pytest
    for bad in ("__import__('os')", "ret(20) ; x", "open(1)", "ret(20) +"):
        with pytest.raises(tt.FormulaError):
            tt.parse(bad)
    assert tt.show(tt.parse("rank(ret(18)) - 0.5 * vol(10)")) == "(rank(ret(20)) - (0.5 * vol(10)))"
    cd = research.Candles.from_rows(research.synthetic_rows(research.UNIVERSE[:10], days=1500))
    # the echo only uses visits whose next n days were over before today
    e = tt.feature(cd, "echo", 10)["BTC"]
    assert all(x is None for x in e[:25]) and any(x is not None for x in e)
    queue = [dict(s) for s in tt.SEEDS]
    st = tt.run_batch(cd, {}, queue, "Breakout 20/10 days, 3 slots, BTC filter 50d", seconds=6, seed=3)
    assert not queue and st["counts"]["tested"] >= len(tt.SEEDS)
    assert st["counts"].get("candidate", 0) == 0          # random-walk prices: nothing may pass every check
    assert {x["origin"] for x in st["board"]} >= {"Ralph", "control"}
    # a promoted idea becomes a normal strategy in the history lab
    research.EXTRA[:] = [lambda: tt.FormulaStrategy(tt.clean(dict(tt.SEEDS[0])))]
    try:
        assert research.by_name("Think tank: Ralph's echo, 10 days")
    finally:
        research.EXTRA[:] = []
