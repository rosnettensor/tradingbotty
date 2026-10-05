"""v0.5: a real-money team. Free data with history, Pattern Hunter, Guardian, Professor veto, Trend Watch,
self-healing brain, phone report, and every agent showing what happens inside it."""
import asyncio
import json
import math
import random
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty import altdata, patterns, research  # noqa: E402
from tradingbotty.db import DB  # noqa: E402

from fakes import FakeFusion, _engine  # noqa: E402

DAY = 86400
STRAT = "Breakout 20/10 days, 3 slots, BTC filter 50d"


def _today():
    return int(time.time() // DAY * DAY)


def _alt_client(fail=()):
    t0 = _today() - 5 * DAY

    def handler(req: httpx.Request):
        u = str(req.url)
        if "fng" in u and "fng" not in fail:
            return httpx.Response(200, json={"data": [{"value": str(40 + i), "timestamp": str(t0 + i * DAY)} for i in range(6)]})
        if "fundingRate" in u and "funding" not in fail:
            start = int(req.url.params["startTime"])
            rows = [{"fundingTime": (t0 + i * DAY // 3) * 1000, "fundingRate": "0.0003"} for i in range(15)]
            return httpx.Response(200, json=[r for r in rows if r["fundingTime"] >= start])
        if "wikimedia" in u and "wiki" not in fail:
            items = [{"timestamp": time.strftime("%Y%m%d00", time.gmtime(t0 + i * DAY)), "views": 1000 + i} for i in range(6)]
            return httpx.Response(200, json={"items": items})
        if "stablecoin" in u and "stables" not in fail:
            return httpx.Response(200, json=[{"date": str(t0 + i * DAY), "totalCirculatingUSD": {"peggedUSD": 1e11 + i}}
                                             for i in range(6)])
        if "hash-rate" in u and "hash" not in fail:
            return httpx.Response(200, json={"values": [{"x": t0 + i * DAY, "y": 5e8 + i} for i in range(6)]})
        return httpx.Response(500, json={"error": "down"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_free_data_is_stored_once_a_day_and_failures_keep_history(tmp_path, monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(altdata.asyncio, "sleep", lambda s: real_sleep(0))
    db = DB(tmp_path / "a.db")
    days = asyncio.run(altdata.update(_alt_client(), db, ["BTC", "SOL"]))
    assert days["fear_greed"] == 5                      # today's value isn't final yet: dropped
    assert days["funding:SOL"] >= 4 and days["wiki:BTC"] == 5 and days["stablecoins"] == 5 and days["hashrate"] == 5
    assert db.get("alt:funding:BTC")[0][1] == pytest.approx(0.0003)  # daily mean of the 8-hourly rates
    assert asyncio.run(altdata.update(_alt_client(fail=("fng",)), db, ["BTC"]))["fear_greed"] == 5  # same day: cached
    db.set("alt_status", {})                            # next day, one source down: its history stays
    warned = []
    days = asyncio.run(altdata.update(_alt_client(fail=("fng",)), db, ["BTC"], lambda *a: warned.append(a)))
    assert days["fear_greed"] == 5 and warned and "fear_greed" in warned[0][2]


def test_alternative_data_is_used_one_day_late():
    rows = research.synthetic_rows(["BTC"], days=30)
    cd = research.Candles.from_rows(rows)
    d5 = int(cd.days[5])
    cd.attach({"x": {d5: 7.0}})
    assert cd.feat("x", 5) is None and cd.feat("x", 6) == 7.0     # known only after the day is over
    assert cd.feat("x", 6 + 9) == 7.0 and cd.feat("x", 6 + 11) is None   # goes stale after 10 days


def test_pattern_hunter_finds_a_planted_signal_and_calls_noise_chance():
    rng = random.Random(5)
    coins = ["BTC", "ETH", "SOL", "ADA", "XRP", "DOGE"]
    n = 1500
    t0 = _today() - n * DAY
    sig = [0.0]
    for _ in range(n - 1):                                     # a slow-moving mood, like the real index
        sig.append(0.95 * sig[-1] + rng.gauss(0, 0.3))
    rows = {}
    for c in coins:
        p, out = 100.0, []
        for i in range(n):
            drift = 0.004 * sig[i - 1] if i > 0 else 0     # yesterday's mood pushes BTC today
            p *= math.exp(rng.gauss(drift if c == "BTC" else 0, 0.02))
            out.append((t0 + i * DAY, p, p * 1.01, p * 0.99, p))
        rows[c] = out
    cd = research.Candles.from_rows(rows)
    fg = {t0 + i * DAY: 50 + 10 * (sig[i]) for i in range(n)}
    cd.attach({"fear_greed": fg})
    res = patterns.analyse(cd)
    by = {r["name"]: r for r in res["rows"]}
    fgrow = next(r for name, r in by.items() if "Fear & Greed" in name and "change" not in name.lower())
    assert fgrow["verdict"] in ("pattern", "hint") and fgrow["corr"] > 0
    assert all(r["verdict"] in ("chance", "no data") for name, r in by.items() if "random" in name.lower())


def test_history_test_includes_the_alternative_data_strategies(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    res = asyncio.run(e.run_research())
    alt = [r for r in res["rows"] if r["group"] == "Trend + alternative data"]
    assert len(alt) == 3 and res["strategies_tested"] == len(research.all_strategies())
    assert e.db.get("patterns")["rows"]
    assert {r["group"] for r in res["rows"]} >= {"Mix", "Trend + calm coins"}
    assert all(r["fees2x"] for r in res["rows"] if r["group"] != "Benchmark")


def test_reality_checks_use_only_the_past_and_rerun_on_fewer_coins():
    cd = research.Candles.from_rows(research.synthetic_rows(research.UNIVERSE[:9], days=1200))
    live = "Breakout 20/10 days, 3 slots, BTC filter 50d"
    res = research.run_all(cd, focus=live)
    wf = res["walk_forward"]
    assert wf["live"] == live and len(wf["years"]) == len(res["years"]) - 1   # the first year only teaches
    names = {r["name"] for r in res["rows"]}
    assert all(y["pick"] in names and y["pick"] != research.HoldBTC.name for y in wf["years"])
    luck = res["coin_luck"]
    assert luck[0]["name"] == live and luck[0]["rounds"] == 8 and luck[0]["worst_cagr"] <= luck[0]["median_cagr"]
    sub = cd.subset(["BTC", "ETH"])
    assert sub.coins == ["BTC", "ETH"] and sub.days is cd.days
    mix = research.by_name("Mix: half Breakout 20/10, half Bitcoin above its 50-day average")
    held = research.current_target(cd, mix)
    assert sum(held.values()) <= 1.0 + 1e-9


def _brain_engine(tmp_path, monkeypatch, target):
    f = FakeFusion()
    f.pairs = {s: {"minOrderAmount": "25"} for s in ("BTC", "SOL", "ETH", "ADA")}
    f.bal.update({"FIAT": 300.0})
    e = _engine(tmp_path, monkeypatch, f)
    e.set_controls({"live.max_invest": 2000, "live.max_order": 150, "live.use_my_coins": True})
    e.wallet = {"total": 300.0, "currency": "CHF"}
    monkeypatch.setattr(research, "current_target", lambda cd, s: dict(target))
    return e, f


def _hack(sym, source, event="hack", age=0):
    return {"ts": time.time() - age, "source": source, "title": f"{sym} bridge drained ({source})", "link": "x",
            "symbols": [sym], "sentiment": -0.9, "impact": 0.9, "event": event, "ai": True}


def test_guardian_blocks_buys_after_hack_news(tmp_path, monkeypatch):
    e, f = _brain_engine(tmp_path, monkeypatch, {"SOL": 0.5, "BTC": 0.5})
    e.bb.news_events = [_hack("SOL", "CoinDesk")]
    asyncio.run(e.guard_tick())
    assert "SOL" in e.guard() and e.guard()["SOL"]["reason"] == "hack"
    asyncio.run(e.set_brain(True, STRAT))
    asyncio.run(e.brain_tick())
    assert [s for s, _ in f.buys] == ["BTC"] and "Guardian blocks it" in e.brain()["note"]
    e.bb.news_events = [_hack("SOL", "CoinDesk", age=4 * DAY)]       # 3 days later the block is gone
    e.db.set("guard", {})
    asyncio.run(e.guard_tick())
    assert "SOL" not in e.guard()


def test_guardian_sells_a_held_coin_only_with_two_witnesses_and_never_btc(tmp_path, monkeypatch):
    e, f = _brain_engine(tmp_path, monkeypatch, {})
    e.db.set("brain", {"on": True, "strategy": STRAT})
    f.bal.update({"SOL": 3.0, "BTC": 1.0})
    e.db.set("live_qty", {"SOL": 3.0, "BTC": 1.0})
    e.bb.news_events = [_hack("SOL", "CoinDesk"), _hack("BTC", "CoinDesk"), _hack("BTC", "Decrypt")]
    asyncio.run(e.guard_tick())
    assert f.sells == []                                             # one witness: block only
    e.shocks = {"SOL": {"change": -31.0, "btc": -1.0, "ts": time.time()}}
    asyncio.run(e.guard_tick())                                      # the crash is the second witness
    assert f.sells == [("SOL", 3.0)] and e.guard()["SOL"]["sold"]
    assert "BTC" in e.db.get("live_qty")                             # BTC never sold on hack news
    asyncio.run(e.guard_tick())
    assert len(f.sells) == 1                                         # sold once, not every minute


def test_professor_can_block_a_buy_but_never_sells(tmp_path, monkeypatch):
    e, f = _brain_engine(tmp_path, monkeypatch, {"ETH": 0.5, "BTC": 0.5})
    e.db.set("brain", {"on": True, "strategy": STRAT})
    f.bal["ETH"] = 2.0
    e.db.set("live_qty", {"ETH": 2.0})
    e.professor_block("ETH", "token unlock of 20% of supply tomorrow")
    asyncio.run(e.guard_tick())
    assert f.sells == [] and e.guard()["ETH"]["reason"] == "professor"
    e.db.set("live_qty", {})
    f.bal["ETH"] = 0.0
    asyncio.run(e.set_brain(True, STRAT))
    asyncio.run(e.brain_tick())
    assert [s for s, _ in f.buys] == ["BTC"] and "ETH not bought" in e.brain()["note"]


def test_professor_reviews_each_decision_once(tmp_path, monkeypatch):
    e, f = _brain_engine(tmp_path, monkeypatch, {"BTC": 1.0})
    calls = []

    async def fake_call(agent, system, prompt, schema, **kw):
        calls.append(prompt)
        return {"assessment": "Bitcoin trend intact, holding it is consistent with the rules.", "watch": "BTC 50d average",
                "idea": "test a 30-day breakout", "block": [{"symbol": "ADA", "reason": "exchange delisting announced"},
                                                            {"symbol": "FAKE", "reason": "not a coin we trade"}],
                "_cost": 0.04}
    e.llm.json_call = fake_call
    prof = e.agent("professor")
    asyncio.run(prof.step(e.bb))
    assert not calls                                                 # no decision yet: nothing to review
    asyncio.run(e.set_brain(True, STRAT))
    asyncio.run(e.brain_tick())
    asyncio.run(prof.step(e.bb))
    asyncio.run(prof.step(e.bb))
    assert len(calls) == 1 and "decision" in calls[0]
    assert set(e.guard()) == {"ADA"} and prof.detail["assessment"].startswith("Bitcoin")
    assert e.db.get("professor_last")["cost"] == 0.04


def test_brain_switches_after_three_weak_nights(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    e.db.set("brain", {"on": True, "strategy": STRAT, "day": 1})
    rows = [{"name": STRAT, "robust": False, "full": {}},
            {"name": "Top 3 by 30-day strength, BTC filter 50d", "robust": True, "full": {"cagr_pct": 50}}]
    for night in range(1, 3):
        e._heal_brain({"rows": rows})
        assert e.brain()["strategy"] == STRAT and e.brain()["weak_days"] == night
    e._heal_brain({"rows": [{**rows[0], "robust": True}, rows[1]]})   # one good night resets the count
    assert e.brain()["weak_days"] == 0
    for _ in range(3):
        e._heal_brain({"rows": rows})
    b = e.brain()
    assert b["strategy"] == rows[1]["name"] and b["switched"]["from"] == STRAT and "day" not in b


def test_phone_report_needs_your_telegram_keys(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    assert asyncio.run(e.notify("hi")) is False                       # no keys: nothing sent
    sent = []
    e.settings.simulate = False
    e.settings.telegram_token, e.settings.telegram_chat = "123:abc", "42"
    e.prices.client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: sent.append(json.loads(r.content)) or httpx.Response(200, json={"ok": True})))
    e.wallet = {"total": 305.0, "fiat": 100.0, "currency": "CHF", "bot_edge": -3.5}
    assert asyncio.run(e.notify(e.daily_report())) is True
    assert sent[0]["chat_id"] == "42" and "Bot's own gain/loss: -3.50 CHF" in sent[0]["text"]


def test_trend_watch_previews_tonights_decision():
    from tradingbotty.agents.crew import preview
    rows = research.synthetic_rows(["BTC", "ETH", "SOL", "ADA"], days=400, seed=3)
    # Bitcoin in a clean uptrend, so buys are allowed
    t0 = rows["BTC"][0][0]
    rows["BTC"] = [(t0 + i * DAY, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(400)]
    cd = research.Candles.from_rows(rows)
    base = preview(cd, STRAT, {})
    assert base["preview"]["hold_now"] == sorted(research.current_target(cd, research.by_name(STRAT)))
    assert base["btc"]["ok"]
    sol = next(r for r in base["rows"] if r["symbol"] == "SOL")
    hot = preview(cd, STRAT, {"SOL": sol["high"] * 1.5 if sol["high"] * 1.5 / cd.c["SOL"][-1] < 2 else cd.c["SOL"][-1] * 1.9})
    s2 = next(r for r in hot["rows"] if r["symbol"] == "SOL")
    if "SOL" in base["preview"]["hold_now"]:
        assert s2["state"] == "holding"
    else:
        assert s2["state"] in ("would buy", "breakout, no slot") and s2["to_breakout_pct"] < 0
    crazy = preview(cd, STRAT, {"ADA": cd.c["ADA"][-1] * 50})           # a broken quote is ignored
    assert next(r for r in crazy["rows"] if r["symbol"] == "ADA")["price"] == cd.c["ADA"][-1]


def test_every_agent_works_for_the_real_money_and_explains_itself(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    e.wallet = {"total": 300.0, "fiat": 100.0, "currency": "CHF", "bot_value": 0, "coins": [], "ts": time.time()}
    asyncio.run(e.prices.backfill())
    asyncio.run(e.run_research())
    asyncio.run(e.set_brain(True, STRAT))
    asyncio.run(e.agent("radar").scan())
    asyncio.run(e.tick())
    ids = [a.id for a in e.team]
    assert ids == ["radar", "trend", "collector", "news", "patterns", "researcher", "guardian", "professor", "brain",
                   "fast", "risk", "livedesk"]
    for a in e.team:
        assert a.status in ("ok", "warn", "idle") or (a.id == "fast" and a.status == "off"), (a.name, a.summary)
        assert a.explain and a.outputs and a.cadence
    for aid in ("radar", "trend", "patterns", "researcher", "guardian", "risk", "livedesk"):
        assert e.agent(aid).detail.get("did"), aid
    assert e.trend["rows"] and e.agent("trend").detail["trend"]["strategy"] == STRAT
    known = {n.id for n in e.sources + e.team}
    assert all(i in known for a in e.team for i in a.inputs)            # every wire in the graph has both ends
    state = e.state()
    json.dumps(state, default=str)
    assert "champion" not in state and "variants" not in state
    for gone in ("strategy", "backtest", "decisions", "agents.team", "agents.optimizer", "brokers.paper"):
        with pytest.raises(ImportError):
            __import__(f"tradingbotty.{gone}")


def test_fusion_scout_flags_crashes_against_bitcoin(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    rows = [{"symbol": "BTC", "change": -2.0, "volume_usd": 1e9, "spread_pct": 0.01},
            {"symbol": "SOL", "change": -25.0, "volume_usd": 1e8, "spread_pct": 0.05},
            {"symbol": "ETH", "change": -18.0, "volume_usd": 1e8, "spread_pct": 0.05}]

    async def scan():
        return rows
    e.universe.scan = scan
    e.fusion_coins = {"BTC", "SOL"}
    asyncio.run(e.agent("radar").scan())
    assert set(e.shocks) == {"SOL"}                                   # ETH fell, but not 20%
    asyncio.run(e.agent("radar").step(e.bb))
    r = e.agent("radar")
    assert r.status == "warn" and "SOL" in r.summary
    tab = {row[0]: row for row in r.detail["table"]["rows"]}
    assert tab["SOL"][-1] == "CRASH" and tab["ETH"][1] == "no"
