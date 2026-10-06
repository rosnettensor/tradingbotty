"""The real-money team. Every agent here either decides, guards or executes the real trades, or feeds the
research that picks the strategy which trades the real money. Nothing here plays with pretend money.

Pipeline (left to right in the dashboard):
  data      Fusion Scout · Trend Watch · Data Collector · News Hunter
  research  Pattern Hunter -> Researcher (history test, nightly)
  guard     Guardian (hack/delisting news, crashes) · The Professor (daily review, can block a buy)
  decide    Daily Brain (the strategy that passed the history test)
  execute   Risk Officer (hard limits) -> Live Desk (orders on Bitpanda Fusion)

Most heavy work runs in the engine's own loops (scan, research, brain, guardian); an agent's run() turns the
results into its status, summary and "what happened inside" detail for the dashboard.
"""
from __future__ import annotations

import asyncio
import math
import time

from .. import research
from .base import Agent, Blackboard


def _day(ts: float) -> str:
    return time.strftime("%d.%m.%Y", time.gmtime(ts))


def _hm(ts: float) -> str:
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(ts, ZoneInfo("Europe/Zurich")).strftime("%H:%M")
    except Exception:
        return time.strftime("%H:%M", time.localtime(ts))


def next_utc_midnight(now: float | None = None) -> float:
    now = time.time() if now is None else now
    return (now // 86400 + 1) * 86400


# --------------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------------


class FusionScout(Agent):
    id = "radar"
    name = "Fusion Scout"
    role = "Checks which coins Fusion really trades, their minimum order and spread, and spots crashes"
    inputs = ["src_fusion", "src_kraken"]
    cadence = "every 10 minutes"
    explain = ("Plain code, free. Every 10 minutes it reads the 24h numbers of every coin on Kraken in one request and "
               "Fusion's list of tradable coins. For each coin the Daily Brain may trade it checks: does Fusion trade it "
               "in your currency, what is Fusion's real minimum order (learned from rejections), how wide is the spread "
               "and how much trades. A coin that falls 20% or more in a day while Bitcoin doesn't is a crash alert: the "
               "Guardian counts it as a second witness next to hack or delisting news.")
    outputs = "tradable coins and minimums for the Daily Brain and Risk Officer, crash alerts for the Guardian"
    SHOCK_DROP = -20.0      # % in 24h
    SHOCK_VS_BTC = -15.0    # and this much worse than Bitcoin

    def __init__(self, ctx):
        super().__init__(ctx)
        self.rows: list[dict] = []
        self.ts = 0.0
        self.error = ""

    async def scan(self) -> None:
        """Runs in its own loop (one Kraken request for all coins)."""
        e = self.ctx
        try:
            rows = await e.universe.scan()
            self.error = ""
        except Exception as ex:
            self.error = str(ex) or type(ex).__name__
            raise
        by = {r["symbol"]: r for r in rows}
        btc = (by.get("BTC") or {}).get("change", 0.0)
        out, shocks = [], {}
        for s in research.UNIVERSE:
            r = by.get(s) or {}
            ch = r.get("change")
            q = e.prices.quotes.get(s)
            if ch is None and q and q.price:
                ch = q.change_24h_pct
            on = None if e.fusion_coins is None else s in e.fusion_coins
            row = {"symbol": s, "on_fusion": on, "min_order": round(e._min_order(s), 2) or None,
                   "change": None if ch is None else round(ch, 2), "volume_usd": r.get("volume_usd"),
                   "spread_pct": None if r.get("spread_pct") is None else round(r["spread_pct"], 3),
                   "held": s in e.db.get("live_qty", {})}
            if ch is not None and s != "BTC" and ch <= self.SHOCK_DROP and ch - btc <= self.SHOCK_VS_BTC:
                shocks[s] = {"change": round(ch, 1), "btc": round(btc, 1), "ts": time.time()}
                row["shock"] = True
            out.append(row)
        for s in set(shocks) - set(e.shocks):
            self.say(f"CRASH ALERT {s}: {shocks[s]['change']:+.1f}% in 24h while Bitcoin moved {btc:+.1f}%. "
                     f"Told the Guardian.", "warn")
        e.shocks = shocks
        self.rows, self.ts = out, time.time()

    async def run(self, bb: Blackboard) -> None:
        e = self.ctx
        self.next_run = (self.ts or time.time()) + 600
        if self.error and not self.rows:
            self.status, self.summary = "warn", f"Kraken scan failed: {self.error[:80]}"
            return
        if not self.rows:
            self.summary = "first scan running"
            return
        on = [r for r in self.rows if r["on_fusion"]]
        off = [r["symbol"] for r in self.rows if r["on_fusion"] is False]
        shocks = list(e.shocks)
        self.summary = ((f"{len(on)} of {len(self.rows)} research coins tradable on Fusion" if e.fusion_coins is not None
                         else "Fusion's coin list not read yet (needs your Fusion key)")
                        + (f"; crash alert: {', '.join(shocks)}" if shocks else "; no crashes"))
        if shocks:
            self.status = "warn"
        cur = e.live.currency if e.live else e.settings["live"]["currency"]
        self.detail = {
            "did": [f"Read 24h numbers for every coin on Kraken ({_hm(self.ts)})",
                    f"Fusion lists {len(e.fusion_coins) if e.fusion_coins else '?'} coins in {cur}",
                    f"Checked {len(self.rows)} coins the Daily Brain may trade"
                    + (f"; not on Fusion: {', '.join(off)}" if off else ""),
                    ("Crash alert sent to the Guardian: " + ", ".join(f"{s} {e.shocks[s]['change']:+.1f}%" for s in shocks))
                    if shocks else "No coin crashed against Bitcoin in the last 24h"],
            "facts": [["Fusion coins", len(e.fusion_coins) if e.fusion_coins else "key missing"],
                      ["Crash rule", f"{self.SHOCK_DROP:g}% in 24h and {self.SHOCK_VS_BTC:g}% worse than Bitcoin"],
                      ["Learned minimums", ", ".join(f"{s} {v:g}" for s, v in e.db.get("fusion_min", {}).items()) or "none"]],
            "table": {"cols": ["coin", "on Fusion", f"min order ({cur})", "24h", "spread %", "traded 24h (USD)", ""],
                      "rows": [[r["symbol"], "yes" if r["on_fusion"] else "no" if r["on_fusion"] is False else "?",
                                r["min_order"], r["change"], r["spread_pct"],
                                None if r["volume_usd"] is None else round(r["volume_usd"]),
                                "CRASH" if r.get("shock") else "held" if r["held"] else ""] for r in self.rows]},
        }


class TrendWatch(Agent):
    id = "trend"
    name = "Trend Watch"
    role = "Runs the Daily Brain's exact rules on live prices: who is near a breakout, who near an exit, and Bitcoin's filter"
    inputs = ["src_binance", "src_kraken"]
    cadence = "every 2 minutes"
    explain = ("Plain code, free. Takes all daily candles since 2017, adds today's live price as if the day closed now, "
               "and runs the Daily Brain's strategy on it, exactly as the brain will at midnight UTC. So you see in "
               "advance what tonight's decision would be at today's prices: which coins would be bought or sold, how "
               "far each coin is from its breakout level (the 20-day high) and its exit level (the 10-day low, or the "
               "trailing stop), and whether Bitcoin is above its 50-day average (below it, the brain holds cash).")
    outputs = "tonight's likely decision and each coin's distance to its buy and sell levels"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.data: dict = {}
        self._busy = False

    async def run(self, bb: Blackboard) -> None:
        e = self.ctx
        if time.time() >= self.next_run and not self._busy:
            self._busy = True
            try:
                cd = await e._daily_candles()
                live = {s: q.price for s, q in e.prices.quotes.items() if q.price}
                name = e.brain().get("strategy") or "Breakout 20/10 days, 3 slots, BTC filter 50d"
                self.data = await asyncio.to_thread(preview, cd, name, live)
                e.trend = self.data
            except Exception as ex:
                self.status, self.summary = "warn", f"waiting for daily history ({str(ex)[:60] or type(ex).__name__})"
                self.next_run = time.time() + 60
                return
            finally:
                self._busy = False
            self.next_run = time.time() + 120
        d = self.data
        if not d:
            self.summary = "loading daily history"
            return
        btc = d["btc"]
        p = d["preview"]
        moves = ([f"buy {', '.join(p['buy'])}"] if p["buy"] else []) + ([f"sell {', '.join(p['sell'])}"] if p["sell"] else [])
        self.summary = (f"Bitcoin {btc['gap_pct']:+.1f}% vs its {btc['days']}-day average · tonight at today's prices: "
                        + ("; ".join(moves) if moves else "no trades"))
        near = [r for r in d["rows"] if r["state"] == "near breakout"]
        self.detail = {
            "did": [f"Loaded {d['days']} daily candles for {len(d['rows'])} coins (last full day {d['last_day']})",
                    f"Added today's live prices as a pretend close ({_hm(d['ts'])})",
                    f"Replayed \"{d['strategy']}\" on all history, like the brain does",
                    f"Bitcoin {btc['price']:.0f} vs its {btc['days']}-day average {btc['avg']:.0f}: "
                    + ("buys allowed" if btc["ok"] else "below: the brain holds cash"),
                    "Tonight at today's prices: " + ("; ".join(moves) if moves else "no trades")
                    + (f"; near a breakout: {', '.join(r['symbol'] for r in near)}" if near else "")],
            "facts": [["Strategy", d["strategy"]], ["Holds now", ", ".join(p["hold_now"]) or "cash"],
                      ["Would hold tonight", ", ".join(p["hold_next"]) or "cash"]],
            "trend": d,
        }


def preview(cd: "research.Candles", name: str, live: dict[str, float]) -> dict:
    """Run the strategy up to the last full day (what the brain holds now) and once more with today's live prices as
    a pretend close (what it would decide tonight if prices stayed). Plus every coin's buy and sell levels."""
    strat = research.by_name(name)
    if not strat:
        raise ValueError(f"unknown strategy {name}")
    now_hold = research.current_target(cd, strat)
    n = len(cd.days)
    px = {}
    for s in cd.coins:
        last = cd.c[s][-1]
        p = live.get(s)
        # Kraken (USD) and Binance (USDT) prices differ by a hair; a wild gap means a bad quote: keep the close
        px[s] = p if p and last and 0.5 < p / last < 2 else last
    nxt = research.Candles(cd.days + [cd.days[-1] + 86400])
    for s in cd.coins:
        p = px[s]
        nxt.o[s], nxt.h[s], nxt.l[s], nxt.c[s] = cd.o[s] + [p], cd.h[s] + [p], cd.l[s] + [p], cd.c[s] + [p]
    nxt.alt = {k: v + [v[-1] if v else None] for k, v in getattr(cd, "alt", {}).items()}
    strat2 = research.by_name(name)
    next_hold = research.current_target(nxt, strat2)
    entry, exit_ = getattr(strat, "entry", 20), getattr(strat, "exit", 10)
    mult, regime = getattr(strat, "atr_mult", 3.0), getattr(strat, "regime", None) or 50
    i = n  # the pretend day
    btc_xs = nxt.closes("BTC", i, regime) or []
    btc_avg = sum(btc_xs) / len(btc_xs) if btc_xs else 0.0
    btc_ok = bool(btc_xs) and btc_xs[-1] > btc_avg
    rows = []
    for s in cd.coins:
        p = px[s]
        if p is None:
            continue
        highs = [x for x in nxt.h[s][i - entry:i] if x is not None]
        lows = [x for x in nxt.l[s][i - exit_:i] if x is not None]
        hi, lo = (max(highs) if highs else None), (min(lows) if lows else None)
        a = research.atr(nxt, s, i)
        peak = getattr(strat2, "peak", {}).get(s)
        stop = peak - mult * a if peak and a else None
        xs = nxt.closes(s, i, 31)
        held, will = s in now_hold, s in next_hold
        state = ("holding" if held and will else "would sell" if held else "would buy" if will
                 else "breakout, no slot" if hi and p > hi and btc_ok
                 else "near breakout" if hi and p >= hi * 0.97 and btc_ok else "waiting")
        exit_level = max(x for x in (lo, stop) if x) if (lo or stop) else None
        rows.append({"symbol": s, "price": p, "high": hi, "low": lo, "stop": stop, "held": held, "state": state,
                     "to_breakout_pct": round((hi / p - 1) * 100, 2) if hi else None,
                     "to_exit_pct": round((exit_level / p - 1) * 100, 2) if exit_level else None,
                     "strength_30d": round((xs[-1] / xs[0] - 1) * 100, 1) if xs else None})
    order = {"would sell": 0, "would buy": 1, "holding": 2, "breakout, no slot": 3, "near breakout": 4, "waiting": 5}
    rows.sort(key=lambda r: (order[r["state"]], r["to_breakout_pct"] if r["to_breakout_pct"] is not None else 99))
    return {"ts": time.time(), "strategy": name, "days": n, "last_day": _day(cd.days[-1]),
            "btc": {"price": btc_xs[-1] if btc_xs else None, "avg": btc_avg, "days": regime, "ok": btc_ok,
                    "gap_pct": round((btc_xs[-1] / btc_avg - 1) * 100, 2) if btc_avg else 0.0},
            "entry_days": entry, "exit_days": exit_,
            "preview": {"hold_now": sorted(now_hold), "hold_next": sorted(next_hold),
                        "buy": sorted(set(next_hold) - set(now_hold)), "sell": sorted(set(now_hold) - set(next_hold))},
            "rows": rows}


ALT_NAMES = {"fear_greed": "Fear & Greed index", "stablecoins": "Stablecoin supply (USD)", "hashrate": "Bitcoin hash rate",
             "wiki:market": "Wikipedia views: Cryptocurrency"}


class DataCollector(Agent):
    id = "collector"
    name = "Data Collector"
    role = "Collects free data with years of history: Fear & Greed, futures funding, Wikipedia views, stablecoins, hash rate"
    inputs = ["src_free"]
    cadence = "once a day, after 00:15 UTC"
    explain = ("Free, no keys. Once a day it downloads five kinds of data that reach back years, so they can be tested "
               "before anyone trusts them: the Crypto Fear & Greed index (since 2018), each coin's perpetual futures "
               "funding rate on Binance (since 2019: how crowded leveraged buyers are), Wikipedia page views for "
               "crypto articles (since 2015: public attention), total stablecoin supply (DefiLlama: cash waiting to buy) "
               "and Bitcoin's hash rate (miners' confidence). Each value is used one day late in tests, so nothing "
               "peeks into the future. A source that fails keeps its stored history.")
    outputs = "daily series for the Pattern Hunter and the alternative-data strategies in the Researcher"

    async def run(self, bb: Blackboard) -> None:
        e = self.ctx
        st = e.db.get("alt_status") or {}
        self.next_run = next_utc_midnight() + 900
        if not st:
            self.summary = ("simulated data (offline mode)" if e.settings.simulate
                            else "first download runs with the next history test")
            return
        days = st.get("days", {})
        errors = st.get("errors", [])
        rows = []
        for key, n in sorted(days.items()):
            series = e.db.get(f"alt:{key}") or []
            last = series[-1] if series else None
            label = ALT_NAMES.get(key) or (f"Funding {key[8:]}" if key.startswith("funding:")
                                           else f"Wikipedia views: {key[5:]}" if key.startswith("wiki:") else key)
            rows.append([label, n, _day(series[0][0]) if series else None, _day(last[0]) if last else None,
                         None if not last else (round(last[1] * 100, 4) if key.startswith("funding:") else round(last[1], 2))])
        stale = []  # a source that missed one day still has yesterday's value; only old data is a real problem
        for x in errors:
            series = e.db.get(f"alt:{x.split(' (')[0]}") or []
            if not series or time.time() - series[-1][0] > 3 * 86400:
                stale.append(x)
        if stale:
            self.status = "warn"
        self.summary = (f"{len(days)} series, {sum(days.values())} days stored"
                        + (f"; {len(errors)} missed today" if errors else "")
                        + (f", {len(stale)} out of date" if stale else ""))
        self.detail = {
            "did": [f"Refreshed on {_day(st.get('ts', time.time()))} at {_hm(st.get('ts', time.time()))}",
                    f"{len(days) - len(errors)} series updated, {len(errors)} failed (their history stays)"]
                   + [f"Failed: {x}" for x in errors[:6]],
            "facts": [["Funding values", "% per 8 hours; 0.01 is neutral, above 0.03 is crowded"]],
            "table": {"cols": ["series", "days", "from", "to", "latest"], "rows": rows},
        }


class NewsHunter(Agent):
    id = "news"
    name = "News Hunter"
    role = "Reads crypto news, tags the coins and spots hacks and delistings for the Guardian"
    inputs = ["src_news"]
    uses_ai = True
    can_disable = True
    default_model = "fast"
    cadence = "every 5 minutes"
    explain = ("Collects new headlines from the news feeds (duplicates and anything older than 12 hours are skipped) "
               "and sends them to Claude Haiku in batches of up to 25, about a cent per batch. Claude tags the coins, "
               "rates direction and impact, and marks the two events that matter for real money: a hack of a coin's "
               "own chain or protocol, and a delisting by a major exchange. Those go to the Guardian. Without AI or "
               "budget it falls back to free keyword rules.")
    outputs = "rated headlines; hack and delisting events for the Guardian; news tone per coin (shown, not traded)"
    default_prompt = (
        "You are a sharp crypto news analyst protecting a small trading bot's real money. For each headline, list "
        "the affected tickers from this set only: {symbols} (BTC for broad crypto news). sentiment is -1 (very "
        "bearish) to 1 (very bullish) for those tickers over the next days. impact is 0 (noise) to 1 "
        "(market-moving). event is one word: hack, delisting, regulation, etf, listing, partnership, macro, "
        "upgrade, rumor, other. Use hack only when that coin's own chain, bridge or protocol was exploited or "
        "drained (an exchange hack that merely involves BTC is not a BTC hack), and delisting only when a major "
        "exchange removes that coin. takeaway: one sentence on what matters most for someone holding these coins.")

    def __init__(self, ctx):
        super().__init__(ctx)
        self.pending: list = []
        self.last_batch: dict = {}

    def on_disable(self, bb: Blackboard) -> None:
        bb.news = {}
        self.pending = []

    def queue(self, headlines: list) -> None:
        self.pending.extend(headlines)

    async def run(self, bb: Blackboard) -> None:
        batch, self.pending = self.pending[:25], self.pending[25:]
        if batch:
            use_ai = self.ctx.settings["ai"]["news_ai"]
            scored = (await self._score_ai(batch) if use_ai else None) or self._score_rules(batch)
            bb.news_events = (scored + bb.news_events)[:150]
            guard = [ev for ev in scored if ev["event"] in ("hack", "delisting") and ev["symbols"]]
            self.last_batch = {"ts": time.time(), "read": len(batch), "rated": len(scored),
                               "ai": bool(scored and scored[0].get("ai")), "guard": len(guard)}
            for ev in scored:
                if ev["impact"] >= 0.6 and ev["symbols"]:
                    arrow = "bullish" if ev["sentiment"] > 0 else "bearish"
                    self.say(f"{arrow.upper()} {', '.join(ev['symbols'])}: {ev['title']} ({ev['event']})")
        now = time.time()
        agg: dict[str, float] = {}
        for ev in bb.news_events:
            w = math.exp(-(now - ev["ts"]) / (6 * 3600))
            for s in ev["symbols"]:
                agg[s] = agg.get(s, 0.0) + ev["sentiment"] * ev["impact"] * w
        bb.news = {s: math.tanh(v) for s, v in agg.items()}
        day = [ev for ev in bb.news_events if now - ev["ts"] < 86400]
        flagged = [ev for ev in day if ev["event"] in ("hack", "delisting") and ev["symbols"]]
        self.summary = (f"{len(day)} headlines rated in 24h"
                        + (f"; {len(flagged)} hack/delisting alerts" if flagged else "; no hack or delisting news"))
        lb = self.last_batch
        top = sorted(bb.news.items(), key=lambda kv: -abs(kv[1]))[:8]
        self.detail = {
            "did": ([f"Last batch {_hm(lb['ts'])}: read {lb['read']} new headlines, rated {lb['rated']} "
                     f"({'Claude' if lb['ai'] else 'keyword rules'}), {lb['guard']} sent to the Guardian"] if lb else
                    ["Waiting for the first new headlines"]) + [f"{len(self.pending)} headlines queued"],
            "facts": [["News tone per coin (info only)", ", ".join(f"{s} {v:+.2f}" for s, v in top) or "none"],
                      ["Guardian events today", len(flagged)]],
            "table": {"cols": ["time", "coins", "event", "tone", "impact", "headline"],
                      "rows": [[_hm(ev["ts"]), ", ".join(ev["symbols"]), ev["event"], round(ev["sentiment"], 2),
                                round(ev["impact"], 2), ev["title"]] for ev in bb.news_events[:15]]},
        }

    async def _score_ai(self, batch) -> list[dict] | None:
        symbols = self.ctx.news_symbols()
        lines = "\n".join(f"{i}. [{h.source}] {h.title}" for i, h in enumerate(batch))
        res = await self.ctx.llm.json_call(
            self.name, self.prompt.replace("{symbols}", ", ".join(symbols)), lines, NEWS_SCHEMA, max_tokens=2000,
            model=self.model,
        )
        if not res or res.get("_over_budget"):
            if res and res.get("_over_budget"):
                self.summary = "AI budget reached: using keyword rules"
            return None
        self.cost += res.get("_cost", 0)
        out = []
        for it in res.get("items", []):
            i = it.get("index")
            if not isinstance(i, int) or not 0 <= i < len(batch):
                continue
            h = batch[i]
            syms = [s for s in it.get("symbols", []) if s in symbols]
            out.append({"ts": h.ts, "source": h.source, "title": h.title, "link": h.link, "symbols": syms,
                        "sentiment": max(-1, min(1, float(it.get("sentiment", 0)))),
                        "impact": max(0, min(1, float(it.get("impact", 0)))), "event": it.get("event", "other"), "ai": True})
        if res.get("takeaway"):
            self.say(f"Takeaway: {res['takeaway']}")
        return out

    def _score_rules(self, batch) -> list[dict]:
        out = []
        for h in batch:
            low = h.title.lower()
            pos = sum(w in low for w in POSITIVE)
            neg = sum(w in low for w in NEGATIVE)
            if not h.symbols or pos == neg:
                continue
            event = ("delisting" if "delist" in low else "hack" if any(w in low for w in GUARD_WORDS) else "keyword")
            out.append({"ts": h.ts, "source": h.source, "title": h.title, "link": h.link, "symbols": h.symbols,
                        "sentiment": 0.6 if pos > neg else -0.6, "event": event, "ai": False,
                        "impact": 0.7 if event != "keyword" and pos < neg else 0.4})
        return out


NEWS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "symbols": {"type": "array", "items": {"type": "string"}},
                    "sentiment": {"type": "number"},
                    "impact": {"type": "number"},
                    "event": {"type": "string"},
                },
                "required": ["index", "symbols", "sentiment", "impact", "event"],
                "additionalProperties": False,
            },
        },
        "takeaway": {"type": "string"},
    },
    "required": ["items", "takeaway"],
    "additionalProperties": False,
}

