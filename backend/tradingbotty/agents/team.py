"""The trading team. Each class is one node in the graph.

Free agents use math only. Agents marked uses_ai call Claude when the AI budget allows, and fall back
to simple rules when it doesn't, so the bot keeps working at zero AI cost.
"""
from __future__ import annotations

import math
import time

from ..decisions import blend, entry_blocker, exit_reason, expected_move, risk_check
from ..strategy import squash, swing_signal, technical_signals
from .base import Agent, Blackboard

# --------------------------------------------------------------------------------------------
# Analysts
# --------------------------------------------------------------------------------------------


class CryptoAnalyst(Agent):
    id = "crypto"
    name = "Crypto Analyst"
    role = "Momentum, trend, dips and breakouts for every coin, from 1-minute prices"
    inputs = ["src_kraken"]
    explain = ("Pure math, no AI, free. Every tick it takes the last few hours of 1-minute prices for each coin and "
               "computes four signals between -1 and +1: momentum (last 15 minutes vs normal volatility), trend "
               "(20- vs 60-minute average), dip (RSI: oversold is positive) and breakout (above the last-hour high). "
               "From hourly candles it adds the multi-day trend (1 to 3 weeks), the slow signal swing traders use.")
    outputs = "momentum, trend, dip, breakout and volatility per coin"

    async def run(self, bb: Blackboard) -> None:
        strongest = []
        for q in self.ctx.prices.crypto():
            closes = q.closes()
            sig = technical_signals(closes)
            sig["swing"] = swing_signal(q.hourly_closes())
            bb.tech[q.symbol] = sig
            strongest.append((sig["momentum"] + sig["trend"], q.symbol, q.change_24h_pct))
        strongest.sort(reverse=True)
        if strongest:
            top, bottom = strongest[0], strongest[-1]
            self.summary = f"strongest {top[1]} ({top[2]:+.1f}% 24h), weakest {bottom[1]} ({bottom[2]:+.1f}%)"
            self.detail = {s: round(v, 2) for v, s, _ in strongest}
            if self.runs % 20 == 0:
                self.say(f"Scanned {len(strongest)} coins. Strongest right now: {top[1]}, weakest: {bottom[1]}.")


class MarketAnalyst(Agent):
    id = "market"
    name = "Market Analyst"
    role = "S&P 500, Nasdaq and big tech: is the overall market risk-on or risk-off?"
    inputs = ["src_yahoo", "src_kraken", "src_feargreed"]
    can_disable = True
    explain = ("Pure math, free. Computes the same signals for your stock watchlist, then one market mood: Bitcoin's "
               "trend plus a contrarian Fear & Greed tilt for coins (extreme fear counts as a buying chance), and the "
               "S&P 500 and Nasdaq trend for stocks. Switched off, market mood counts as neutral.")
    outputs = "market mood per symbol, stock signals, the regime shown in the cockpit"

    def on_disable(self, bb: Blackboard) -> None:
        bb.market = {}

    async def run(self, bb: Blackboard) -> None:
        for q in self.ctx.prices.stocks():
            bb.tech[q.symbol] = {**technical_signals(q.closes()), "swing": swing_signal(q.hourly_closes())}

        def regime(sym: str) -> float:
            t = bb.tech.get(sym)
            return 0.0 if not t else 0.6 * t["trend"] + 0.4 * t["momentum"]

        stocks = (regime("SPY") + regime("QQQ")) / 2
        btc = regime("BTC")
        fg = self.ctx.social.fear_greed
        # contrarian tilt: extreme fear tends to be a better time to buy than extreme greed
        fg_tilt = 0.0 if fg is None else (50 - fg) / 50
        crypto = 0.7 * btc + 0.3 * fg_tilt
        for q in self.ctx.prices.quotes.values():
            bb.market[q.symbol] = max(-1.0, min(1.0, crypto if q.kind == "crypto" else stocks))
        mood = "risk-on" if crypto > 0.15 else "risk-off" if crypto < -0.15 else "neutral"
        bb.regime = {"stocks": round(stocks, 2), "crypto": round(crypto, 2), "fear_greed": fg,
                     "fear_greed_label": self.ctx.social.fear_greed_label, "mood": mood}
        self.summary = f"crypto {mood} ({crypto:+.2f}), stocks {stocks:+.2f}, fear&greed {fg if fg is not None else '?'}"
        prev = self.detail.get("mood")
        self.detail = bb.regime
        if prev and prev != mood:
            self.say(f"Market mood flipped from {prev} to {mood}.")


