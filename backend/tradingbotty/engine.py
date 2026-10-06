"""The engine: owns the feeds, the real-money agent team, the daily brain and the live account.

There is no pretend money here. The history test (research.py) and the Pattern Hunter (patterns.py) simulate
strategies on past prices to choose and check the one strategy that trades the real account; everything else
either feeds that research, guards the real money, or executes real orders.
"""
from __future__ import annotations

import asyncio
import copy
import math
import re
import time
from collections import deque

from . import altdata, controls, fastlab, patterns, research
from .fasttrader import FastTrader as FastPot
from .agents.base import Blackboard, Source
from .agents.crew import (DailyBrain, DataCollector, FastTrader, FusionScout, Guardian, LiveDesk, NewsHunter,
                          PatternHunter, Professor, Researcher, RiskOfficer, TrendWatch)
from .brokers.bitpanda import BitpandaBroker
from .brokers.fusion import FusionBroker
from .bus import Bus
from .config import Settings
from .data.prices import PriceFeed
from .data.social import SocialFeed
from .data.universe import Universe
from .db import DB
from .llm import LLM, Budget

HISTORY_DAYS = 7          # minute prices kept for the coin charts
BRAIN_ID = "brain"        # trades.variant_id of real orders


def make_live_broker(settings: Settings):
    """The live venue from config.toml [live] broker: "fusion" (Bitpanda Fusion, ~0.25% fees) or "bitpanda" (app quotes)."""
    kind = settings["live"].get("broker", "fusion")
    currency = settings["live"]["currency"]
    if kind == "fusion":
        if not settings.fusion_api_key:
            raise ValueError("Add BITPANDA_FUSION_API_KEY to .env and restart first.")
        return FusionBroker(settings.fusion_api_key, currency)
    if kind == "bitpanda":
        if not settings.bitpanda_api_key:
            raise ValueError("Add BITPANDA_API_KEY to .env and restart first.")
        return BitpandaBroker(settings.bitpanda_api_key, currency)
    raise ValueError(f"unknown live broker {kind!r} in config.toml")