POSITIVE = ["surge", "soar", "rally", "record", "approve", "approval", "etf", "partnership", "acquire", "merger",
            "beat", "upgrade", "bull", "adopt", "launch", "jump", "gain", "inflow"]
GUARD_WORDS = ["hack", "exploit", "drained", "stolen"]
NEGATIVE = ["hack", "exploit", "delist", "drained", "stolen", "ban", "lawsuit", "sue", "crash", "plunge", "fall", "drop",
            "sell-off", "selloff", "downgrade", "bear", "fraud", "outage", "investigation", "outflow", "liquidat", "miss"]


# --------------------------------------------------------------------------------------------
# Research
# --------------------------------------------------------------------------------------------


class PatternHunter(Agent):
    id = "patterns"
    name = "Pattern Hunter"
    role = "Tests every free signal against the next week's price on all history, with controls against luck"
    inputs = ["collector", "src_binance"]
    cadence = "once a day, after the history test"
    explain = ("Plain math, free. For 15 signals (funding, Fear & Greed, stablecoin growth, hash rate, Wikipedia "
               "attention, coin strength, volatility, and two random control signals) it asks one question on all "
               "history: did the signal's value say anything about the next 7 days' price? It uses rank correlation "
               "on non-overlapping weeks, corrects for testing many signals at once (Bonferroni), and demands the "
               "same direction in both halves of history and in most years. Random controls must come out as "
               "\"chance\", or the test itself is broken. This is how pizza-index ideas get sorted from real ones: "
               "a pattern without a mechanism usually fails exactly these checks.")
    outputs = "which signals are real patterns; the real ones become entry rules the Researcher tests as strategies"

    async def run(self, bb: Blackboard) -> None:
        pat = self.ctx.db.get("patterns") or {}
        self.next_run = next_utc_midnight() + 900
        if not pat.get("rows"):
            self.summary = "first test runs with the next history test"
            return
        rows = pat["rows"]
        found = [r for r in rows if r.get("verdict") == "pattern"]
        hints = [r for r in rows if r.get("verdict") == "hint"]
        controls = [r for r in rows if "random" in r.get("name", "").lower()]
        ctl_ok = all(r.get("verdict") in ("chance", "no data") for r in controls)
        if not ctl_ok:
            self.status = "warn"
        self.summary = (f"{pat.get('tested', len(rows))} signals tested: {len(found)} real, {len(hints)} hints"
                        + ("" if ctl_ok else "; a random control looks significant, so treat results carefully"))
        self.detail = {
            "did": [f"Ran on {_day(pat.get('ts', time.time()))}" + (" (simulated data)" if pat.get("simulated") else ""),
                    f"Tested {pat.get('tested', len(rows))} signals on up to {max((r.get('weeks') or 0) for r in rows)} "
                    f"non-overlapping weeks of history",
                    f"Random controls: {'all came out as chance, the test works' if ctl_ok else 'one looks significant'}",
                    ("Real patterns: " + "; ".join(f"{r['name']} ({r.get('direction', '')})" for r in found))
                    if found else "No signal passes every check (that's normal: most ideas are noise)"],
            "facts": [["Pattern", "significant after correction, same direction in both halves and most years"],
                      ["Hint", "promising but fails one check"], ["Chance", "indistinguishable from noise"]],
            "table": {"cols": ["signal", "verdict", "rank corr", "p (corrected)", "years same way", "top-bottom spread %/wk"],
                      "rows": [[r["name"], r.get("verdict"), r.get("corr"), r.get("p_corrected"), r.get("years_same"),
                                r.get("spread_pct")] for r in rows]},
        }