class HypeScout(Agent):
    id = "hype"
    name = "Hype Scout"
    role = "Counts buzz on Reddit and CoinGecko trending, and spots sudden spikes"
    inputs = ["src_reddit", "src_coingecko"]
    can_disable = True
    explain = ("Free. Counts how often each coin or stock is mentioned in hot posts of your subreddits (popular posts "
               "count more) and compares that with its own recent average. Buzz well above normal is positive. Coins "
               "trending on CoinGecko get a bonus. It needs a few polls before the average means anything.")
    outputs = "hype score per symbol"

    def on_disable(self, bb: Blackboard) -> None:
        bb.hype = {}

    async def run(self, bb: Blackboard) -> None:
        social = self.ctx.social
        for sym in self.ctx.prices.quotes:
            v = social.hype_velocity(sym)
            trending = 0.3 if sym in social.trending else 0.0
            bb.hype[sym] = max(-1.0, min(1.0, squash(v) + trending))
        top = sorted(bb.hype.items(), key=lambda kv: -kv[1])[:3]
        self.summary = "buzz leaders: " + ", ".join(f"{s} {v:+.2f}" for s, v in top)
        self.detail = {"mentions": social.mention_counts, "trending": social.trending[:7], "posts": social.posts[:8]}
        for s, v in top:
            if v > 0.6 and self.detail.get("_said_" + s, 0) < time.time() - 1800:
                self.detail["_said_" + s] = time.time()
                self.say(f"Buzz spike on {s}: chatter is well above its normal level.")


class HypeDetective(Agent):
    id = "detective"
    name = "Hype vs Price Detective"
    role = "Compares buzz with price: early hype is a signal, late hype after a pump is a trap"
    inputs = ["hype", "crypto"]
    can_disable = True
    explain = ("Free. Checks hype against the last hour's price move. Buzz rising while the price is still flat is "
               "boosted (early). Buzz after a pump of more than 4% is turned negative (you'd be buying from the people "
               "who caused the hype).")
    outputs = "corrected hype scores and findings"

    async def run(self, bb: Blackboard) -> None:
        findings = []
        for sym, h in list(bb.hype.items()):
            q = self.ctx.prices.quotes.get(sym)
            if not q or len(q.candles) < 61:
                continue
            closes = q.closes()
            move_1h = math.log(closes[-1] / closes[-61]) * 100
            if h > 0.4 and abs(move_1h) < 1.0:
                bb.hype[sym] = min(1.0, h * 1.3)
                findings.append(f"{sym}: buzz rising, price still flat (early)")
            elif h > 0.4 and move_1h > 4.0:
                bb.hype[sym] = h * 0.3 - 0.2
                findings.append(f"{sym}: buzz after +{move_1h:.1f}% pump (late, careful)")
            elif h < -0.2 and move_1h < -3.0:
                findings.append(f"{sym}: buzz fading while price drops")
        self.summary = findings[0] if findings else "no hype/price divergence"
        new = [f for f in findings if f not in self.detail.get("findings", [])]
        self.detail = {"findings": findings}
        for f in new[:2]:
            self.say(f)


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
NEGATIVE = ["hack", "exploit", "ban", "lawsuit", "sue", "crash", "plunge", "fall", "drop", "sell-off", "selloff",
            "downgrade", "bear", "fraud", "outage", "investigation", "outflow", "liquidat", "miss"]


