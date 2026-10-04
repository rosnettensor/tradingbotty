"""The engine: owns the feeds, the agent team, the strategy variants and the paper/live switch."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass

from .agents.base import Blackboard, Source
from .agents.optimizer import Optimizer
from .agents.team import (Buyer, CryptoAnalyst, HypeDetective, HypeScout, MarketAnalyst, NewsHunter, Predictor,
                          Professor, RiskOfficer)
from .brokers.bitpanda import BitpandaBroker
from .brokers.paper import PaperBroker, Position
from .bus import Bus
from .config import Settings
from .data.prices import PriceFeed
from .data.social import SocialFeed
from .db import DB
from .llm import LLM, Budget
from .strategy import SEED_VARIANTS, StrategyConfig


@dataclass
class Variant:
    id: str
    name: str
    config: StrategyConfig
    broker: PaperBroker
    champion: bool
    created: float
    start_equity: float
    day_start_equity: float = 0.0
    day_start_ts: float = 0.0


class Engine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = DB(settings.db_path)
        self.bus = Bus()
        m = settings["markets"]
        self.prices = PriceFeed(m["crypto"], m["stocks"], settings.simulate, self._log)
        self.social = SocialFeed(m["crypto"] + m["stocks"], settings.simulate, self._log)
        ab = settings["ai_budget"]
        self.budget = Budget(self.db, ab)
        self.llm = LLM(settings.anthropic_api_key, self.budget, self.db, ab["fast_model"], ab["deep_model"])
        self.bb = Blackboard()
        self.variants: dict[str, Variant] = {}
        self.live: BitpandaBroker | None = None
        self.live_errors = 0
        self._last_said: dict[str, float] = {}
        self.started = time.time()

        h = lambda key, feed: (lambda: feed.healthy.get(key, False))  # noqa: E731
        self.sources = [
            Source(self, "src_kraken", "Kraken prices", "Live crypto prices (free public API)", h("crypto", self.prices)),
            Source(self, "src_yahoo", "Yahoo stocks", "US stock prices, 1-minute bars", h("stock", self.prices)),
            Source(self, "src_reddit", "Reddit", "Hot posts from crypto and stock subreddits", h("reddit", self.social)),
            Source(self, "src_coingecko", "CoinGecko trending", "Most searched coins", lambda: bool(self.social.trending)),
            Source(self, "src_feargreed", "Fear & Greed", "Crypto market sentiment index", lambda: self.social.fear_greed is not None),
            Source(self, "src_news", "News feeds", "CoinDesk, Cointelegraph, Yahoo Finance, CNBC", h("news", self.social)),
        ]
        self.team = [CryptoAnalyst(self), MarketAnalyst(self), HypeScout(self), HypeDetective(self), NewsHunter(self),
                     Professor(self), Predictor(self), RiskOfficer(self), Buyer(self), Optimizer(self)]
        self._by_id = {a.id: a for a in self.sources + self.team}
        self._load_variants()

    # ------------------------------------------------------------------ state
    @property
    def mode(self) -> str:
        return self.db.get("mode", "paper")

    @property
    def kill_switch(self) -> bool:
        return self.db.get("kill_switch", False)

    def agent(self, id: str):
        return self._by_id[id]

    def champion(self) -> Variant | None:
        return next((v for v in self.variants.values() if v.champion), None)

    def _log(self, agent: str, level: str, message: str) -> None:
        if level == "warn":  # feeds can fail every poll; say it once per half hour, not every minute
            key = f"{agent}:{message[:40]}"
            if time.time() - self._last_said.get(key, 0) < 1800:
                return
            self._last_said[key] = time.time()
        self.bus.publish("log", self.db.log(agent, level, message))

    def throttled_say(self, agent, message: str, every: float = 600) -> None:
        if time.time() - self._last_said.get(message, 0) > every:
            self._last_said[message] = time.time()
            agent.say(message)

    # ------------------------------------------------------------------ variants
    def _load_variants(self) -> None:
        rows = self.db.query("SELECT * FROM variants WHERE retired=0 ORDER BY created_at")
        if not rows:
            for i, (name, cfg) in enumerate(SEED_VARIANTS.items()):
                self.add_variant(cfg, name=name, champion=(i == 0), note="starting personality")
            return
        for r in rows:
            saved = self.db.get(f"broker:{r['id']}")
            self.variants[r["id"]] = self._make_variant(r["id"], r["name"], StrategyConfig.from_dict(json.loads(r["config_json"])),
                                                        bool(r["is_champion"]), r["created_at"], saved)
        # personalities added in a later version join the experiment with a fresh paper account
        known = {r["name"] for r in self.db.query("SELECT name FROM variants")}
        for name, cfg in SEED_VARIANTS.items():
            if name not in known:
                self.add_variant(cfg, name=name, note="new starting personality")

    def _make_variant(self, id, name, cfg, champion, created, saved=None) -> Variant:
        p = self.settings["paper"]
        start = self.settings["money"]["starting_cash_usd"]
        broker = PaperBroker(start, p["fee_pct"], p["slippage_pct"])
        v = Variant(id, name, cfg, broker, champion, created, start)
        if saved:
            broker.cash = saved["cash"]
            broker.positions = {s: Position(**pos) for s, pos in saved["positions"].items()}
            broker.last_sell = saved.get("last_sell", {})
            v.day_start_equity = saved.get("day_start_equity", 0.0)
            v.day_start_ts = saved.get("day_start_ts", 0.0)
        return v

    def _save_broker(self, v: Variant) -> None:
        b = v.broker
        self.db.set(f"broker:{v.id}", {
            "cash": b.cash, "last_sell": b.last_sell,
            "positions": {s: p.__dict__ for s, p in b.positions.items()},
            "day_start_equity": v.day_start_equity, "day_start_ts": v.day_start_ts,
        })

    def add_variant(self, cfg: StrategyConfig, name: str | None = None, parent_id: str | None = None,
                    champion: bool = False, note: str = "") -> Variant:
        vid = uuid.uuid4().hex[:8]
        if not name:
            base = self.variants[parent_id].name.split(" #")[0] if parent_id in self.variants else "Variant"
            n = self.db.query("SELECT COUNT(*) c FROM variants")[0]["c"] + 1
            name = f"{base} #{n}"
        now = time.time()
        self.db.execute(
            "INSERT INTO variants(id,name,parent_id,config_json,created_at,note,is_champion) VALUES(?,?,?,?,?,?,?)",
            (vid, name, parent_id, json.dumps(cfg.to_dict()), now, note, int(champion)),
        )
        v = self._make_variant(vid, name, cfg, champion, now)
        self.variants[vid] = v
        self._save_broker(v)
        self._board_cache = None
        return v

    def retire_variant(self, vid: str) -> None:
        v = self.variants.get(vid)
        if not v or v.champion:
            return
        self.db.execute("UPDATE variants SET retired=1 WHERE id=?", (vid,))
        del self.variants[vid]
        self._board_cache = None

    def promote(self, vid: str) -> None:
        if vid not in self.variants:
            raise KeyError(vid)
        self.db.execute("UPDATE variants SET is_champion=0")
        self.db.execute("UPDATE variants SET is_champion=1 WHERE id=?", (vid,))
        for v in self.variants.values():
            v.champion = v.id == vid
        self._board_cache = None
        self._log("Optimizer", "info", f"{self.variants[vid].name} is now the champion.")

    def leaderboard(self, max_age: float = 30) -> list[dict]:
        cached = getattr(self, "_board_cache", None)
        if cached and time.time() - cached[0] < max_age:
            return cached[1]
        board = self._leaderboard()
        self._board_cache = (time.time(), board)
        return board

    def _leaderboard(self) -> list[dict]:
        prices = {s: q.price for s, q in self.prices.quotes.items() if q.price}
        out = []
        for v in self.variants.values():
            eq = v.broker.equity(prices)
            ret = (eq / v.start_equity - 1) * 100
            series = [v.start_equity] + [r["equity"] for r in self.db.query(
                "SELECT equity FROM equity WHERE variant_id=? AND mode='paper' ORDER BY ts", (v.id,))] + [eq]
            peak, dd = series[0], 0.0
            for x in series:
                peak = max(peak, x)
                dd = max(dd, (peak - x) / peak * 100 if peak else 0)
            t = self.db.query(
                "SELECT COUNT(*) n, COALESCE(SUM(fee),0) fees, SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) wins, "
                "SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END) sells FROM trades WHERE variant_id=? AND mode='paper'",
                (v.id,))[0]
            out.append({
                "id": v.id, "name": v.name, "champion": v.champion, "equity": round(eq, 2), "return_pct": round(ret, 2),
                "max_drawdown_pct": round(dd, 2), "fitness": round(ret - 0.5 * dd, 2), "trades": t["n"],
                "fees": round(t["fees"], 2), "win_rate": round((t["wins"] or 0) / t["sells"] * 100, 1) if t["sells"] else None,
                "age_h": round((time.time() - v.created) / 3600, 1), "config": v.config.to_dict(),
                "positions": len(v.broker.positions),
            })
        return sorted(out, key=lambda b: -b["fitness"])

    # ------------------------------------------------------------------ trading
    async def execute(self, v: Variant, symbol: str, side: str, amount: float, price: float, reason: str,
                      fraction: float) -> None:
        """amount is USD for BUY and quantity for SELL. fraction is used to mirror the champion on live."""
        b = v.broker
        try:
            fill = b.buy(symbol, amount, price) if side == "BUY" else b.sell(symbol, amount, price)
        except ValueError as e:
            self._log("Risk Officer", "warn", f"Refused {v.name} {side} {symbol}: {e}")
            return
        self.db.execute(
            "INSERT INTO trades(ts,variant_id,mode,symbol,side,qty,price,notional,fee,pnl,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), v.id, "paper", symbol, side, fill.qty, fill.price, fill.notional, fill.fee, fill.pnl, reason),
        )
        self._save_broker(v)
        trade = {"variant": v.name, "champion": v.champion, "mode": "paper", "symbol": symbol, "side": side,
                 "notional": round(fill.notional, 2), "price": fill.price, "pnl": fill.pnl, "reason": reason, "ts": time.time()}
        self.bus.publish("trade", trade)
        if v.champion:
            pnl = f", P&L {fill.pnl:+.2f} USD" if fill.pnl is not None else ""
            self._log("Buyer", "trade", f"{side} {symbol} for {fill.notional:.2f} USD at {fill.price:.6g} ({reason}{pnl})")
            if self.mode == "live" and self.live:
                await self._mirror_live(symbol, side, fraction)

    async def _mirror_live(self, symbol: str, side: str, fraction: float) -> None:
        try:
            if side == "BUY":
                bal = await self.live.balances()
                fiat = bal.get("FIAT", 0.0)
                invested = sum(self.db.get("live_cost", {}).values())
                amount = min(fiat, fraction * (fiat + invested))
                if amount < self.settings["risk"]["min_order_usd"]:
                    return
                res = await self.live.buy(symbol, amount)
                cost = self.db.get("live_cost", {})
                cost[symbol] = cost.get(symbol, 0.0) + amount
                self.db.set("live_cost", cost)
            else:
                res = await self.live.sell_fraction(symbol, fraction)
                cost = self.db.get("live_cost", {})
                cost.pop(symbol, None)
                self.db.set("live_cost", cost)
            if res:
                ex = res.get("execution", {})
                self.db.execute(
                    "INSERT INTO trades(ts,variant_id,mode,symbol,side,qty,price,notional,fee,pnl,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (time.time(), self.champion().id, "live", symbol, side, float(ex.get("quantity", 0) or 0),
                     float(ex.get("price", 0) or 0), float(ex.get("notional", 0) or 0), float(ex.get("fee", 0) or 0), None,
                     "mirror of champion"))
                self._log("Buyer", "live", f"LIVE {side} {symbol}: {ex.get('notional', '?')} {self.live.currency} filled.")
                self.bus.publish("trade", {"mode": "live", "symbol": symbol, "side": side, "ts": time.time()})
            self.live_errors = 0
        except Exception as e:
            self.live_errors += 1
            self._log("Buyer", "error", f"LIVE {side} {symbol} failed: {e}")
            if self.live_errors >= 3:
                self.db.set("mode", "paper")
                self._log("Risk Officer", "error", "Three live errors in a row: switched back to PAPER for safety.")

    async def set_mode(self, mode: str) -> dict:
        if mode == "paper":
            self.db.set("mode", "paper")
            self._log("Risk Officer", "info", "Switched to PAPER trading.")
            return {"ok": True, "mode": "paper"}
        if mode != "live":
            return {"ok": False, "error": "unknown mode"}
        if not self.settings.bitpanda_api_key:
            return {"ok": False, "error": "Add BITPANDA_API_KEY to .env and restart first."}
        try:
            self.live = BitpandaBroker(self.settings.bitpanda_api_key, self.settings["live"]["currency"])
            info = await self.live.connect()
            bal = await self.live.balances()
        except Exception as e:
            self.live = None
            return {"ok": False, "error": f"Bitpanda connection failed: {e}"}
        self.db.set("mode", "live")
        self.live_errors = 0
        self._log("Risk Officer", "live", f"LIVE trading ON. {bal.get('FIAT', 0):.2f} {self.live.currency} available, "
                                          f"{info['assets']} assets. The champion's next trades use real money.")
        return {"ok": True, "mode": "live", "fiat": bal.get("FIAT", 0)}

    def set_kill_switch(self, on: bool) -> None:
        self.db.set("kill_switch", on)
        self._log("Risk Officer", "error" if on else "info",
                  "KILL SWITCH ON: no new buys. Open positions still follow their exits." if on else "Kill switch off.")

    # ------------------------------------------------------------------ loops
    async def run(self) -> None:
        if self.mode == "live":
            r = await self.set_mode("live")
            if not r["ok"]:
                self.db.set("mode", "paper")
        self._log("Engine", "info", "Booting: loading price history" + (" (SIMULATED data)" if self.settings.simulate else ""))
        await self.prices.backfill()
        self._log("Engine", "info", f"Team online. AI {'on' if self.llm.available else 'off (no API key): free math mode'}.")
        e = self.settings["engine"]
        await asyncio.gather(
            self._every(e["price_poll_seconds"], self.prices.poll_crypto),
            self._every(e["stock_poll_seconds"], self.prices.poll_stocks),
            self._every(e["hype_poll_seconds"], self.social.poll_hype),
            self._every(e["news_poll_seconds"], self._poll_news),
            self._every(e["tick_seconds"], self.tick),
            self._every(60, self.snapshot_equity),
            self._every(2, self.publish_prices),
        )

    async def _every(self, seconds: float, fn) -> None:
        while True:
            try:
                await fn()
            except Exception as ex:
                self._log("Engine", "error", f"{getattr(fn, '__name__', fn)} failed: {ex}")
            await asyncio.sleep(seconds)

    async def _poll_news(self) -> None:
        fresh = await self.social.poll_news()
        self.agent("news").queue(fresh)

    async def tick(self) -> None:
        for node in self.sources + self.team:
            await node.step(self.bb)
            self.bus.publish("node", node.node())
        self.bus.publish("state", self.state(light=True))

    async def snapshot_equity(self) -> None:
        prices = {s: q.price for s, q in self.prices.quotes.items() if q.price}
        if not prices:
            return
        now = time.time()
        for v in self.variants.values():
            eq = v.broker.equity(prices)
            if now - v.day_start_ts > 86400:
                v.day_start_ts, v.day_start_equity = now, eq
                self._save_broker(v)
            self.db.execute("INSERT INTO equity(ts,variant_id,mode,equity,cash) VALUES(?,?,?,?,?)",
                            (now, v.id, "paper", eq, v.broker.cash))

    async def publish_prices(self) -> None:
        self.bus.publish("prices", self.ticker())

    def ticker(self) -> list[dict]:
        return [{"symbol": q.symbol, "kind": q.kind, "price": q.price, "change": round(q.change_24h_pct, 2),
                 "spark": [round(c[1], 8) for c in list(q.candles)[-60:]]}
                for q in self.prices.quotes.values() if q.price]

    def state(self, light: bool = False) -> dict:
        prices = {s: q.price for s, q in self.prices.quotes.items() if q.price}
        champ = self.champion()
        s = {
            "mode": self.mode, "kill_switch": self.kill_switch, "simulate": self.settings.simulate,
            "ai": self.llm.available, "budget": self.budget.snapshot(), "regime": self.bb.regime,
            "risk_appetite": self.bb.risk_appetite, "uptime": time.time() - self.started,
            "champion": None if not champ else {"id": champ.id, "name": champ.name, **champ.broker.to_dict(prices),
                                                "start": champ.start_equity},
            "scores": self.bb.scores.get(champ.id, {}) if champ else {},
            "auto_promote": self.db.get("auto_promote", False),
        }
        if not light:
            s["ticker"] = self.ticker()
            s["nodes"] = [n.node() for n in self.sources + self.team]
            s["log"] = self.db.query("SELECT ts,agent,level,message FROM agent_log ORDER BY id DESC LIMIT 150")[::-1]
            s["trades"] = self.recent_trades()
        return s

    def recent_trades(self, limit: int = 50) -> list[dict]:
        return self.db.query(
            "SELECT t.ts,t.mode,t.symbol,t.side,t.notional,t.price,t.pnl,t.reason,v.name variant,v.is_champion champion "
            "FROM trades t JOIN variants v ON v.id=t.variant_id ORDER BY t.id DESC LIMIT ?", (limit,))

    def experiments(self) -> dict:
        board = self.leaderboard(max_age=0)
        curves = {}
        for b in board:
            rows = self.db.query("SELECT ts,equity FROM equity WHERE variant_id=? AND mode='paper' ORDER BY ts", (b["id"],))
            step = max(1, len(rows) // 400)
            curves[b["id"]] = [[r["ts"], round(r["equity"], 3)] for r in rows[::step]]
        retired = self.db.query("SELECT id,name,note,created_at FROM variants WHERE retired=1 ORDER BY created_at DESC LIMIT 20")
        return {"leaderboard": board, "curves": curves, "retired": retired}