class ThinkTank(Agent):
    id = "thinktank"
    name = "Think Tank"
    role = "Invents original strategies with Claude and evolution, and tests them endlessly against the market"
    inputs = ["src_binance"]
    uses_ai = True
    can_disable = True
    default_model = "claude-sonnet-5-5"
    cadence = "tests ideas every few minutes; asks Claude for new ones every 6 hours"
    explain = ("A think tank with no standard indicators. Every 6 hours it asks Claude for 6 original ideas borrowed "
               "from physics, biology, information theory, game theory or anything else, written in a small, safe "
               "formula language (nothing is executed). In between, evolution mutates and crosses the best ideas and "
               "tries random ones, endlessly. Every idea is judged on daily history since 2017 with real fees: the "
               "first 60% of the days to discover, the last 40% kept secret to judge. A candidate must beat Bitcoin "
               "on both, survive twice the fees, pass the correction for the thousands of ideas tried and keep working "
               "without a third of the coins. Ralph's price echo and a moon-phase control are in the race too. "
               "Nothing here trades: a candidate can be added to the history lab, where it faces the full bar.")
    outputs = "candidates for the history lab; ideas Claude builds on next time"
    default_prompt = ("You are a bold, rigorous quant inventor. Original concepts only, each with a mechanism you can "
                      "explain in plain words. Prefer simple formulas that capture one idea well.")

    def __init__(self, ctx):
        super().__init__(ctx)
        self.queued = False
        self._inventing = False

    def on_disable(self, bb: Blackboard) -> None:
        pass

    async def run(self, bb: Blackboard) -> None:
        e = self.ctx
        st = e.tt or {}
        ai = e.db.get("tt_ai") or {}
        every = 6 * 3600
        due = self.queued or time.time() - ai.get("last", 0) > every
        self.next_run = ai.get("last", time.time()) + every
        if due and e.llm.available and not self._inventing and st.get("board") is not None:
            self.queued = False
            self._inventing = True
            asyncio.create_task(self._invent())
        c = st.get("counts", {})
        paused = e.db.get("tt_paused")
        self.summary = ("paused · " if paused else "") + (
            f"{c.get('tested', 0)} ideas tested · {c.get('candidate', 0)} candidates · "
            f"{c.get('promising', 0)} promising" if c else "first ideas are tested a few minutes after the start")
        board = st.get("board", [])
        echo = [x for x in board if x.get("origin") == "Ralph"]
        self.detail = {
            "did": [f"Last batch {_hm(st['ts'])}" if st.get("ts") else "no batch yet",
                    f"Claude's last ideas: {', '.join(ai.get('names', [])[:6]) or 'none yet'}"
                    + (f" ({_hm(ai['last'])})" if ai.get("last") else ""),
                    f"Ideas waiting: {len(e.db.get('tt_queue') or [])}"]
                   + [f"Ralph's echo: {x['name']} -> {x['res']['verdict']}" for x in echo],
            "facts": [["Discovery", f"{(st.get('periods') or {}).get('from', '?')} to {(st.get('periods') or {}).get('cut', '?')}"],
                      ["Holdout (secret)", f"{(st.get('periods') or {}).get('cut', '?')} to {(st.get('periods') or {}).get('to', '?')}"],
                      ["Claude ideas so far", ai.get("total", 0)], ["Claude cost", f"${ai.get('cost', 0):.2f}"]],
            "table": {"cols": ["idea", "from", "verdict", "discovery %/yr", "holdout %/yr", "signal"],
                      "rows": [[x["name"], x.get("origin"), x["res"]["verdict"], x["res"]["disc"].get("cagr_pct"),
                                x["res"]["hold"].get("cagr_pct"), x["res"].get("ic_hold")] for x in board[:15]]},
        }
        if c.get("candidate"):
            self.status = "ok"

    async def _invent(self) -> None:
        from .. import thinktank
        e = self.ctx
        try:
            st = e.tt or {}
            board = sorted(st.get("board", []), key=lambda x: -x["res"]["fitness"])
            dead = [x["name"] for x in st.get("recent", []) if x["verdict"] == "dead" and x["origin"] == "Claude"]
            res = await e.llm.json_call(self.name, self.prompt, thinktank.ai_prompt(board, dead, st.get("counts", {}).get("tested", 0)),
                                        thinktank.AI_SCHEMA, max_tokens=6000, model=self.model)
            ai = e.db.get("tt_ai") or {}
            ai["last"] = time.time()
            if not res or res.get("_over_budget"):
                ai["note"] = "no ideas this time: " + ("AI budget used up" if res else
                                                       ((e.llm.last_error or {}).get("why") or "no answer"))
                e.db.set("tt_ai", ai)
                return
            ideas = []
            for raw in res.get("ideas", [])[:8]:
                idea = thinktank.clean({**raw, "origin": "Claude"})
                if idea:
                    ideas.append(idea)
            self.cost += res.get("_cost", 0)
            e.db.set("tt_queue", (e.db.get("tt_queue") or []) + ideas)  # appended: the search takes from the front
            ai.update(total=ai.get("total", 0) + len(ideas), names=[i["name"] for i in ideas],
                      cost=ai.get("cost", 0) + res.get("_cost", 0), note=f"{len(ideas)} ideas queued")
            e.db.set("tt_ai", ai)
            self.say(f"New ideas from Claude: " + "; ".join(f"{i['name']} ({i['inspiration']})" for i in ideas))
        finally:
            self._inventing = False