class NewsHunter(Agent):
    id = "news"
    name = "News Hunter"
    role = "Reads news feeds, rates each headline's direction and impact (mergers, ETFs, hacks, regulation, macro)"
    inputs = ["src_news"]
    uses_ai = True
    can_disable = True
    default_model = "fast"
    explain = ("Collects new headlines from your news feeds (duplicates and anything older than 12 hours are skipped) "
               "and sends them to Claude in batches of up to 25. Claude tags the affected tickers and rates direction "
               "and impact. Without AI or budget it falls back to free keyword rules. A headline's effect fades over "
               "about 3 hours.")
    outputs = "news score per symbol, rated headlines"
    default_prompt = (
        "You are a sharp markets news analyst for a small trading bot. For each headline, list the affected "
        "tickers from this set only: {symbols} (use SPY for broad US-market or macro news, BTC for "
        "broad crypto news). sentiment is -1 (very bearish) to 1 (very bullish) for those tickers over the next "
        "hours. impact is 0 (noise) to 1 (market-moving). event is one word: merger, etf, regulation, hack, "
        "earnings, macro, partnership, listing, rumor, other. takeaway: one sentence on what matters most.")

    def on_disable(self, bb: Blackboard) -> None:
        bb.news = {}
        self.pending = []

    def __init__(self, ctx):
        super().__init__(ctx)
        self.pending: list = []

    def queue(self, headlines: list) -> None:
        self.pending.extend(headlines)

    async def run(self, bb: Blackboard) -> None:
        batch, self.pending = self.pending[:25], self.pending[25:]
        if batch:
            use_ai = self.ctx.settings["ai"]["news_ai"]
            scored = (await self._score_ai(batch) if use_ai else None) or self._score_rules(batch)
            bb.news_events = (scored + bb.news_events)[:150]
            for ev in scored:
                if ev["impact"] >= 0.6 and ev["symbols"]:
                    arrow = "bullish" if ev["sentiment"] > 0 else "bearish"
                    self.say(f"{arrow.upper()} {', '.join(ev['symbols'])}: {ev['title']} ({ev['event']})")
        # decayed sentiment per symbol: news older than a few hours matters less
        now = time.time()
        agg: dict[str, float] = {}
        for ev in bb.news_events:
            w = math.exp(-(now - ev["ts"]) / (3 * 3600))
            for s in ev["symbols"]:
                agg[s] = agg.get(s, 0.0) + ev["sentiment"] * ev["impact"] * w
        bb.news = {s: squash(v) for s, v in agg.items()}
        top = sorted(bb.news.items(), key=lambda kv: -abs(kv[1]))[:3]
        self.summary = ("news tilt: " + ", ".join(f"{s} {v:+.2f}" for s, v in top)) if top else "no relevant news yet"
        self.detail = {"recent": bb.news_events[:8], "queued": len(self.pending)}

    async def _score_ai(self, batch) -> list[dict] | None:
        symbols = list(self.ctx.prices.quotes)
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
            syms = [s for s in it.get("symbols", []) if s in self.ctx.prices.quotes]
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
            out.append({"ts": h.ts, "source": h.source, "title": h.title, "link": h.link, "symbols": h.symbols,
                        "sentiment": 0.6 if pos > neg else -0.6, "impact": 0.4, "event": "keyword", "ai": False})
        return out


PROF_SCHEMA = {
    "type": "object",
    "properties": {
        "assessment": {"type": "string"},
        "risk_appetite": {"type": "number"},
        "avoid": {"type": "array", "items": {"type": "string"}},
        "idea": {"type": "string"},
    },
    "required": ["assessment", "risk_appetite", "avoid", "idea"],
    "additionalProperties": False,
}