class Engine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = DB(settings.db_path)
        self.bus = Bus()
        # dashboard changes are saved in the database and laid over config.toml
        self._base_raw = copy.deepcopy(settings.raw)
        controls.apply(settings.raw, self.db.get("controls", {}))
        controls.apply(self._base_raw, {})
        src = self.db.get("sources", {})
        # the coins the daily brain may trade, plus any coin the bot still holds
        coins = list(dict.fromkeys([*research.UNIVERSE, *self.db.get("live_qty", {})]))
        self.prices = PriceFeed(coins, [], settings.simulate, self._log)
        self.social = SocialFeed(coins, settings.simulate, self._log)
        self.social.extra_symbols = list(research.UNIVERSE)
        self.universe = Universe(settings.simulate, self._log)
        self.fusion_coins: set[str] | None = None  # what Fusion can trade in our currency, once the key is checked
        self.wallet: dict | None = None            # the real Bitpanda account, refreshed every 30 seconds
        self._viewer = None                        # read-only Fusion connection while live trading is off
        if src.get("feeds"):
            self.social.feeds = src["feeds"]
            if not self.db.get("feeds_2026_10"):  # new default feeds reach existing installs once
                from .data.social import NEW_FEEDS_2026_10, RSS_FEEDS
                for name in NEW_FEEDS_2026_10:
                    self.social.feeds.setdefault(name, RSS_FEEDS[name])
                self.db.set("feeds_2026_10", True)
        self._load_candles()
        ab = settings["ai_budget"]
        self.budget = Budget(self.db, ab)
        self.budget.profit = lambda: max(0.0, float((self.wallet or {}).get("bot_edge") or 0.0))
        self.llm = LLM(settings.anthropic_api_key, self.budget, self.db, ab["fast_model"], ab["deep_model"])
        self.bb = Blackboard()
        self.live: FusionBroker | BitpandaBroker | None = None
        self.live_errors = 0
        self.shocks: dict[str, dict] = {}          # Fusion Scout's crash alerts
        self.trend: dict = {}                      # Trend Watch's preview of tonight's decision
        self.risk_log: deque = deque(self.db.get("risk_log", []), maxlen=40)
        self._last_said: dict[str, float] = {}
        self.started = time.time()
        self.fast = FastPot(self)                  # the fast pot: small, separate real money, every 4 hours

        h = lambda key, feed: (lambda: feed.healthy.get(key, False))  # noqa: E731
        self.sources = [
            Source(self, "src_fusion", "Bitpanda Fusion", "Your real account: balance, prices, which coins trade",
                   lambda: bool(self.wallet and not self.wallet.get("error")),
                   "Read every 30 seconds with your Fusion key (Read + Trade, never withdrawal): cash, coins, prices "
                   "and the list of coins Fusion can trade."),
            Source(self, "src_kraken", "Kraken live prices", "Live prices of the 22 research coins (free)",
                   h("crypto", self.prices),
                   "Kraken's free public API: the last price every few seconds, for the coin charts and for Trend "
                   "Watch's live preview, plus one request with every coin's 24h numbers for the Fusion Scout."),
            Source(self, "src_binance", "Daily history", "Daily candles since 2017 (Binance, Coinbase, Kraken)",
                   lambda: bool(self.__dict__.get("_daily")),
                   "Every daily candle since 2017 for the 22 research coins, downloaded once and then only the new "
                   "days. The history test, the Pattern Hunter, Trend Watch and the Daily Brain all use the same data."),
            Source(self, "src_free", "Free data", "Fear & Greed, futures funding, Wikipedia, stablecoins, hash rate",
                   lambda: bool((self.db.get("alt_status") or {}).get("days")) or settings.simulate,
                   "alternative.me, Binance futures, Wikimedia, DefiLlama and blockchain.com: free, no keys, years of "
                   "daily history each."),
            Source(self, "src_news", "News feeds", "Crypto news headlines (RSS)", h("news", self.social),
                   "The RSS feeds in Controls: CoinDesk, Cointelegraph, Decrypt, The Block, Bitcoin Magazine and more."),
        ]
        self.team = [FusionScout(self), TrendWatch(self), DataCollector(self), NewsHunter(self), PatternHunter(self),
                     Researcher(self), Guardian(self), Professor(self), DailyBrain(self), FastTrader(self),
                     RiskOfficer(self), LiveDesk(self)]
        self._by_id = {a.id: a for a in self.sources + self.team}
        # remember news across restarts so headlines aren't re-read (and re-paid for) after every restart
        self.social._seen_links = set(self.db.get("news_seen", []))
        self.bb.news_events = [e for e in self.db.get("news_events", []) if time.time() - e["ts"] < 24 * 3600]
        self._tune_for_daily_brain()
        self._retire_paper()
        self._fix_double_flows()

    def _tune_for_daily_brain(self) -> None:
        """One-time settings update for the live account (2026-10-05): the daily brain may use the whole account
        in a few big positions. Your later changes win; fresh installs skip it."""
        if self.db.get("tuned") == "2026-10-05" or self.db.get("mode") != "live":
            return
        overrides = self.db.get("controls", {})
        overrides.update({
            "live.max_invest": 2000,             # = the whole account: the brain never uses more than the account holds
            "live.max_order": 150,               # one order per coin (a third of the account is about 115 CHF)
            "live.max_spread_pct": 1.0,
            "live.use_my_coins": True,
        })
        controls.apply(self.settings.raw, overrides)
        self.db.set("controls", overrides)
        b = self.brain()
        if b.get("on"):
            b.pop("day", None)  # decide again with the new limits right after this start
            self.db.set("brain", b)
        self.db.set("tuned", "2026-10-05")
        self.db.log("Engine", "info", "Settings tuned for the daily brain: whole account, about a third per coin, "
                                      "orders up to 150 CHF. Change anything in Controls.")

    def _retire_paper(self) -> None:
        """v0.5: the pretend-money lane (minute strategies, champion contest, Optimizer) is gone. Its old records stay
        in the database untouched; nothing reads them any more."""
        if self.db.get("v05"):
            return
        self.db.set("v05", time.time())
        self.db.log("Engine", "info", "v0.5: the paper strategies, champion contest and Optimizer are switched off for "
                                      "good. Every agent now works for the real money or for the history test that "
                                      "picks its strategy.")

    def _fix_double_flows(self) -> None:
        """One-time repair (2026-10-05): a deposit entered twice by hand counted twice. Keep the first entry, undo the
        repeats, and straighten the chart of the bot's own gain around the deposit."""
        if self.db.get("flows_fixed"):
            return
        self.db.set("flows_fixed", time.time())
        flows = self.db.get("flows") or []
        keep, undo = [], []
        for f in flows:
            twin = next((k for k in keep if k["how"] == f["how"] == "entered by you" and k["amount"] == f["amount"]
                         and abs(f["ts"] - k["ts"]) < 1800), None)
            (undo if twin else keep).append(f)
        hist = self.db.get("wallet_hist", [])
        for f in undo:
            for key, field in (("hold_start", "fiat"), ("account_start", "total")):
                st = self.db.get(key)
                if st:
                    st[field] -= f["amount"]
                    self.db.set(key, st)
            for h in hist:
                if h[0] >= f["ts"] and h[2] is not None:
                    h[2] = round(h[2] + f["amount"], 2)
        for f in keep:  # before it was entered, the deposit showed up as the bot's gain: take it out of the chart
            if f["how"] != "entered by you":
                continue
            start = next((hist[k][0] for k in range(len(hist) - 1, 0, -1)
                          if hist[k][0] < f["ts"] and f["ts"] - hist[k][0] < 6 * 3600
                          and abs(hist[k][1] - hist[k - 1][1] - f["amount"]) < abs(f["amount"]) * 0.2), None)
            for h in hist:
                if start and start <= h[0] < f["ts"] and h[2] is not None:
                    h[2] = round(h[2] - f["amount"], 2)
        if undo:
            self.db.set("flows", keep)
            self.db.set("wallet_hist", hist)
            self.db.log("Live Desk", "info", f"Fixed a deposit that was entered {len(undo) + 1} times: counted once now.")

    # ------------------------------------------------------------------ state
    @property
    def mode(self) -> str:
        return self.db.get("mode", "paper")

    @property
    def kill_switch(self) -> bool:
        return self.db.get("kill_switch", False)

    def agent(self, id: str):
        return self._by_id[id]

    def _log(self, agent: str, level: str, message: str) -> None:
        if level == "warn":  # feeds can fail every poll; say it once per half hour, not every minute
            key = f"{agent}:{message[:40]}"
            if time.time() - self._last_said.get(key, 0) < 1800:
                return
            self._last_said[key] = time.time()
        self.bus.publish("log", self.db.log(agent, level, message))

    # ------------------------------------------------------------------ dashboard settings
    def controls(self) -> dict:
        return {"controls": controls.describe(self.settings.raw, self.db.get("controls", {}))}

    def set_controls(self, changes: dict) -> dict:
        overrides = self.db.get("controls", {})
        for key, value in changes.items():
            if value is None:  # reset this one to config.toml
                overrides.pop(key, None)
                section, name = key.split(".")
                self.settings.raw[section][name] = controls.get(self._base_raw, key)
            else:
                overrides[key] = controls.coerce(key, value)
        controls.apply(self.settings.raw, overrides)
        self.db.set("controls", overrides)
        if self.brain_on() and {"live.max_invest", "live.max_order"} & set(changes):
            b = self.brain()
            b.pop("day", None)  # new money limits: the brain re-decides at its next check instead of tomorrow
            self.db.set("brain", b)
        self._log("Engine", "info", "Settings changed: " + ", ".join(
            f"{controls.BY_KEY[k]['label']} = {'default' if v is None else controls.coerce(k, v)}" for k, v in changes.items()))
        return self.controls()

    def sources_info(self) -> dict:
        return {"feeds": self.social.feeds, "status": self.social.source_status, "coins": research.UNIVERSE}

    async def add_source(self, kind: str, value: str, name: str = "") -> dict:
        value = value.strip()
        if kind != "feeds":
            return {"ok": False, "error": "only news feeds can be added; the coins are the history test's 22"}
        if not value.startswith(("http://", "https://")):
            return {"ok": False, "error": "paste the full feed address, starting with https://"}
        why = await self.social.check_feed(value)
        if why:
            return {"ok": False, "error": why}
        label = name.strip() or value.split("/")[2].removeprefix("www.")
        self.social.feeds[label] = value
        self.db.set("sources", {"feeds": self.social.feeds})
        self._log("Engine", "info", f"Added the news feed {label}.")
        return {"ok": True, **self.sources_info()}

    def remove_source(self, kind: str, value: str) -> dict:
        if kind != "feeds":
            return {"ok": False, "error": "unknown list"}
        self.social.feeds.pop(value, None)
        self.db.set("sources", {"feeds": self.social.feeds})
        self._log("Engine", "info", f"Removed the news feed {value}.")
        return {"ok": True, **self.sources_info()}

    def set_agent(self, aid: str, enabled: bool | None = None, prompt: str | None = None, model: str | None = None,
                  reset_prompt: bool = False, run_now: bool = False) -> dict:
        a = self._by_id.get(aid)
        if not a:
            raise KeyError(aid)
        if enabled is not None:
            if not a.can_disable:
                raise ValueError(f"{a.name} is essential and can't be switched off")
            off = set(self.db.get("agents_off", []))
            off.discard(aid) if enabled else off.add(aid)
            self.db.set("agents_off", sorted(off))
            self._log("Engine", "info", f"{a.name} switched {'on' if enabled else 'off'}.")
        if a.uses_ai:
            if reset_prompt:
                self.db.execute("DELETE FROM kv WHERE key=?", (f"prompt:{aid}",))
            elif prompt is not None:
                if not prompt.strip():
                    raise ValueError("the prompt can't be empty")
                self.db.set(f"prompt:{aid}", prompt.strip()[:6000])
                self._log("Engine", "info", f"{a.name}'s instructions were edited.")
            if model is not None:
                from .llm import PRICES
                if model not in PRICES:
                    raise ValueError("unknown model")
                self.db.set(f"model:{aid}", model)
        if run_now and hasattr(a, "run_now"):
            a.run_now()
        return a.node()

    # ------------------------------------------------------------------ prices for the charts
    def _load_candles(self) -> None:
        since = time.time() - 86400
        for q in self.prices.quotes.values():
            q.merge(self._history(q.symbol, 1440, since))

    def _history(self, symbol: str, limit: int, since: float = 0) -> list[tuple[float, float]]:
        rows = self.db.query("SELECT ts, close FROM candles WHERE symbol=? AND ts>=? ORDER BY ts DESC LIMIT ?",
                             (symbol, since, limit))
        return [(r["ts"], r["close"]) for r in reversed(rows)]

    def _save_candles(self) -> None:
        rows = [(q.symbol, int(ts), c) for q in self.prices.quotes.values() for ts, c in list(q.candles)[-5:]]
        self.db.executemany("INSERT OR REPLACE INTO candles(symbol, ts, close) VALUES(?,?,?)", rows)
        if int(time.time()) % 3600 < 60:
            self.db.execute("DELETE FROM candles WHERE ts<?", (time.time() - HISTORY_DAYS * 86400,))

    def candles(self, symbol: str, minutes: int = 240) -> dict:
        q = self.prices.quotes.get(symbol)
        if not q:
            raise KeyError(symbol)
        since = time.time() - minutes * 60
        rows = self._history(symbol, minutes + 5, since) if minutes > 1440 else [c for c in q.candles if c[0] >= since]
        trades = self.db.query("SELECT ts,side,price,notional,reason FROM trades WHERE symbol=? AND ts>=? AND mode='live' "
                               "ORDER BY ts", (symbol, since))
        return {"symbol": symbol, "candles": rows, "trades": trades}

    async def daily(self, symbol: str, days: int = 120) -> dict:
        """Daily candles with the brain's buy and sell levels, and the bot's real trades on that coin."""
        cd = await self._daily_candles()
        if symbol not in cd.c:
            raise KeyError(symbol)
        n = len(cd.days)
        a = max(0, n - days)
        t = next((r for r in (self.trend or {}).get("rows", []) if r["symbol"] == symbol), None)
        trades = self.db.query("SELECT ts,side,notional,price,reason FROM trades WHERE symbol=? AND mode='live' AND ts>=? "
                               "ORDER BY ts", (symbol, cd.days[a]))
        q = self.prices.quotes.get(symbol)
        return {"symbol": symbol, "days": cd.days[a:], "o": cd.o[symbol][a:], "h": cd.h[symbol][a:],
                "l": cd.l[symbol][a:], "c": cd.c[symbol][a:], "levels": t, "trades": trades,
                "live": q.price if q else None}

    # ------------------------------------------------------------------ research: history test and patterns
    async def run_research(self) -> dict:
        """The history test: all daily candles since 2017, every strategy vs holding Bitcoin, then the Pattern Hunter."""
        if self.__dict__.get("_research_busy"):
            return {"busy": True}
        self._research_busy = True
        try:
            cd = await self._daily_candles(force=True)
            if not self.settings.simulate:
                await altdata.update(self.prices.client, self.db, cd.coins, self._log)
                cd.attach(altdata.load(self.db, cd.coins))
            res = await asyncio.to_thread(research.run_all, cd, None, self.brain().get("strategy"))
            res["simulated"] = bool(self.settings.simulate)
            self.db.set("research", res)
            robust = [r for r in res["rows"] if r["robust"]]
            btc = next(r for r in res["rows"] if r["name"] == research.HoldBTC.name)
            self._log("Researcher", "info", f"History test {res['from']} to {res['to']} ({len(res['years'])} years, "
                      f"{res['strategies_tested']} strategies): {len(robust)} pass every robustness check"
                      + (f", best {robust[0]['name']} ({robust[0]['full'].get('cagr_pct')}%/yr, worst drop "
                         f"{robust[0]['full'].get('max_dd_pct')}%)" if robust else "")
                      + f". Holding Bitcoin: {btc['full'].get('cagr_pct')}%/yr, worst drop {btc['full'].get('max_dd_pct')}%.")
            await self._hunt_patterns(cd)
            self._heal_brain(res)
            return res
        finally:
            self._research_busy = False

    async def _hunt_patterns(self, cd: "research.Candles") -> None:
        """The Pattern Hunter: which signals really said something about the next week, on all history."""
        try:
            pat = await asyncio.to_thread(patterns.analyse, cd)
        except Exception as ex:
            self._log("Pattern Hunter", "warn", f"Pattern test failed: {str(ex)[:100]}")
            return
        pat["simulated"] = bool(self.settings.simulate)
        pat["sources"] = (self.db.get("alt_status") or {}).get("days", {})
        self.db.set("patterns", pat)
        found = [r for r in pat["rows"] if r.get("verdict") == "pattern"]
        hints = [r for r in pat["rows"] if r.get("verdict") == "hint"]
        self._log("Pattern Hunter", "info", f"Tested {pat['tested']} signals against the next week's price: "
                  + (f"{len(found)} real pattern(s): " + "; ".join(f"{r['name']} ({r['direction']})" for r in found[:3])
                     if found else "no signal passes every check")
                  + (f". {len(hints)} weaker hint(s)." if hints else "."))

    def _heal_brain(self, res: dict) -> None:
        """If the brain's strategy fails the robustness checks three daily runs in a row, hand the money to the best
        strategy that passes. One bad day of data can't flip it back and forth."""
        b = self.brain()
        mine = next((r for r in res["rows"] if r["name"] == b.get("strategy")), None)
        if not b.get("on") or not mine:
            return
        if mine["robust"]:
            if b.get("weak_days"):
                b["weak_days"] = 0
                self.db.set("brain", b)
            return
        b["weak_days"] = b.get("weak_days", 0) + 1
        best = next((r for r in res["rows"] if r["robust"]), None)
        if b["weak_days"] >= 3 and best:
            old = b["strategy"]
            b.update({"strategy": best["name"], "weak_days": 0, "switched": {"from": old, "ts": time.time()}})
            b.pop("day", None)  # the new strategy decides right away
            self.db.set("brain", b)
            msg = (f"The daily brain switched from {old} to {best['name']}: the old one failed the robustness checks "
                   f"three days in a row, the new one passes them ({best['full'].get('cagr_pct')}%/yr in the test).")
            self._log("Daily Brain", "live", msg)
            self._notify_later(msg, title="🧠 Strategy switched", tags=["arrows_counterclockwise"], priority=4)
            return
        self.db.set("brain", b)
        self._log("Researcher", "warn", f"The daily brain's strategy {mine['name']} fails a robustness check on the "
                  f"newest data ({b['weak_days']} of 3 days). After 3 days in a row the brain switches to "
                  + (best["name"] if best else "the best strategy that passes") + ".")

    # ------------------------------------------------------------------ guardian
    GUARD_EVENTS = ("hack", "delisting")
    GUARD_HOURS = 72

    def news_symbols(self) -> list[str]:
        """Coins the News Hunter tags: every coin the daily brain may trade, plus any the bot holds."""
        return list(dict.fromkeys([*research.UNIVERSE, *self.db.get("live_qty", {})]))

    def guard(self) -> dict:
        return {s: g for s, g in (self.db.get("guard") or {}).items() if g.get("until", 0) > time.time()}

    def professor_block(self, sym: str, reason: str) -> None:
        """The Professor's veto: no buys of this coin for 24 hours (it never sells anything)."""
        g = self.db.get("guard") or {}
        cur = g.get(sym) if (g.get(sym) or {}).get("until", 0) > time.time() else None
        cur = cur or {"since": time.time(), "titles": [], "sources": [], "reason": "professor"}
        cur["titles"] = (cur["titles"] + [reason[:160]])[-4:]
        cur["sources"] = sorted(set(cur["sources"]) | {"The Professor"})
        cur["until"] = max(cur.get("until", 0), time.time() + 24 * 3600)
        g[sym] = cur
        self.db.set("guard", g)
        self._log("Guardian", "warn", f"The Professor blocks buying {sym} for 24 hours: {reason[:140]}")

    async def guard_tick(self) -> None:
        """The Guardian: hack or delisting news about a coin blocks buying it for 3 days. If the bot holds it and
        two independent witnesses agree (two news sources, or news plus the Fusion Scout's crash alert), the bot
        sells it right away instead of waiting for the next day. Bitcoin and Ethereum are never sold this way:
        exchange hacks mention them all the time."""
        now = time.time()
        old = self.guard()
        g = dict(old)
        for ev in self.bb.news_events:
            syms = ev.get("symbols") or []
            if (ev.get("event") in self.GUARD_EVENTS and ev.get("sentiment", 0) <= -0.5 and ev.get("impact", 0) >= 0.6
                    and now - ev["ts"] < 48 * 3600 and 1 <= len(syms) <= 2):
                for s in syms:
                    cur = g.get(s) or {"since": now, "titles": [], "sources": []}
                    if ev["title"] not in cur["titles"]:
                        cur["titles"] = (cur["titles"] + [ev["title"]])[-4:]
                    cur["sources"] = sorted(set(cur["sources"]) | {ev.get("source", "?")})
                    cur.update({"until": max(cur.get("until", 0), ev["ts"] + self.GUARD_HOURS * 3600),
                                "reason": ev["event"]})
                    g[s] = cur
        for s, shock in self.shocks.items():  # a crash is a second witness, never a reason on its own
            if s in g and g[s].get("reason") in self.GUARD_EVENTS:
                g[s]["sources"] = sorted(set(g[s]["sources"]) | {f"crash {shock['change']:+.0f}%"})
        for s in set(g) - set(old):
            self._log("Guardian", "warn", f"{g[s]['reason'].upper()} news about {s}: no buys of {s} for 3 days. "
                      f"\"{g[s]['titles'][0][:90]}\"")
        if self.mode == "live" and self.live:
            books = [b for b, on in (("brain", self.brain_on()), ("fast", self.fast.on())) if on]
            for s, entry in g.items():
                news = [x for x in entry["sources"] if x != "The Professor"]
                held = [b for b in books if s in self.db.get(self._book_keys(b)[0], {})]
                if (held and s not in ("BTC", "ETH") and entry.get("reason") in self.GUARD_EVENTS
                        and len(news) >= 2 and not entry.get("sold")):
                    for b in held:
                        ex = await self._live_sell(s, reason=f"guardian: {entry['reason']} news", book=b)
                        if b == "fast" and ex:
                            self.fast.booked_sell(s, ex)
                    entry["sold"] = not any(s in self.db.get(self._book_keys(b)[0], {}) for b in held)
                    if entry["sold"]:
                        msg = (f"Sold {s} right away: {', '.join(news)} report {entry['reason']} "
                               f"(\"{entry['titles'][0][:80]}\"). No buys of {s} for 3 days.")
                        self._log("Guardian", "live", msg)
                        self._notify_later(msg, title=f"🛡 Guardian sold {s}", tags=["rotating_light"], priority=5)
        self.db.set("guard", g)

    # ------------------------------------------------------------------ phone report
    def _notify_later(self, text: str, **kw) -> None:
        try:
            asyncio.get_running_loop().create_task(self.notify(text, **kw))
        except RuntimeError:
            pass  # no event loop (tests)

    def phone_channels(self) -> list[str]:
        st = self.settings
        return (["ntfy"] if st.ntfy_topic else []) + (["WhatsApp"] if st.whatsapp_phone and st.whatsapp_key else []) + (["Telegram"] if st.telegram_token and st.telegram_chat else [])

    async def notify(self, text: str, title: str = "TradingBotty", tags: list[str] | None = None, priority: int = 3) -> bool:
        """A message to your phone: WhatsApp through CallMeBot (WHATSAPP_PHONE and WHATSAPP_APIKEY in .env)
        and/or your own Telegram bot (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID). True if at least one arrived."""
        st = self.settings
        if st.simulate or not self.phone_channels():
            return False
        ok = False
        if st.ntfy_topic:
            try:
                r = await self.prices.client.post("https://ntfy.sh/", json={
                    "topic": st.ntfy_topic.strip(), "title": title, "message": text[:3900], "tags": tags or [],
                    "priority": priority})
                if r.status_code != 200:
                    self._log("Engine", "warn", f"ntfy refused the message ({r.status_code}): check NTFY_TOPIC in .env")
                ok = ok or r.status_code == 200
            except Exception as ex:
                self._log("Engine", "warn", f"ntfy message failed: {str(ex)[:80]}")
        text = f"{title}\n\n{text}" if title else text  # the other apps have no title line
        if st.whatsapp_phone and st.whatsapp_key:
            try:
                phone = st.whatsapp_phone.replace(" ", "")
                r = await self.prices.client.get("https://api.callmebot.com/whatsapp.php",
                                                 params={"phone": phone, "text": text[:1500], "apikey": st.whatsapp_key})
                good = r.status_code == 200 and "error" not in r.text[:400].lower()
                if not good:
                    self._log("Engine", "warn", f"WhatsApp (CallMeBot) refused the message ({r.status_code}): "
                                                "check WHATSAPP_PHONE and WHATSAPP_APIKEY in .env")
                ok = ok or good
            except Exception as ex:
                self._log("Engine", "warn", f"WhatsApp message failed: {str(ex)[:80]}")
        if st.telegram_token and st.telegram_chat:
            try:
                r = await self.prices.client.post(f"https://api.telegram.org/bot{st.telegram_token}/sendMessage",
                                                  json={"chat_id": st.telegram_chat, "text": text[:3900]})
                if r.status_code != 200:
                    self._log("Engine", "warn", f"Telegram refused the message ({r.status_code}): check the token and chat id")
                ok = ok or r.status_code == 200
            except Exception as ex:
                self._log("Engine", "warn", f"Telegram message failed: {str(ex)[:80]}")
        return ok

    @staticmethod
    def _local_now() -> time.struct_time:
        """Swiss time for the morning briefing (falls back to the computer's own clock)."""
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo("Europe/Zurich")).timetuple()
        except Exception:
            return time.localtime()

    async def morning_tick(self) -> None:
        """Once a day at the chosen hour (Swiss time): the briefing to your phone."""
        if not self.phone_channels():
            return
        now = self._local_now()
        day = time.strftime("%Y-%m-%d", now)
        if now.tm_hour < int(self.settings["phone"]["morning_hour"]) or self.db.get("briefing_day") == day:
            return
        self.db.set("briefing_day", day)
        if await self.notify(self.daily_report(), title=self.report_title(), tags=["sunny"]):
            self._log("Engine", "info", f"Morning briefing sent by {' and '.join(self.phone_channels())}.")

    def report_title(self) -> str:
        w = self.wallet or {}
        cur = w.get("currency", "")
        if w.get("total") is None:
            return "☀️ TradingBotty"
        edge = f" ({w['bot_edge']:+.2f})" if w.get("bot_edge") is not None else ""
        return f"☀️ {w['total']:.2f} {cur}{edge} · TradingBotty"

    def daily_report(self) -> str:
        """The morning briefing: short blocks with one emoji each, easy to read on a phone."""
        b, w = self.brain(), self.wallet or {}
        cur = w.get("currency", "")
        out = []
        if w.get("total") is not None:
            out.append(f"💰 Account {w['total']:.2f} {cur}\n    cash {w['fiat']:.2f} · coins {w['total'] - w['fiat']:.2f}")
        else:
            out.append("💰 Account: not read yet")
        if w.get("bot_edge") is not None:
            out.append(f"{'📈' if w['bot_edge'] >= 0 else '📉'} Bot's own result {w['bot_edge']:+.2f} {cur}\n    without your own coins' price swings")
        held = sorted((self.db.get("live_qty") or {}).keys())
        brain = [f"🧠 Daily Brain · {b.get('strategy') or 'no strategy'}",
                 f"    holds {', '.join(held) if held else 'nothing (cash)'}"]
        if b.get("btc_ok") is not None:
            brain.append("    Bitcoin filter: " + ("✅ buys allowed" if b["btc_ok"] else "⛔ waiting in cash on purpose"))
        if b.get("note"):
            brain.append(f"    {b['note'][:220]}")
        out.append("\n".join(brain))
        f = self.fast.status()
        if f.get("on"):
            out.append(f"⚡ Fast pot {f.get('pot', 0):.2f} {cur}\n    {f.get('trades', 0)} trades · {f.get('realized', 0):+.2f} {cur} so far · "
                       + (f"holds {', '.join(f['pos'])}" if f.get("pos") else "waiting for a pump"))
        day = self.db.query("SELECT symbol,side,notional,variant_id FROM trades WHERE mode='live' AND ts>? ORDER BY ts",
                            (time.time() - 86400,))
        if day:
            out.append("🔁 Real trades, last 24h\n" + "\n".join(
                f"    {'🟢' if t['side'] == 'BUY' else '🔴'} {t['side'].lower()} {t['symbol']} {t['notional']:.2f}"
                f"{' ⚡' if t['variant_id'] == 'fast' else ''}" for t in day))
        else:
            last = self.db.query("SELECT MAX(ts) t FROM trades WHERE mode='live'")[0]["t"]
            out.append("🔁 No real trade in 24h" + (f" (last {round((time.time() - last) / 3600)} h ago)" if last else ""))
        near = [r["symbol"] for r in (self.trend or {}).get("rows", []) if r["state"] == "near breakout"]
        if near:
            out.append("👀 Close to a breakout: " + ", ".join(near))
        guard = self.guard()
        out.append("🛡 Guardian: " + (", ".join(f"{s} blocked ({g['reason']})" for s, g in guard.items()) if guard else "all clear"))
        prof = self.db.get("professor_last") or {}
        if prof.get("assessment") and time.time() - (prof.get("ts") or 0) < 36 * 3600:
            out.append("🎓 Professor\n    " + prof["assessment"][:400])
        found = [r["name"] for r in (self.db.get("patterns") or {}).get("rows", []) if r.get("verdict") == "pattern"]
        if found:
            out.append("🔬 Patterns: " + "; ".join(found[:3]))
        return "\n\n".join(out)

    # ------------------------------------------------------------------ live trading
    async def set_mode(self, mode: str) -> dict:
        if mode == "paper":
            self.db.set("mode", "paper")
            self._log("Risk Officer", "info", "Live trading OFF: standby, nothing trades the real money.")
            return {"ok": True, "mode": "paper"}
        if mode != "live":
            return {"ok": False, "error": "unknown mode"}
        moved = self.db.get("moved")
        if moved:
            return {"ok": False, "error": f"This bot was moved to {moved.get('to', 'the server')} on "
                    f"{time.strftime('%d.%m. %H:%M', time.localtime(moved['ts']))}. Two copies would trade the same money "
                    "twice. If the other copy is on STANDBY or off, press 'Trade on this computer again' in Controls first."}
        try:
            self.live = make_live_broker(self.settings)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        try:
            info = await self.live.connect()
            bal = await self.live.balances()
        except Exception as e:
            self.live = None
            return {"ok": False, "error": f"Bitpanda connection failed: {e}"}
        self.db.set("mode", "live")
        self.live_errors = 0
        self._log("Risk Officer", "live", f"LIVE trading ON via {self.live.name}. {bal.get('FIAT', 0):.2f} {self.live.currency} "
                                          f"cash, {info['assets']} assets. The Daily Brain trades the real money.")
        return {"ok": True, "mode": "live", "fiat": bal.get("FIAT", 0)}

    def _live_held(self, bal: dict, symbol: str) -> float:
        ids = getattr(self.live, "asset_ids", {})  # the app broker keys balances by asset id
        return float(bal.get(symbol.upper(), bal.get(ids.get(symbol.upper(), "?"), 0.0)) or 0.0)

    def _record(self, sym: str, side: str, ex: dict, notional: float, reason: str, book: str = "brain") -> None:
        self.db.execute(
            "INSERT INTO trades(ts,variant_id,mode,symbol,side,qty,price,notional,fee,pnl,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), BRAIN_ID if book == "brain" else "fast", "live", sym, side, float(ex.get("quantity", 0) or 0), float(ex.get("price", 0) or 0),
             notional, float(ex.get("fee", 0) or 0), None, reason))
        self.bus.publish("trade", {"mode": "live", "symbol": sym, "side": side, "notional": round(notional, 2),
                                   "reason": reason, "ts": time.time(), "book": book})
        if self.settings["phone"].get("trades"):
            who = "⚡ Fast pot" if book == "fast" else "🧠 Daily Brain"
            cur = self.live.currency if self.live else ""
            self._notify_later(f"{who}\n{reason}\n\nprice {float(ex.get('price', 0) or 0):g} · fee {float(ex.get('fee', 0) or 0):.2f} {cur}",
                               title=f"{'🟢 BUY' if side == 'BUY' else '🔴 SELL'} {sym} · {notional:.2f} {cur}",
                               tags=["chart_with_upwards_trend" if side == "BUY" else "chart_with_downwards_trend"])

    @staticmethod
    def _book_keys(book: str) -> tuple[str, str]:
        """Where each trader keeps its own coins: the daily brain in live_qty/live_cost, the fast pot in fast_qty/fast_cost.
        Each only ever sells what it bought itself; your own coins are in neither."""
        return ("live_qty", "live_cost") if book == "brain" else ("fast_qty", "fast_cost")

    async def _live_sell(self, symbol: str, reason: str, book: str = "brain") -> dict | None:
        """Sell all of a coin the bot bought itself. Coins you owned before stay untouched. Returns the fill."""
        kq, kc = self._book_keys(book)
        try:
            owned = self.db.get(kq, {})
            mine = owned.get(symbol, 0.0)
            if mine <= 0:
                return None
            res = await self.live.sell_fraction(symbol, 1.0, owned=mine)
            cost = self.db.get(kc, {})
            owned.pop(symbol, None)
            cost.pop(symbol, None)
            self.db.set(kq, owned)
            self.db.set(kc, cost)
            ex = None
            if res:
                ex = res.get("execution", {}) or {}
                if not float(ex.get("quantity", 0) or 0):
                    ex = {**ex, "quantity": mine}
                self._record(symbol, "SELL", ex, float(ex.get("notional", 0) or 0), reason, book)
                self._log("Live Desk", "live", f"LIVE SELL {symbol}: {ex.get('notional', '?')} {self.live.currency} "
                                               f"filled ({reason}).")
            self.live_errors = 0
            return ex
        except Exception as e:
            self.live_errors += 1
            self._log("Live Desk", "error", f"LIVE SELL {symbol} failed: {e}")
            if self.live_errors >= 3:
                self.db.set("mode", "paper")
                self._log("Risk Officer", "error", "Three live errors in a row: live trading stopped for safety.")
            return None

    def _min_order(self, sym: str, pairs: dict | None = None) -> float:
        """Fusion's smallest order for a coin: its listed minimum, or more if Fusion told us so in a rejection."""
        pairs = pairs if pairs is not None else (getattr(self.live or self._viewer, "pairs", None) or {})
        return max(float((pairs.get(sym) or {}).get("minOrderAmount") or 0), float(self.db.get("fusion_min", {}).get(sym, 0)))

    def _learn_min(self, sym: str, ex: Exception) -> bool:
        """Fusion says "Enter a higher amount than 30 CHF" when its list shows a lower minimum: remember the real one."""
        m = re.search(r"higher amount than ([\d.]+)", str(ex))
        if not m:
            return False
        mins = self.db.get("fusion_min", {})
        mins[sym] = round(float(m.group(1)) * 1.02, 2)
        self.db.set("fusion_min", mins)
        return True

    def _risk_note(self, order: str, checks: list, result: str) -> None:
        self.risk_log.append({"ts": time.time(), "time": time.strftime("%d.%m %H:%M"), "order": order,
                              "checks": checks, "result": result})
        self.db.set("risk_log", list(self.risk_log))

    async def _live_buy(self, sym: str, want: float, reason: str, book: str = "brain") -> tuple[float, float]:
        """One real buy of `want` in account currency, through the Risk Officer's checks, raising cash from your coins
        if allowed (daily brain only). Records it as that trader's coin. Returns (amount spent, quantity received)."""
        cfg = self.settings["live"]
        cur = self.live.currency
        checks: list[list[str]] = []
        order = f"BUY {sym} {want:.2f} {cur}"

        def stop(msg: str) -> ValueError:
            checks.append(["result", "stop", msg])
            self._risk_note(order, checks, f"not sent: {msg}")
            return ValueError(msg)

        if self.kill_switch:
            raise stop("kill switch is on")
        checks.append(["kill switch", "ok", "off"])
        if book == "fast":
            amount = want
            checks.append(["fast pot", "ok", f"{want:.2f} {cur} from the fast pot (its own limit, set in the Fast Trader Lab)"])
        else:
            invested = sum(self.db.get("live_cost", {}).values())
            amount = min(want, cfg["max_invest"] - invested)
            checks.append(["cap on money in coins", "ok" if amount >= want - 0.01 else "cut",
                           f"{invested:.2f} of {cfg['max_invest']:.0f} {cur} used, {max(0.0, cfg['max_invest'] - invested):.2f} free"])
        if book == "brain" and amount < want - 0.01:
            self.agent("livedesk").say(f"{reason}: lowered {want:.2f} to {amount:.2f} {cur} for {sym} to stay inside your "
                                       f"cap of {cfg['max_invest']:.0f} {cur} in bot trades (raise it in Controls).", "warn")
        pairs = getattr(self.live, "pairs", None) or {}
        min_amt = self._min_order(sym, pairs)
        if amount < max(min_amt, 1):
            raise stop(f"{amount:.2f} {cur} is below the minimum order ({max(min_amt, 1):g} {cur}) or your cap is full")
        checks.append(["Fusion minimum", "ok", f"{amount:.2f} ≥ {max(min_amt, 1):g} {cur}"])
        if hasattr(self.live, "spread_pct"):
            try:
                spread = await self.live.spread_pct(sym)
            except Exception as ex:  # can't read the book: liquid coins only, so the order goes ahead
                checks.append(["spread", "unknown", f"order book unreadable ({str(ex)[:60]})"])
            else:
                if spread > cfg["max_spread_pct"]:
                    raise stop(f"spread {spread:.2f}% is above your {cfg['max_spread_pct']}% limit")
                checks.append(["spread", "ok", f"{spread:.2f}% ≤ {cfg['max_spread_pct']}%"])
        bal = await self.live.balances()
        reserve = self.fast.cash_reserve() if book == "brain" else 0.0  # the fast pot's cash stays for the fast pot
        room = max(0.0, bal.get("FIAT", 0.0) - reserve) * 0.995  # Fusion adds its fee on top: all your cash is "too big"
        if reserve:
            checks.append(["fast pot cash", "kept", f"{reserve:.2f} {cur} stays free for the fast pot"])
        if amount > room:
            if cfg.get("use_my_coins") and book == "brain":
                checks.append(["cash", "short", f"{room:.2f} {cur} usable: selling some of your coins first"])
                room = max(0.0, await self._raise_cash(amount - room, await self._spare_coins(bal), pairs) - reserve) * 0.995
                bal = await self.live.balances()
            amount = min(amount, room)
            if amount < max(min_amt, 1):  # not enough cash for Fusion's minimum: don't send a doomed order
                raise stop(f"only {room:.2f} {cur} cash usable, below the {max(min_amt, 1):g} {cur} minimum"
                           + (" (the fast pot never sells your coins)" if book == "fast"
                              else "" if cfg.get("use_my_coins") else " (the bot may not sell your coins)"))
        checks.append(["cash incl. fee room", "ok", f"{amount:.2f} ≤ 99.5% of {bal.get('FIAT', 0.0):.2f} {cur}"])
        before = self._live_held(bal, sym)
        try:
            res = await self.live.buy(sym, amount)
        except Exception as e:
            if self._learn_min(sym, e):
                raise stop(f"Fusion wants more than {amount:.2f} {cur} for {sym}; noted for next time") from e
            self._risk_note(order, checks + [["Fusion", "refused", str(e)[:120]]], "refused by Fusion")
            raise
        ex = (res or {}).get("execution", {}) or {}
        got = float(ex.get("quantity", 0) or 0) or max(0.0, self._live_held(await self.live.balances(), sym) - before)
        ex = {**ex, "quantity": got}  # the booked quantity is what really arrived
        kq, kc = self._book_keys(book)
        cost = self.db.get(kc, {})
        cost[sym] = cost.get(sym, 0.0) + amount
        self.db.set(kc, cost)
        owned = self.db.get(kq, {})
        owned[sym] = owned.get(sym, 0.0) + got
        self.db.set(kq, owned)
        self._record(sym, "BUY", ex, amount, reason, book)
        self._risk_note(f"BUY {sym} {amount:.2f} {cur}", checks, "sent")
        return amount, got

    async def _spare_coins(self, bal: dict) -> dict[str, tuple[float, float]]:
        """Your coins the bot may sell for cash: tradable, not bought by the bot, not about to be bought by the
        brain. symbol -> (quantity, value in account currency)."""
        if not hasattr(self.live, "prices"):
            return {}
        prices = await self.live.prices()
        mine = {**self.db.get("live_qty", {}), **self.db.get("fast_qty", {})}
        pairs = getattr(self.live, "pairs", {}) or {}
        out = {}
        for sym, qty in bal.items():
            if sym in ("FIAT", self.live.currency) or sym in mine or sym not in pairs or not qty:
                continue
            if sym in self.__dict__.get("_brain_target", {}):
                continue  # the brain is about to buy this coin: selling yours of it to pay would only cost fees
            value = qty * prices.get(sym, 0.0)
            if value >= 1:
                out[sym] = (qty, value)
        return out

    async def _raise_cash(self, need: float, spare: dict, pairs: dict) -> float:
        """Sell spare coins, biggest first, until `need` more cash is there. Returns the new cash balance."""
        for sym, (qty, value) in sorted(spare.items(), key=lambda kv: -kv[1][1]):
            if need <= 0:
                break
            low = max(self._min_order(sym, pairs), 1)
            if value < low * 1.02:
                continue  # worth less than Fusion's minimum order: it can't be sold, try the next coin
            frac = min(1.0, need * 1.01 / value)
            if value * frac < low:
                frac = min(1.0, low * 1.05 / value)
            try:
                res = await self.live.sell_fraction(sym, frac)
            except Exception as e:
                self._learn_min(sym, e)
                self.agent("livedesk").say(f"Couldn't sell your {sym} to fund a buy: {str(e)[:120]}", "warn")
                continue
            ex = (res or {}).get("execution") or {}
            if not float(ex.get("quantity", 0) or 0):
                ex = {**ex, "quantity": qty * frac}
            got = float(ex.get("notional", 0) or 0) - float(ex.get("fee", 0) or 0)
            self._record(sym, "SELL", ex, float(ex.get("notional", 0) or 0), "your coin, sold for cash (you allowed it)")
            self.agent("livedesk").say(f"Sold {frac * 100:.0f}% of your {sym} for {got:.2f} {self.live.currency} "
                                       f"to fund a buy (you allowed the bot to use your coins).", "live")
            need -= got
        return (await self.live.balances()).get("FIAT", 0.0)

    # ------------------------------------------------------------------ the daily brain
    def brain(self) -> dict:
        return self.db.get("brain", None) or {"on": False, "strategy": None}

    def brain_on(self) -> bool:
        return bool(self.brain().get("on"))

    async def set_brain(self, on: bool, strategy: str | None = None) -> dict:
        """Hand the real money to one strategy from the history test, decided once a day on daily candles."""
        b = self.brain()
        if strategy:
            if not research.by_name(strategy):
                raise ValueError(f"unknown strategy: {strategy}")
            b["strategy"] = strategy
        if on and not b.get("strategy"):
            raise ValueError("pick a strategy from the history test first")
        b["on"] = bool(on)
        b.pop("day", None)  # decide again right away
        self.db.set("brain", b)
        if on:
            self._log("Daily Brain", "live", f"Daily brain ON: {b['strategy']} manages the bot's real coins. It decides "
                                             f"once a day on daily candles, exactly like in the history test.")
            asyncio.create_task(self.brain_tick())  # first decision now, in the background (history may take a minute)
        else:
            self._log("Daily Brain", "info", "Daily brain OFF: nothing trades the real money; the bot's coins stay as "
                                             "they are until you switch it on again.")
        return self.brain()

    async def _daily_candles(self, force: bool = False) -> "research.Candles":
        """All daily history for the test universe (cached in the database), updated once a new day has closed."""
        async with self.__dict__.setdefault("_hist_lock", asyncio.Lock()):  # brain, Researcher and Trend Watch share it
            return await self._daily_candles_locked(force)

    async def _daily_candles_locked(self, force: bool) -> "research.Candles":
        cd = self.__dict__.get("_daily")
        if cd and not force and cd.days[-1] >= time.time() // 86400 * 86400 - 86400:
            return cd
        coins = [c for c in research.UNIVERSE if not self.fusion_coins or c in self.fusion_coins or c == "BTC"]
        if not cd:
            self._log("Researcher", "info", f"Loading all daily history since 2017 for {len(coins)} coins "
                                            f"(the first time takes a minute or two, then only new days).")
        rows = (research.synthetic_rows(coins, days=2500) if self.settings.simulate
                else await research.update_history(self.prices.client, self.db, coins, self._log))
        if "BTC" not in rows:
            raise ValueError("no Bitcoin history right now (Binance, Coinbase and Kraken all failed)")
        cd = research.Candles.from_rows(rows)
        cd.attach(altdata.synthetic(cd.days, cd.coins) if self.settings.simulate else altdata.load(self.db, cd.coins))
        self._daily = cd
        return self._daily

    async def research_tick(self) -> None:
        """The Researcher: once a day, after the daily candle closes, re-runs every strategy on all history, then
        the fast lab."""
        now = time.time()
        tried = self.__dict__.setdefault("_lab_tried", {})
        last = (self.db.get("research") or {}).get("ts", 0)
        if now // 86400 > last // 86400 and now % 86400 > 900 and now - tried.get("research", 0) > 3600:
            tried["research"] = now  # a failed run (no data reachable) is tried again in an hour, not every tick
            await self.run_research()
        last = (self.db.get("fastlab") or {}).get("ts", 0)
        if now // 86400 > last // 86400 and now % 86400 > 1800 and now - tried.get("fast", 0) > 3600:
            tried["fast"] = now
            try:
                await self.run_fastlab()
            except Exception:
                pass  # already in the feed as "Fast lab failed"

    # ------------------------------------------------------------------ fast lab: 4-hour candles, speculative rules
    async def run_fastlab(self) -> dict:
        """Can a fast, speculative trader beat Fusion's fees? 4-hour candles of the most traded coins Fusion lists,
        the same robustness checks as the history test, a signal test on the next 24 hours and Bitcoin links."""
        if self.__dict__.get("_fast_busy"):
            return {"busy": True}
        self._fast_busy = True
        st = self.fast_status = {"running": True, "started": time.time(), "step": "picking the most traded coins",
                                 "done": 0, "total": 0, "error": None}
        try:
            if self.settings.simulate:
                rows = fastlab.synthetic_4h([*research.UNIVERSE[:16]])
            else:
                coins = await fastlab.pick_coins(self.prices.client, self.fusion_coins)
                self._log("Researcher", "info", f"Fast lab: loading 4-hour candles for {len(coins)} coins "
                          "(the first time takes a few minutes).")

                def progress(k, n, sym):
                    st.update(step=f"loading 4-hour candles: {sym}", done=k, total=n)
                rows = await fastlab.update_4h(self.prices.client, self.db, coins, self._log, progress)
            if "BTC" not in rows or len(rows) < 8:
                raise ValueError("Not enough 4-hour history yet (Bitcoin plus at least 7 coins).")
            cd = research.Candles.from_rows(rows)
            st.update(step=f"testing {len(fastlab.fast_strategies())} rules and {len(fastlab.fast_candidates())} signals "
                      f"on {len(cd.coins)} coins", done=st["total"])
            res = await asyncio.to_thread(fastlab.run, cd)
            res["simulated"] = bool(self.settings.simulate)
            self.db.set("fastlab", res)
            robust = [r for r in res["rows"] if r["robust"] and r["group"].startswith("Fast")]
            found = [r for r in res["patterns"]["rows"] if r.get("verdict") == "pattern"]
            self._log("Researcher", "info", f"Fast lab {res['from']} to {res['to']}, {len(cd.coins)} coins, "
                      f"{res['cost_per_side_pct']}% cost per side: "
                      + (f"{len(robust)} fast strategies pass every check, best {robust[0]['name']} "
                         f"({robust[0]['full'].get('cagr_pct')}%/yr)" if robust else "no fast strategy passes every check")
                      + f"; {len(found)} real 24-hour signal(s)"
                      + (": " + "; ".join(f"{r['name']} ({r['direction']})" for r in found[:3]) if found else "") + ".")
            return res
        except Exception as ex:
            st["error"] = str(ex)[:200]
            self._log("Researcher", "warn", f"Fast lab failed: {st['error']}")
            raise
        finally:
            st.update(running=False, finished=time.time())
            self._fast_busy = False

    async def brain_tick(self) -> None:
        """Every few minutes: once a new daily candle has closed, decide what to hold and trade the difference."""
        b = self.brain()
        if not b.get("on") or self.mode != "live" or not self.live or self.kill_switch or self.__dict__.get("_brain_busy"):
            return
        if not (self.wallet or {}).get("total"):
            return  # right after a start: wait until the account balance has been read
        self._brain_busy = True
        try:
            cd = await self._daily_candles()
            if b.get("day") == cd.days[-1]:
                return
            strat = research.by_name(b["strategy"])
            if not strat:
                raise ValueError(f"strategy {b['strategy']} no longer exists")
            target = research.current_target(cd, strat)
            regime = getattr(strat, "regime", None)
            note, steps = await self._brain_rebalance(target, strat.name, cd)
            b = {**self.brain(), "day": cd.days[-1], "ts": time.time(), "target": target, "note": note, "steps": steps,
                 "regime_days": regime, "btc_ok": research.btc_uptrend(cd, len(cd.days) - 1, regime) if regime else None,
                 "explain": strat.explain}
            self.db.set("brain", b)
        except Exception as ex:
            self._log("Daily Brain", "error", f"Daily decision failed: {str(ex) or type(ex).__name__}. Retrying in 5 minutes.")
        finally:
            self._brain_busy = False
            self._brain_target = {}

    async def _brain_rebalance(self, target: dict[str, float], name: str, cd=None) -> tuple[str, list[str]]:
        """Sell the bot's coins the strategy no longer wants, then buy the ones it wants up to its share of your cap."""
        cfg, cur = self.settings["live"], self.live.currency
        pairs = getattr(self.live, "pairs", None) or {}
        prices = await self.live.prices() if hasattr(self.live, "prices") else {}
        owned = self.db.get("live_qty", {})
        total = (self.wallet or {}).get("total")
        if not total:
            raise ValueError("your Bitpanda balance hasn't been read yet")
        pot = self.fast.pot_size() if self.fast.on() else 0.0
        budget = min(cfg["max_invest"], (total - pot) * 0.98)  # 2% left for fees; the fast pot's share stays out
        self._brain_target = dict(target)
        day = time.strftime("%d.%m.%Y", time.gmtime(cd.days[-1])) if cd else "yesterday"
        steps = [f"Daily candle of {day} closed: replayed \"{name}\" on all history up to it",
                 "Strategy wants: " + (", ".join(f"{s} {round(w * 100)}%" for s, w in target.items()) or "nothing (cash)"),
                 f"Account {total:.2f} {cur}: budget {budget:.2f} {cur} (your cap {cfg['max_invest']:.0f}, 2% kept for fees"
                 + (f", {pot:.2f} {cur} fast pot kept out" if pot else "") + ")",
                 "Bot holds now: " + (", ".join(owned) or "nothing")]
        done = []
        bal = await self.live.balances()
        for sym in [s for s in owned if s not in target]:
            value = min(owned[sym], bal.get(sym, 0.0)) * prices.get(sym, 0.0)
            min_amt = self._min_order(sym, pairs)
            if 0 < value < min_amt:  # (0 = you sold it yourself: the sell below finds nothing and forgets it)
                # Fusion rejects orders under its minimum, sells too: retrying every day would only log errors
                done.append(f"{sym} ({value:.2f} {cur}) is below Fusion's {min_amt:g} {cur} minimum and can't be sold "
                            f"by the bot: sell it in the Bitpanda app, the bot then forgets it")
                continue
            errors = self.live_errors
            await self._live_sell(sym, reason=f"daily brain: {name} no longer holds it")
            if sym in self.db.get("live_qty", {}):
                self.live_errors = errors  # one coin Fusion won't sell must not stop live trading
                done.append(f"Fusion refused to sell {sym}: sell it in the Bitpanda app, the bot then forgets it")
            else:
                done.append(f"sold {sym}")
        owned = self.db.get("live_qty", {})
        for sym, w in sorted(target.items(), key=lambda kv: -kv[1]):
            if pairs and sym not in pairs:
                done.append(f"{sym} isn't on Fusion")
                continue
            blocked = self.guard().get(sym)
            if blocked:
                done.append(f"{sym} not bought: the Guardian blocks it ({blocked['reason']})")
                continue
            want = w * budget - owned.get(sym, 0.0) * prices.get(sym, 0.0)
            if want < 0.25 * w * budget:
                if sym in owned:
                    done.append(f"kept {sym}")
                continue  # already about right: no trades for small drift
            min_amt = max(self._min_order(sym, pairs), self.settings["risk"]["min_order_usd"])
            if want < min_amt:
                done.append(f"{sym}: {want:.2f} {cur} more would be below the minimum order")
                continue
            spent = 0.0
            parts = math.ceil(want / cfg["max_order"])  # equal orders inside your per-order cap
            chunk = want / parts
            if chunk < min_amt:
                parts, chunk = max(1, int(want // min_amt)), min(cfg["max_order"], want)
                if chunk < min_amt or cfg["max_order"] < min_amt:
                    done.append(f"{sym}: raise 'Biggest single live order' above {min_amt:g} {cur}")
                    continue
            for _ in range(parts):
                try:
                    amount, _ = await self._live_buy(sym, chunk, f"daily brain: {name}")
                except Exception as ex:  # one refused order skips this coin today, never the whole decision
                    done.append(f"{sym} not bought: {str(ex)[:160]}")
                    break
                self._log("Live Desk", "live", f"LIVE BUY {sym}: {amount:.2f} {cur} filled (daily brain: {name}).")
                spent += amount
                if amount < chunk - 0.01:
                    break  # cap or cash ran out
            if spent:
                done.append(f"bought {sym} for {spent:.2f} {cur}")
        holds = ", ".join(sorted(target)) or "nothing (cash)"
        trades = [d for d in done if not d.startswith("kept ")]
        note = f"holds {holds}" + (f"; {'; '.join(trades)}" if trades else "; no trades needed")
        self._log("Daily Brain", "live", f"Daily decision ({name}): {note}.")
        return note, steps + (done or ["No trades needed: the bot already holds what the strategy wants"])

    # ------------------------------------------------------------------ the real account
    async def _poll_wallet(self) -> None:
        """The real account, read-only: cash, the bot's coins with value and P&L, what Fusion can trade."""
        if self.settings.simulate:
            return
        b = self.live or self._viewer
        if b is None:
            try:
                b = make_live_broker(self.settings)
            except ValueError:
                return  # no key: nothing to show
            try:
                await b.connect()
            except Exception as ex:
                self.wallet = {"error": str(ex) or type(ex).__name__, "ts": time.time()}
                return
            self._viewer = b
        try:
            read_at = time.time()
            bal = await b.balances()
            prices = await b.prices() if hasattr(b, "prices") else {}
        except Exception as ex:
            err = str(ex) or type(ex).__name__
            if self.wallet and not self.wallet.get("error") and time.time() - self.wallet.get("ts", 0) < 600:
                self.wallet["stale"] = err  # a short network hiccup: keep showing the last good numbers
                return
            self.wallet = {"error": err, "ts": time.time()}
            return
        if getattr(b, "pairs", None):
            self.fusion_coins = set(b.pairs)
        qty, cost = self.db.get("live_qty", {}), self.db.get("live_cost", {})
        fqty, fcost = self.db.get("fast_qty", {}), self.db.get("fast_cost", {})
        fast_coins = []
        for sym, q in fqty.items():
            q = min(q, max(0.0, bal.get(sym, 0.0) - qty.get(sym, 0.0)))
            px = prices.get(sym, 0.0)
            fast_coins.append({"symbol": sym, "qty": q, "cost": round(fcost.get(sym, 0.0), 2), "price": px,
                               "value": round(q * px, 2), "pnl": round(q * px - fcost.get(sym, 0.0), 2) if px else None})
        coins = []
        for sym, q in qty.items():
            q = min(q, bal.get(sym, 0.0))  # count only what's really in the account (you may have sold some by hand)
            px = prices.get(sym, 0.0)
            coins.append({"symbol": sym, "qty": q, "cost": round(cost.get(sym, 0.0), 2), "price": px,
                          "value": round(q * px, 2), "pnl": round(q * px - cost.get(sym, 0.0), 2) if px else None})
        cur = b.currency
        yours = {}
        for k, v in bal.items():
            left = (v or 0) - qty.get(k, 0.0) - fqty.get(k, 0.0)  # your part: whatever the bot didn't buy
            if k not in ("FIAT", cur) and left > 1e-12:
                yours[k] = left
        own = [{"symbol": k, "qty": round(v, 8), "price": prices.get(k), "value": round(v * prices.get(k, 0), 2),
                "tradable": k in (getattr(b, "pairs", None) or {})} for k, v in yours.items()]
        own.sort(key=lambda c: -c["value"])
        bot_value = sum(c["value"] for c in coins)
        fast_value = sum(c["value"] for c in fast_coins)
        yours_value = sum(c["value"] for c in own)
        fiat = bal.get("FIAT", 0.0)
        self.wallet = {
            "venue": b.name, "currency": cur, "fiat": round(fiat, 2), "coins": coins,
            "bot_value": round(bot_value, 2), "bot_cost": round(sum(cost.values()), 2),
            "bot_pnl": round(bot_value - sum(cost.get(c["symbol"], 0) for c in coins if c["price"]), 2),
            "yours": {c["symbol"]: c["qty"] for c in own}, "own_coins": own, "yours_value": round(yours_value, 2),
            "fast_coins": fast_coins, "fast_value": round(fast_value, 2),
            "total": round(fiat + bot_value + fast_value + yours_value, 2), "use_my_coins": self.settings["live"]["use_my_coins"],
            "max_invest": self.settings["live"]["max_invest"], "ts": time.time(),
        }
        total = self.wallet["total"]
        allc = {c["symbol"]: c for c in own}  # every coin in the account, one list
        for c in coins:
            a = allc.setdefault(c["symbol"], {"symbol": c["symbol"], "qty": 0.0, "price": c["price"], "value": 0.0,
                                              "tradable": True})
            a["qty"] = round(a["qty"] + c["qty"], 8)
            a["value"] = round(a["value"] + c["value"], 2)
            a["bot"] = True
            a["pnl"] = c["pnl"]
        for c in fast_coins:
            a = allc.setdefault(c["symbol"], {"symbol": c["symbol"], "qty": 0.0, "price": c["price"], "value": 0.0,
                                              "tradable": True})
            a["qty"] = round(a["qty"] + c["qty"], 8)
            a["value"] = round(a["value"] + c["value"], 2)
            a["fast"] = True
        self.wallet["all_coins"] = sorted(allc.values(), key=lambda c: -c["value"])
        self.wallet["coins_value"] = round(total - fiat, 2)
        held = {k: float(v or 0) for k, v in bal.items() if k not in ("FIAT", cur) and (v or 0) > 1e-12}
        self._spot_flows(fiat, held, prices, total, read_at, cur)
        self.wallet.update(self._track_account(total, fiat, held, prices))

    def _spot_flows(self, fiat: float, held: dict, prices: dict, total: float, read_at: float, cur: str) -> None:
        """Money you pay in or take out is not the bot's gain or loss. Between two reads of the account, every change
        in cash and coins that the bot's own recorded orders don't explain is a deposit or withdrawal (coins sent in
        or out count too; a coin you buy by hand in the app nets out, cash out and coin in). It moves the starting
        point of the bot's own gain and of "since start", so neither jumps."""
        if self.__dict__.get("_brain_busy") or self.__dict__.get("_fast_trading"):
            return  # orders in flight: compare after they are booked
        prev = self.db.get("flow_state")
        self.db.set("flow_state", {"ts": read_at, "fiat": fiat, "qty": held})
        if not prev:
            return
        cash, qty = 0.0, {}
        for t in self.db.query("SELECT side,symbol,qty,notional,fee FROM trades WHERE mode='live' AND ts>? AND ts<=?",
                               (prev["ts"], read_at)):
            n, f, q = float(t["notional"] or 0), float(t["fee"] or 0), float(t["qty"] or 0)
            sign = 1 if t["side"] == "BUY" else -1
            cash += -sign * n - f
            qty[t["symbol"]] = qty.get(t["symbol"], 0.0) + sign * q
        flow = fiat - prev["fiat"] - cash
        for sym in set(held) | set(prev["qty"]):
            dq = held.get(sym, 0.0) - prev["qty"].get(sym, 0.0) - qty.get(sym, 0.0)
            if abs(dq) > 1e-12:
                flow += dq * prices.get(sym, 0.0)
        if abs(flow) >= max(2.5, total * 0.005):
            self.book_flow(flow, "spotted automatically")

    def book_flow(self, amount: float, how: str) -> dict:
        """A deposit (+) or withdrawal (-): shift the starting points so it never counts as the bot's gain or loss."""
        hs = self.db.get("hold_start")
        if hs:
            hs["fiat"] = hs["fiat"] + amount
            self.db.set("hold_start", hs)
        st = self.db.get("account_start")
        if st:
            st["total"] = st["total"] + amount
            self.db.set("account_start", st)
        flows = (self.db.get("flows") or []) + [{"ts": time.time(), "amount": round(amount, 2), "how": how}]
        self.db.set("flows", flows[-50:])
        cur = (self.wallet or {}).get("currency", "CHF")
        self._log("Live Desk", "info", f"{'Deposit' if amount > 0 else 'Withdrawal'} of {abs(amount):.2f} {cur} "
                                       f"{how}: not counted as the bot's gain or loss.")
        return {"flows": flows[-50:]}

    def _bot_edge(self, total: float, fiat: float, held: dict | None, prices: dict | None) -> float | None:
        """What the bot's trading added or lost: your account now minus what it would be worth if nobody had
        traded (the cash and coins you had when measuring started, at today's prices). Coin price swings cancel out."""
        if held is None or prices is None:
            return None
        start = self.db.get("hold_start")
        if not start:
            start = {"ts": time.time(), "fiat": fiat, "coins": held,
                     "values": {k: q * prices.get(k, 0.0) for k, q in held.items()}}
            self.db.set("hold_start", start)
        untouched = start["fiat"] + sum(q * prices[k] if prices.get(k) else start["values"].get(k, 0.0)
                                        for k, q in start["coins"].items())
        return round(total - untouched, 2)

    def _track_account(self, total: float, fiat: float = 0.0, held: dict | None = None,
                       prices: dict | None = None) -> dict:
        """Remember your account total over time: start value, 24h change, the bot's own gain or loss, a chart."""
        now = time.time()
        edge = self._bot_edge(total, fiat, held, prices)
        hist = self.db.get("wallet_hist", [])
        if not hist or now - hist[-1][0] >= 300:
            hist = (hist + [[now, round(total, 2), edge]])[-4000:]
            self.db.set("wallet_hist", hist)
        start = self.db.get("account_start") or {"ts": now, "total": total}
        if not self.db.get("account_start"):
            self.db.set("account_start", start)
        day = next((h[1] for h in hist if h[0] >= now - 86400), total)
        step = max(1, len(hist) // 300)
        hs = self.db.get("hold_start") or {}
        flows = self.db.get("flows") or []
        return {"bot_edge": edge, "bot_edge_since": hs.get("ts"), "flows": flows[-5:],
                "paid_in": round(sum(f["amount"] for f in flows), 2),
                "bot_edge_pct": round(edge / (total - edge) * 100, 2) if edge is not None and total - edge else None,
                "start_total": round(start["total"], 2), "start_ts": start["ts"],
                "change": round(total - start["total"], 2),
                "change_pct": round((total / start["total"] - 1) * 100, 2) if start["total"] else 0.0,
                "change_24h": round(total - day, 2), "history": hist[::step] + ([hist[-1]] if hist and len(hist) % step else [])}

    def set_kill_switch(self, on: bool) -> None:
        self.db.set("kill_switch", on)
        self._log("Risk Officer", "warn" if on else "info", "KILL SWITCH ON: no decisions and no buys." if on
                  else "Kill switch off.")

    # ------------------------------------------------------------------ loops
    async def run(self) -> None:
        if self.mode == "live":  # after a restart (crash, deploy, server maintenance): reconnect on its own
            for attempt in range(6):
                r = await self.set_mode("live")
                if r["ok"] or self.db.get("moved"):
                    break
                self._log("Engine", "warn", f"Reconnect to Bitpanda failed ({r.get('error', '')[:80]}), trying again in 30 s.")
                await asyncio.sleep(0 if self.settings.simulate else 30)
            if not r["ok"]:
                self.db.set("mode", "paper")
                self._notify_later(f"After a restart the bot could not reconnect to Bitpanda and is on STANDBY: {r.get('error', '')[:200]}. "
                                   "Open the dashboard and switch LIVE again.", title="⚠️ TradingBotty on STANDBY", tags=["warning"], priority=5)
            else:
                self._notify_later("The server restarted and the bot is trading again, nothing lost.", title="🔄 TradingBotty restarted",
                                   tags=["arrows_counterclockwise"], priority=2)
        self._log("Engine", "info", "Booting: loading prices" + (" (SIMULATED data)" if self.settings.simulate else ""))
        try:
            if not self.settings.simulate:
                await self.universe._load_pairs()  # exact Kraken pair names for every coin
        except Exception as ex:
            self._log("Engine", "warn", f"Kraken pair list failed: {ex}")
        await self._poll_wallet()
        await self.prices.backfill()
        self._log("Engine", "info", f"Team online: {len(self.team)} agents. "
                                    f"AI {'on' if self.llm.available else 'off (no API key): free rules'}.")
        e = self.settings["engine"]  # read on every loop, so dashboard changes apply without a restart
        await asyncio.gather(
            self._every(lambda: e["price_poll_seconds"], self.prices.poll_crypto),
            self._every(lambda: e["news_poll_seconds"], self._poll_news),
            self._every(lambda: e["tick_seconds"], self.tick),
            self._every(lambda: 60, self.snapshot),
            self._every(lambda: 2, self.publish_prices),
            self._every(lambda: 600, self._scan),
            self._every(lambda: 30, self._poll_wallet),
            self._every(lambda: 300, self.brain_tick),
            self._every(lambda: 60, self.fast.tick),
            self._every(lambda: 60, self.guard_tick),
            self._every(lambda: 60, self.morning_tick),
            self._every(lambda: 1800, self.research_tick),
        )

    async def _every(self, seconds, fn) -> None:
        while True:
            started = time.time()
            try:
                await fn()
            except Exception as ex:
                self._log("Engine", "error", f"{getattr(fn, '__name__', fn)} failed: {ex}")
            # sleep in short slices so a shorter interval set in the dashboard takes effect quickly
            while time.time() - started < seconds():
                await asyncio.sleep(min(1.0, max(0.05, seconds() - (time.time() - started))))

    async def _scan(self) -> None:
        await self.agent("radar").scan()

    async def _poll_news(self) -> None:
        fresh = await self.social.poll_news()
        self.agent("news").queue(fresh)
        self.db.set("news_seen", list(self.social._seen_links)[-3000:])

    async def tick(self) -> None:
        before = len(self.bb.news_events) and self.bb.news_events[0]["ts"]
        for node in self.sources + self.team:
            await node.step(self.bb)
            self.bus.publish("node", node.node())
        if self.bb.news_events and self.bb.news_events[0]["ts"] != before:
            self.db.set("news_events", self.bb.news_events[:150])
        self.bus.publish("state", self.state(light=True))

    async def snapshot(self) -> None:
        if self.prices.quotes:
            self._save_candles()

    async def publish_prices(self) -> None:
        self.bus.publish("prices", self.ticker())

    def ticker(self) -> list[dict]:
        return [{"symbol": q.symbol, "price": q.price, "change": round(q.change_24h_pct, 2),
                 "spark": [round(c[1], 8) for c in list(q.candles)[-60:]]}
                for q in self.prices.quotes.values() if q.price]

    def state(self, light: bool = False) -> dict:
        res = self.db.get("research") or {}
        s = {
            "mode": self.mode, "kill_switch": self.kill_switch, "simulate": self.settings.simulate,
            "ai": self.llm.available, "budget": self.budget.snapshot(), "uptime": time.time() - self.started,
            "wallet": self.wallet,
            "brain": self.brain(),
            "trend": self.trend,
            "guard": self.guard(),
            "shocks": self.shocks,
            "professor": self.db.get("professor_last") or {},
            "research": {"ts": res.get("ts"), "robust": sum(1 for r in res.get("rows", []) if r["robust"]),
                         "tested": res.get("strategies_tested")},
            "telegram": bool(self.settings.telegram_token and self.settings.telegram_chat),
            "moved": self.db.get("moved"),
            "phone": {"channels": self.phone_channels(), "hour": self.settings["phone"]["morning_hour"],
                      "trades": bool(self.settings["phone"].get("trades")), "last": self.db.get("briefing_day")},
            "news": [{k: e.get(k) for k in ("ts", "source", "title", "link", "symbols", "sentiment", "impact", "event", "ai")}
                     for e in self.bb.news_events[:25]],
            "live_trades": self.db.query("SELECT COUNT(*) n, MAX(ts) last FROM trades WHERE mode='live'")[0],
            "fast": self.fast.status(),
            "live_caps": {k: self.settings["live"][k] for k in ("max_invest", "max_order", "max_spread_pct", "use_my_coins")},
        }
        if not light:
            s["ticker"] = self.ticker()
            s["nodes"] = [n.node() for n in self.sources + self.team]
            s["log"] = self.db.query("SELECT ts,agent,level,message FROM agent_log ORDER BY id DESC LIMIT 300")[::-1]
            s["trades"] = self.recent_trades()
            s["report"] = self.daily_report()
        return s

    def recent_trades(self, limit: int = 50) -> list[dict]:
        return self.db.query("SELECT ts,mode,symbol,side,notional,price,fee,reason FROM trades WHERE mode='live' "
                             "ORDER BY id DESC LIMIT ?", (limit,))