class Researcher(Agent):
    id = "researcher"
    name = "Researcher"
    role = "Re-tests every strategy on all daily history since 2017, and keeps the live strategy honest"
    inputs = ["src_binance", "collector", "patterns"]
    cadence = "once a day, after 00:15 UTC"
    explain = ("Plain math, free. Every night it re-runs about 37 strategies on all daily history since 2017 for the "
               "22 big coins Fusion trades, with Fusion's fee and a spread estimate on every trade, and compares each "
               "with simply holding Bitcoin. \"Robust\" means: better risk-adjusted than holding Bitcoin in both "
               "halves of history and in most calendar years, and a deflated Sharpe ratio (which corrects for having "
               "tried many strategies) of at least 80%. If the strategy trading your money fails that three nights "
               "in a row, the Daily Brain switches to the best robust one.")
    outputs = "the ranking of strategies; the robust ones may trade the real money; a warning or switch for the brain"

    async def run(self, bb: Blackboard) -> None:
        e = self.ctx
        res = e.db.get("research") or {}
        self.next_run = next_utc_midnight() + 900
        if not res.get("rows"):
            self.summary = "first history test pending (Research tab or tonight)"
            return
        b = e.brain()
        rows = res["rows"]
        robust = sorted([r for r in rows if r["robust"]], key=lambda r: -(r["full"].get("sharpe") or 0))
        mine = next((r for r in rows if r["name"] == b.get("strategy")), None)
        btc = next((r for r in rows if r["name"] == research.HoldBTC.name), None)
        if mine and not mine["robust"]:
            self.status = "warn"
        self.summary = (f"{len(robust)} of {len(rows)} strategies robust; live strategy "
                        + ("passes" if mine and mine["robust"] else f"fails today ({b.get('weak_days', 0)} of 3 nights in a row)" if mine
                           else "none"))

        def line(r):
            f = r["full"]
            return [r["name"], f.get("cagr_pct"), f.get("max_dd_pct"), f.get("sharpe"),
                    f"{r.get('years_won', '–')}/{r.get('years_total', '–')}",
                    None if r.get("skill_prob") is None else round(r["skill_prob"] * 100), "yes" if r["robust"] else "no"]
        alt = [r for r in rows if r.get("group") == "Trend + alternative data"]
        self.detail = {
            "did": [f"Tested {res.get('strategies_tested', len(rows))} strategies on {res.get('days')} days "
                    f"({res.get('from')} to {res.get('to')}), {len(res.get('coins', []))} coins"
                    + (" (simulated)" if res.get("simulated") else ""),
                    f"{len(robust)} pass every robustness check",
                    (f"Live strategy \"{mine['name']}\": {mine['full'].get('cagr_pct')}%/yr, worst drop "
                     f"{mine['full'].get('max_dd_pct')}%, {'robust' if mine['robust'] else 'NOT robust today'}")
                    if mine else "No strategy trades the real money yet",
                    (f"Holding Bitcoin for comparison: {btc['full'].get('cagr_pct')}%/yr, worst drop "
                     f"{btc['full'].get('max_dd_pct')}%") if btc else ""],
            "facts": [["Ran", f"{_day(res.get('ts', time.time()))} {_hm(res.get('ts', time.time()))}"],
                      ["Cost per side", f"{res.get('cost_per_side_pct', 0.4)}%"],
                      ["Alternative-data strategies", "; ".join(f"{r['name'].split(', ')[-1]}: "
                                                               f"{r['full'].get('cagr_pct')}%/yr{' ✓' if r['robust'] else ''}"
                                                               for r in alt) or "none yet"],
                      ["Fast lab (4-hour candles)", self._fast(e.db.get("fastlab") or {})]],
            "table": {"cols": ["strategy", "%/yr", "worst drop %", "sharpe", "years won", "skill %", "robust"],
                      "rows": [line(r) for r in ([mine] if mine else []) + [r for r in robust if r is not mine][:8]
                               + ([btc] if btc else [])]},
        }


    @staticmethod
    def _fast(f: dict) -> str:
        if not f.get("rows"):
            return "not run yet (Research tab, or nightly after the history test)"
        fast = [r for r in f["rows"] if r["group"].startswith("Fast")]
        ok = [r for r in fast if r["robust"]]
        best = max(fast, key=lambda r: r["full"].get("sharpe") or -9, default=None)
        return (f"{len(ok)} of {len(fast)} fast strategies robust at {f.get('cost_per_side_pct')}% per side"
                + (f"; best {best['name']} {best['full'].get('cagr_pct')}%/yr" if best else ""))