class Professor(Agent):
    id = "professor"
    name = "The Professor"
    role = "Economics and trading-theory heavyweight: reviews the whole picture a few times a day, sets risk appetite"
    inputs = ["market", "news", "detective", "optimizer"]
    uses_ai = True
    can_disable = True
    default_model = "deep"
    explain = ("The expensive brain, used rarely. Every few hours (you set how often) it gets a short brief: market "
               "mood, top news, hype, coin moves and the strategy leaderboard. It returns a risk appetite between 0.5 "
               "and 1.5 that scales every position size, a list of tickers to avoid for now, and one testable idea. "
               "It can't place or force trades. Switched off, risk appetite is 1.0 and nothing is avoided.")
    outputs = "risk appetite, avoid list, assessment and a research idea"
    default_prompt = (
        "You are a Stanford finance professor coaching a tiny, playful spot-only trading bot (about 100 USD, "
        "fees about {fee}% per trade, no leverage, no shorting). Think in probabilities, base rates, Kelly sizing, "
        "regime shifts and behavioral finance. Given the brief, return: assessment (2-3 sentences, plain words), "
        "risk_appetite between 0.5 (defensive) and 1.5 (aggressive) that scales position sizes, avoid (tickers to "
        "skip for now, may be empty), and idea (one concrete, testable strategy idea for the experiments).")

    def on_disable(self, bb: Blackboard) -> None:
        bb.risk_appetite, bb.avoid = 1.0, set()

    def run_now(self) -> None:
        self.next_due = 0

    def __init__(self, ctx):
        super().__init__(ctx)
        self.next_due = time.time() + 600  # let the feeds warm up first: a lecture on empty data wastes money

    async def run(self, bb: Blackboard) -> None:
        if not self.ctx.settings["ai"]["professor_on"]:
            self.on_disable(bb)
            self.summary = "reviews paused in Controls; risk appetite 1.00"
            return
        if time.time() < self.next_due:
            return
        if self.ctx.social.fear_greed is None or not bb.news_events or not bb.regime:
            self.summary = "waiting for fresh market, news and sentiment data"
            return
        every = self.ctx.settings["engine"]["professor_every_minutes"] * 60
        self.next_due = time.time() + every
        board = self.ctx.leaderboard()[:5]
        brief = {
            "regime": bb.regime,
            "top_news": [{k: e[k] for k in ("title", "symbols", "sentiment", "impact")} for e in bb.news_events[:10]],
            "hype": {s: round(v, 2) for s, v in sorted(bb.hype.items(), key=lambda kv: -abs(kv[1]))[:6]},
            "coins_24h": {q.symbol: round(q.change_24h_pct, 2) for q in self.ctx.prices.crypto()},
            "strategies": [{k: b[k] for k in ("name", "return_pct", "trades", "max_drawdown_pct")} for b in board],
        }
        res = await self.ctx.llm.json_call(
            self.name, self.prompt.replace("{fee}", str(self.ctx.settings["paper"]["fee_pct"])), str(brief),
            PROF_SCHEMA, deep=True, max_tokens=3000, model=self.model,
        )
        if not res or res.get("_over_budget"):
            self.summary = "resting (no AI key or budget); risk appetite stays " + f"{bb.risk_appetite:.2f}"
            return
        self.cost += res.get("_cost", 0)
        bb.risk_appetite = max(0.5, min(1.5, float(res.get("risk_appetite", 1.0))))
        bb.avoid = {s for s in res.get("avoid", []) if s in self.ctx.prices.quotes}
        self.summary = f"risk appetite {bb.risk_appetite:.2f}; avoid {', '.join(sorted(bb.avoid)) or 'nothing'}"
        self.detail = {"assessment": res.get("assessment"), "idea": res.get("idea"), "ts": time.time()}
        self.ctx.db.set("professor_last", {"risk_appetite": bb.risk_appetite, "avoid": sorted(bb.avoid),
                                           "detail": self.detail})
        self.say(f"Lecture: {res.get('assessment')}")
        if res.get("idea"):
            self.say(f"Research idea: {res['idea']}")


# --------------------------------------------------------------------------------------------
# Decision makers
# --------------------------------------------------------------------------------------------


class Predictor(Agent):
    id = "predictor"
    name = "Predictor"
    role = "Blends every signal into one score per coin, separately for each strategy variant"
    inputs = ["crypto", "market", "hype", "detective", "news", "professor"]
    explain = ("Pure math, free. For every strategy it multiplies each signal by that strategy's weight and adds them "
               "up, giving one score per symbol (about -1 to +1). A strategy buys when the score is above its buy "
               "threshold and sells when it falls below its sell threshold. The weights are what the Optimizer and "
               "your sliders change.")
    outputs = "a score per symbol for every strategy, and the signal breakdown behind it"

    async def run(self, bb: Blackboard) -> None:
        bb.scores, bb.signals = {}, {}
        for sym, q in self.ctx.prices.quotes.items():
            t = bb.tech.get(sym, {})
            bb.signals[sym] = {
                "momentum": t.get("momentum", 0.0), "trend": t.get("trend", 0.0),
                "reversion": t.get("reversion", 0.0), "breakout": t.get("breakout", 0.0), "swing": t.get("swing", 0.0),
                "hype": bb.hype.get(sym, 0.0), "news": bb.news.get(sym, 0.0), "market": bb.market.get(sym, 0.0),
            }
        for v in self.ctx.variants.values():
            w = v.config.weights()
            bb.scores[v.id] = {
                sym: blend(w, sig) for sym, sig in bb.signals.items()
                if (v.config.trade_stocks if self.ctx.prices.quotes[sym].kind == "stock" else v.config.trade_crypto)
            }
        champ = self.ctx.champion()
        if champ and bb.scores.get(champ.id):
            ranked = sorted(bb.scores[champ.id].items(), key=lambda kv: -kv[1])
            self.summary = "champion's picks: " + ", ".join(f"{s} {x:+.2f}" for s, x in ranked[:3])
            self.detail = {s: round(x, 3) for s, x in ranked}


class RiskOfficer(Agent):
    """Plain code, not AI: it can't be talked into a bad trade."""
    id = "risk"
    name = "Risk Officer"
    role = "Hard safety gate in plain code: cash-only, spot-only, size caps, daily loss stop, kill switch"
    inputs = ["predictor"]
    kind = "gate"
    explain = ("Plain code, no AI, so nothing can talk it into a bad trade. Every buy from every strategy passes "
               "through it: it caps the size per position, the number of positions and the daily loss, keeps a cash "
               "reserve, honors the kill switch, and never spends more cash than there is. There is no code path for "
               "margin, leverage or short selling, so the account can't go below zero.")
    outputs = "approved order size, or a reason for blocking"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.blocked = 0
        self.reasons: dict[str, int] = {}

    async def run(self, bb: Blackboard) -> None:
        r = self.ctx.settings["risk"]
        ks = " | KILL SWITCH ON" if self.ctx.kill_switch else ""
        self.summary = (f"max {r['max_position_pct']}%/position, {r['max_open_positions']} positions, "
                        f"daily stop -{r['max_daily_loss_pct']}%, blocked {self.blocked}{ks}")
        self.detail = {"limits": {k: r[k] for k in ("max_position_pct", "max_open_positions", "max_daily_loss_pct",
                                                     "min_cash_reserve_usd", "min_order_usd")},
                       "blocked_by_reason": self.reasons}

    def check_buy(self, variant, symbol: str, usd: float, prices: dict) -> tuple[float, str | None]:
        """Returns (allowed_usd, reason_if_blocked)."""
        allowed, why = risk_check(self.ctx.settings["risk"], variant.broker, variant.day_start_equity,
                                  self.ctx.kill_switch, symbol, usd, prices)
        if why:
            self.reasons[why] = self.reasons.get(why, 0) + 1
        return allowed, why


def expected_move_pct(bb: Blackboard, quote) -> float:
    return expected_move(quote.change_24h_pct, bb.tech.get(quote.symbol, {}).get("vol", 0.0))