# --------------------------------------------------------------------------------------------
# Guard
# --------------------------------------------------------------------------------------------


class Guardian(Agent):
    id = "guardian"
    name = "Guardian"
    role = "Blocks buys after hack or delisting news, and sells a held coin at once when two witnesses agree"
    inputs = ["news", "radar", "professor"]
    kind = "gate"
    cadence = "every minute"
    explain = ("Plain code, no AI. Strategies tested on price history can't see a hack coming, so this agent covers "
               "that gap. A hack or delisting headline about a coin (from the News Hunter) blocks buying it for 3 "
               "days; a concrete warning from the Professor blocks it for 1 day. If the bot holds that coin and two "
               "independent witnesses agree (two news sources, or one news source plus the Fusion Scout's crash "
               "alert), it sells right away instead of waiting for the next daily decision. Bitcoin and Ethereum are "
               "never sold this way: exchange hacks mention them all the time.")
    outputs = "buy blocks for the Daily Brain; emergency sells through the Live Desk"

    async def run(self, bb: Blackboard) -> None:
        e = self.ctx
        g = e.guard()
        self.next_run = time.time() + 60
        held = set(e.db.get("live_qty", {}))
        if g:
            self.status = "warn"
        self.summary = (("blocking " + ", ".join(f"{s} ({x['reason']})" for s, x in g.items())) if g
                        else "all clear: no hack, delisting or crash on any coin")
        self.detail = {
            "did": [f"Checked {len(bb.news_events)} rated headlines and {len(e.shocks)} crash alerts ({_hm(time.time())})",
                    (f"Active blocks: {', '.join(g)}" if g else "No active blocks"),
                    (f"Held coins under watch: {', '.join(sorted(held & set(g)))}" if held & set(g)
                     else "None of the bot's coins is affected")],
            "facts": [["Block after hack/delisting news", f"{e.GUARD_HOURS} hours"],
                      ["Block after a Professor warning", "24 hours"],
                      ["Emergency sell", "held coin, 2 witnesses, never BTC or ETH"]],
            "table": {"cols": ["coin", "reason", "witnesses", "blocked until", "sold", "headline"],
                      "rows": [[s, x["reason"], ", ".join(x.get("sources", [])), _hm(x["until"]) + " " + _day(x["until"]),
                                "yes" if x.get("sold") else "", (x.get("titles") or [""])[0]] for s, x in g.items()]},
        }