class Buyer(Agent):
    id = "buyer"
    name = "Buyer"
    role = "Places the orders: paper for every strategy, and live money for the champion when the switch is on"
    inputs = ["risk"]
    explain = ("Every tick, for every strategy: first checks exits on each open position (stop loss, take profit, "
               "trailing stop, weak score after the minimum hold, or the Professor's avoid list). Then it walks the "
               "best scores and buys while the strategy's rules allow: buy threshold, position limit, cooldown, "
               "hourly buy limit and fee guard. Size = position % x equity x the Professor's risk appetite, then cut "
               "by the Risk Officer. Stocks only trade while the US market is open. Live mode copies the champion.")
    outputs = "orders, and why each symbol was or wasn't bought"

    async def run(self, bb: Blackboard) -> None:
        quotes = self.ctx.prices.quotes
        prices = {s: q.price for s, q in quotes.items() if q.price}
        risk: RiskOfficer = self.ctx.agent("risk")
        now = time.time()
        made = 0
        champ = self.ctx.champion()
        why_not: dict[str, str] = {}
        for v in self.ctx.variants.values():
            cfg, b = v.config, v.broker
            if cfg.hold:
                made += await self._hold(v, quotes, prices)
                continue
            scores = bb.scores.get(v.id, {})
            # 1) exits first
            for sym, pos in list(b.positions.items()):
                q = quotes.get(sym)
                if not q or not q.price or not q.tradable:
                    continue  # e.g. stock market closed: sell at the next open
                reason = exit_reason(cfg, pos, q.price, scores.get(sym, 0.0), now, bb.avoid)
                if reason:
                    await self.ctx.execute(v, sym, "SELL", pos.qty, q.price, reason, fraction=1.0)
                    made += 1
            # 2) entries, best score first
            if self.ctx.kill_switch:
                if v is champ:
                    why_not = {s: "kill switch is on" for s in scores}
                continue
            buys_last_hour = self.ctx.buys_since(v.id, now - 3600)
            for sym, score in sorted(scores.items(), key=lambda kv: -kv[1]):
                q = quotes.get(sym)
                if not q or not q.price:
                    continue
                if not q.tradable:
                    blocker = "US market closed"
                else:
                    blocker = entry_blocker(cfg, b, sym, score, now, bb.avoid, buys_last_hour, expected_move_pct(bb, q))
                if blocker:
                    if v is champ:
                        why_not[sym] = blocker
                    continue
                equity = b.equity(prices)
                want = equity * cfg.position_pct / 100 * bb.risk_appetite
                usd, why = risk.check_buy(v, sym, want, prices)
                if why:
                    risk.blocked += 1
                    if v is champ:
                        why_not[sym] = why
                        self.ctx.throttled_say(risk, f"Blocked {v.name} buying {sym}: {why}.")
                    continue
                await self.ctx.execute(v, sym, "BUY", usd, q.price, f"score {score:+.2f}",
                                       fraction=usd / equity if equity else 0)
                buys_last_hour += 1
                made += 1
        if champ:
            eq = champ.broker.equity(prices)
            self.summary = f"champion {champ.name}: {eq:.2f} USD, {len(champ.broker.positions)} open"
        self.detail = {"orders_this_tick": made, "why_not": why_not}

    async def _hold(self, v, quotes, prices) -> int:
        """Benchmark: buy the first four coins once, about a quarter each, then never trade again."""
        if self.ctx.buys_since(v.id, 0) or v.broker.positions:
            return 0
        basket = hold_basket(quotes)
        made = 0
        for sym in basket:
            usd = min(v.broker.equity(prices) * 0.24, v.broker.cash - 0.01)
            if usd > 1:
                await self.ctx.execute(v, sym, "BUY", usd, prices[sym], "buy & hold benchmark", fraction=0.24)
                made += 1
        return made


def hold_basket(quotes) -> list[str]:
    return [s for s, q in quotes.items() if q.kind == "crypto" and q.price][:4]