PROF_SCHEMA = {
    "type": "object",
    "properties": {
        "assessment": {"type": "string"},
        "block": {"type": "array", "items": {"type": "object", "properties": {
            "symbol": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["symbol", "reason"], "additionalProperties": False}},
        "watch": {"type": "string"},
        "idea": {"type": "string"},
    },
    "required": ["assessment", "block", "watch", "idea"],
    "additionalProperties": False,
}


class Professor(Agent):
    id = "professor"
    name = "The Professor"
    role = "Reviews each daily decision with Claude, can veto a buy for a concrete danger, writes your daily briefing"
    inputs = ["news", "trend", "researcher", "patterns"]
    uses_ai = True
    can_disable = True
    default_model = "deep"
    cadence = "once a day, after the Daily Brain decides"
    explain = ("The expensive brain, used once a day (a few cents). Right after the Daily Brain's decision it gets the "
               "whole picture: the decision and why, Trend Watch's levels, the history test, real patterns, Guardian "
               "blocks, the day's top news, Fear & Greed and your account. It writes a short assessment for your "
               "briefing, names what to watch, and may block a coin for 24 hours, but only for a concrete, "
               "coin-specific danger (hack, exploit, delisting, regulator action, insolvency, token unlock dump). "
               "It can't buy, can't force a sell and can't change the strategy: the tested rules stay in charge.")
    outputs = "daily assessment and briefing; 24-hour buy blocks for the Guardian"
    default_prompt = (
        "You are a sober quant and risk manager reviewing a small crypto bot that trades real money (spot only, "
        "no leverage, Bitpanda Fusion, fee about 0.25% per side). Its strategy was chosen because it passed a "
        "strict history test since 2017: trust the rules for normal market moves. Your job is what the rules "
        "can't see. Given today's brief, return: assessment (3-4 plain sentences: what the bot did and why it "
        "makes sense or not, the market regime, the main risk), block (coins with a concrete, coin-specific danger "
        "visible in the brief: hack, exploit, delisting, regulator action, insolvency, a large token unlock; "
        "never block for ordinary price moves or vague fear; usually empty), watch (one sentence: what could change "
        "tomorrow's decision), idea (one concrete, testable rule the Researcher could add to the history test).")

    def __init__(self, ctx):
        super().__init__(ctx)
        self.queued = False

    def run_now(self) -> None:
        self.queued = True

    def on_disable(self, bb: Blackboard) -> None:
        pass

    async def run(self, bb: Blackboard) -> None:
        e = self.ctx
        last = e.db.get("professor_last") or {}
        b = e.brain()
        due = self.queued or (b.get("ts") and b["ts"] > last.get("ts", 0) and time.time() - b["ts"] < 6 * 3600)
        self.next_run = 0 if due else next_utc_midnight() + 600
        retry = self.__dict__.get("_retry_at", 0)
        if due and time.time() < retry:
            self.next_run = retry  # the last call failed: wait an hour before the next try
            return
        if last:
            self._show(last)
        else:
            self.summary = "first review after the next daily decision"
        if not due:
            return
        if not e.settings["ai"]["professor_on"]:
            self.summary = "reviews paused in Controls"
            self.queued = False
            return
        asked, self.queued = self.queued, False
        brief = self.brief()
        res = await e.llm.json_call(self.name, self.prompt, str(brief), PROF_SCHEMA, deep=True, max_tokens=8000,
                                    model=self.model)
        if not res or res.get("_over_budget"):
            if not e.llm.available:
                why = "no AI key"
            elif res and res.get("_over_budget"):
                why = "today's AI budget is used up"
            else:
                why = "the AI call failed: " + ((e.llm.last_error or {}).get("why") or "no usable answer")
                self.queued = asked  # a review you asked for is tried again too
                self._retry_at = self.next_run = time.time() + 3600
                self.summary = f"resting ({why[:100]}), next try in an hour"
                return
            e.db.set("professor_last", {**last, "ts": time.time(), "skipped": why})
            self.summary = f"resting ({why})"
            return
        self.cost += res.get("_cost", 0)
        blocks = [x for x in res.get("block", []) if x.get("symbol") in research.UNIVERSE]
        out = {"ts": time.time(), "assessment": res.get("assessment"), "watch": res.get("watch"),
               "idea": res.get("idea"), "block": blocks, "cost": round(res.get("_cost", 0), 4),
               "brief": {k: brief[k] for k in ("decision", "btc", "tonight", "guard") if k in brief}}
        e.db.set("professor_last", out)
        for x in blocks:
            e.professor_block(x["symbol"], x.get("reason", "Professor's warning"))
        self.say(f"Review: {out['assessment']}")
        if out["watch"]:
            self.say(f"Watch: {out['watch']}")
        if out["idea"]:
            self.say(f"Research idea: {out['idea']}")
        self._show(out)

    def _show(self, p: dict) -> None:
        self.summary = (p.get("assessment") or p.get("skipped") or "")[:140]
        self.detail = {
            "did": [f"Reviewed on {_day(p.get('ts', time.time()))} at {_hm(p.get('ts', time.time()))}"
                    + (f" for {p['cost']:.3f} USD" if p.get("cost") else ""),
                    ("Blocked for 24h: " + "; ".join(f"{x['symbol']} ({x['reason']})" for x in p["block"]))
                    if p.get("block") else "Blocked nothing (the tested rules stay in charge)"],
            "assessment": p.get("assessment"), "watch": p.get("watch"), "idea": p.get("idea"),
            "facts": [["What it was given", ", ".join(p.get("brief", {})) or "–"]],
        }

    def brief(self) -> dict:
        e = self.ctx
        b, w, t = e.brain(), e.wallet or {}, e.trend or {}
        fg = (e.db.get("alt:fear_greed") or [[0, None]])[-1][1]
        res = e.db.get("research") or {}
        mine = next((r for r in res.get("rows", []) if r["name"] == b.get("strategy")), None)
        return {
            "decision": {"strategy": b.get("strategy"), "holds": b.get("target"), "note": b.get("note"),
                         "btc_above_average": b.get("btc_ok")},
            "btc": t.get("btc"),
            "tonight": t.get("preview"),
            "coins": [{k: r[k] for k in ("symbol", "state", "to_breakout_pct", "to_exit_pct", "strength_30d")}
                      for r in t.get("rows", [])[:12]],
            "history_test": None if not mine else {"cagr_pct": mine["full"].get("cagr_pct"),
                                                   "worst_drop_pct": mine["full"].get("max_dd_pct"), "robust": mine["robust"]},
            "patterns": [r["name"] for r in (e.db.get("patterns") or {}).get("rows", []) if r.get("verdict") == "pattern"],
            "guard": {s: g["reason"] for s, g in e.guard().items()},
            "news": [{k: ev[k] for k in ("title", "symbols", "sentiment", "impact", "event")}
                     for ev in e.bb.news_events[:15]],
            "fear_greed": fg,
            "account": {k: w.get(k) for k in ("currency", "total", "fiat", "bot_value", "bot_edge")},
        }


# --------------------------------------------------------------------------------------------
# Decide and execute
# --------------------------------------------------------------------------------------------


class DailyBrain(Agent):
    id = "brain"
    name = "Daily Brain"
    role = "Trades the real money with the strategy that passed the history test, once per daily candle"
    inputs = ["researcher", "trend", "guardian"]
    cadence = "once a day, right after 00:00 UTC"
    explain = ("Plain code, no AI. Right after each daily candle closes (00:00 UTC, 02:00 in Switzerland in summer) "
               "it replays its strategy on all history up to yesterday, exactly as in the history test, and gets "
               "the coins to hold. It sells the bot's coins the strategy no longer wants, then buys the wanted ones "
               "with an equal share of the account each, in orders inside your per-order limit. Coins the Guardian "
               "blocks are skipped. If it misses midnight (Mac off), it catches up when the bot starts again. "
               "Changing the money limits makes it decide again right away.")
    outputs = "the target coins and orders for the Risk Officer and the Live Desk"

    async def run(self, bb: Blackboard) -> None:
        e = self.ctx
        b = e.brain()
        self.next_run = next_utc_midnight() + 60
        if not b.get("on"):
            self.status = "off" if b.get("strategy") else "idle"
            self.summary = "off: nothing trades the real money" if b.get("strategy") else "pick a strategy in Research"
            self.detail = {"did": ["Not running: switch it on in the Research tab"]}
            return
        if e.mode != "live":
            self.summary = "standby: switch LIVE on to trade"
        elif e.kill_switch:
            self.status, self.summary = "warn", "kill switch on: no decisions"
        else:
            hold = ", ".join(f"{s} {round(w * 100)}%" for s, w in (b.get("target") or {}).items()) or "cash"
            self.summary = f"{b['strategy']}: holds {hold}"
        self.detail = {
            "did": b.get("steps") or [b.get("note") or "first decision pending"],
            "facts": [["Strategy", b.get("strategy")],
                      ["Last decision", f"{_day(b['ts'])} {_hm(b['ts'])}" if b.get("ts") else "pending"],
                      ["Next decision", f"after {_hm(self.next_run)} (your time)"],
                      ["Bitcoin filter", "–" if b.get("btc_ok") is None else
                       f"above its {b.get('regime_days')}-day average" if b["btc_ok"] else f"below its {b.get('regime_days')}-day average: cash"],
                      ["Robustness", self._robust(e, b)]]
                     + ([["Last switch", f"from {b['switched']['from']} on {_day(b['switched']['ts'])}"]] if b.get("switched") else []),
            "target": b.get("target"),
        }


    @staticmethod
    def _robust(e, b) -> str:
        rows = (e.db.get("research") or {}).get("rows") or []
        mine = next((r for r in rows if r["name"] == b.get("strategy")), None)
        if not mine:
            return "not tested yet"
        if mine["robust"]:
            return "passes every check in the last history test"
        return f"fails a check today ({b.get('weak_days', 0)} of 3 nights; after 3 it switches to the best robust one)"


class FastTrader(Agent):
    id = "fast"
    name = "Fast Trader"
    role = "Trades a small, separate pot of real money with one fast rule, every 4 hours, next to the Daily Brain"
    inputs = ["researcher", "guardian"]
    cadence = "every 4 hours, 2 minutes after each 4-hour candle closes (UTC)"
    explain = ("Plain code, no AI. It has its own pot (an amount you set, which then grows or shrinks with its own "
               "gains and losses, or a share of your account) and its own coins. It never sells your coins or the "
               "Daily Brain's, never buys a coin the brain holds, and the brain leaves the pot's cash alone. After "
               "each 4-hour candle it asks its rule from the Fast Trader Lab what to hold, with its real positions "
               "(entry price, peak, entry time), exactly as in the test, and trades the difference through the Risk "
               "Officer. Each coin gets at least 32 so it stays above Fusion's 25 (some coins 30) minimum even after a drop.")
    outputs = "fast buys and sells for the Risk Officer and the Live Desk"

    async def run(self, bb: Blackboard) -> None:
        e = self.ctx
        st = e.fast.status()
        self.next_run = st["next"]
        cur = (e.wallet or {}).get("currency", "CHF")
        if not st["on"]:
            self.status = "off"
            self.summary = "off: switch it on in Research › Fast Trader Lab"
            self.detail = {"did": ["Not running: no fast trades"],
                           "facts": [["Rule", st["strategy"]], ["Pot", f"{st['pot']:.2f} {cur}"]]}
            return
        if e.mode != "live":
            self.summary = "standby: switch LIVE on to trade"
        elif e.kill_switch:
            self.status, self.summary = "warn", "kill switch on: no decisions"
        else:
            self.summary = f"pot {st['pot']:.2f} {cur}: {st['note'] or 'first decision at the next 4-hour candle'}"
        coins = {c["symbol"]: c for c in (e.wallet or {}).get("fast_coins", [])}
        self.detail = {
            "did": st["steps"] or ["First decision after the next 4-hour candle"],
            "facts": [["Rule", st["strategy"]], ["Passes every check", "yes" if st.get("robust") else "no: play money"],
                      ["Pot", f"{st['pot']:.2f} {cur}" + (" (your amount + its gains and losses)" if st["mode"] == "chf" else f" ({st['pct']:g}% of your account)")],
                      ["Coins at a time", st["slots"]], ["Gain or loss booked", f"{st['realized']:+.2f} {cur}"],
                      ["Trades closed", f"{st['trades']} ({st['wins']} won)"],
                      ["Next decision", f"after {_hm(st['next'])} (your time)"]],
            "table": {"cols": ["coin", "in at", "since", cur, "now", "gain"],
                      "rows": [[s, p["entry"], f"{_day(p['ts'])} {_hm(p['ts'])}", round(p["cost"], 2),
                                coins.get(s, {}).get("value"), coins.get(s, {}).get("pnl")] for s, p in st["pos"].items()]},
        }


class RiskOfficer(Agent):
    """Plain code, not AI: it can't be talked into a bad trade."""
    id = "risk"
    name = "Risk Officer"
    role = "Hard limits in plain code on every real order: caps, Fusion minimums, spread, fees, kill switch"
    inputs = ["brain", "fast"]
    kind = "gate"
    cadence = "on every real order"
    explain = ("Plain code, no AI, so nothing can talk it into a bad trade. Every real buy passes its checks: the "
               "kill switch, your cap on money in coins, your biggest-order limit, Fusion's minimum order (including "
               "the higher minimums Fusion revealed in rejections), the spread in Fusion's order book, and room for "
               "the fee (at most 99.5% of the cash, Fusion adds its fee on top). If cash is short and you allowed it, "
               "it sells your other coins first, biggest first, never ones the brain is about to buy. There is no "
               "code for margin, leverage or short selling: the account can never go below zero.")
    outputs = "approved order sizes, or a clear reason why an order was not sent"

    async def run(self, bb: Blackboard) -> None:
        e = self.ctx
        lv = e.settings["live"]
        cur = e.live.currency if e.live else lv["currency"]
        log = list(e.risk_log)[-12:]
        blocked = sum(1 for x in log if x["result"] != "sent")
        if e.kill_switch:
            self.status = "warn"
        self.summary = (f"cap {lv['max_invest']:.0f} {cur}, orders ≤ {lv['max_order']:.0f}, spread ≤ {lv['max_spread_pct']}%"
                        + (" · KILL SWITCH ON" if e.kill_switch else "") + (f" · {blocked} of last {len(log)} orders stopped" if blocked else f" · last {len(log)} orders passed" if log else ""))
        self.detail = {
            "did": [f"{x['time']} {x['order']}: {x['result']}" for x in reversed(log[-6:])] or ["No real order checked yet"],
            "facts": [["Most in coins", f"{lv['max_invest']:.0f} {cur}"], ["Biggest order", f"{lv['max_order']:.0f} {cur}"],
                      ["Max spread", f"{lv['max_spread_pct']}%"], ["Cash usable per order", "99.5% (fee room)"],
                      ["May sell your coins for cash", "yes" if lv.get("use_my_coins") else "no"],
                      ["Kill switch", "ON" if e.kill_switch else "off"]],
            "checks": list(reversed(log)),
        }


class LiveDesk(Agent):
    id = "livedesk"
    name = "Live Desk"
    role = "Places the real orders on Bitpanda Fusion and keeps the wallet, fills and fees in view"
    inputs = ["risk", "guardian", "src_fusion"]
    kind = "gate"
    cadence = "reads your account every 30 seconds"
    explain = ("Plain code, no AI. The only part of the bot that touches your money. It sends market orders to Bitpanda "
               "Fusion (about 0.25% fee per side), records every fill with its real fee, and remembers which coins "
               "the bot bought, so it only ever sells those (plus your own coins when cash is short and you allowed "
               "it). It reads your account every 30 seconds. Three failed orders in a row stop live trading for "
               "safety. The key it uses can read and trade, never withdraw.")
    outputs = "real orders and fills; the live wallet in the cockpit"

    async def run(self, bb: Blackboard) -> None:
        e = self.ctx
        w = e.wallet
        self.next_run = time.time() + 30
        if not w:
            self.status = "idle"
            self.summary = ("no Fusion key: add BITPANDA_FUSION_API_KEY to .env" if not e.settings.fusion_api_key
                            else "connecting to Fusion…")
            return
        if w.get("error"):
            self.status, self.summary = "warn", f"Fusion: {w['error'][:80]}"
            return
        cur = w["currency"]
        t = e.db.query("SELECT COUNT(*) n, COALESCE(SUM(fee),0) fees, COALESCE(SUM(notional),0) vol, MAX(ts) last "
                       "FROM trades WHERE mode='live'")[0]
        mode = "LIVE" if e.mode == "live" else "standby (not trading)"
        self.summary = (f"{mode}: {w['total']:.2f} {cur}, cash {w['fiat']:.2f}, bot coins {w['bot_value']:.2f}; "
                        f"{t['n']} real orders")
        recent = e.recent_trades(10)
        fee_pct = t["fees"] / t["vol"] * 100 if t["vol"] else None
        self.detail = {
            "did": [f"Read your account {round(time.time() - w['ts'])}s ago: {w['total']:.2f} {cur}",
                    f"{t['n']} real orders so far" + (f", last {_day(t['last'])} {_hm(t['last'])}" if t["last"] else ""),
                    f"Errors in a row: {e.live_errors} of 3"],
            "facts": [["Mode", mode], ["Fees paid", f"{t['fees']:.2f} {cur}" + (f" ({fee_pct:.2f}% of volume)" if fee_pct else "")],
                      ["Bot's coins", ", ".join(c["symbol"] for c in w["coins"]) or "none"],
                      ["Your own coins", ", ".join(c["symbol"] for c in w.get("own_coins", [])) or "none"]],
            "table": {"cols": ["time", "side", "coin", cur, "fee", "why"],
                      "rows": [[f"{_day(r['ts'])} {_hm(r['ts'])}", r["side"], r["symbol"], round(r["notional"], 2),
                                round(r["fee"] or 0, 3), r["reason"]] for r in recent]},
        }
