"""The engine: owns the feeds, the agent team, the strategy variants and the paper/live switch."""
from __future__ import annotations

import asyncio
import copy
import json
import math
import time
import uuid
from dataclasses import dataclass

from . import backtest, controls, research
from .agents.base import Blackboard, Source
from .agents.optimizer import Optimizer
from .agents.radar import LiveDesk, MarketRadar
from .agents.team import (Buyer, CryptoAnalyst, HypeDetective, HypeScout, MarketAnalyst, NewsHunter, Predictor,
                          Professor, RiskOfficer)
from .brokers.bitpanda import BitpandaBroker
from .brokers.fusion import FusionBroker
from .brokers.paper import PaperBroker, Position
from .bus import Bus
from .config import Settings
from .data.prices import PriceFeed, market_open, us_market_open
from .data.social import SocialFeed
from .data.universe import Universe
from .db import DB
from .llm import LLM, Budget
from .strategy import SEED_VARIANTS, STRATEGY_FIELDS, StrategyConfig

HISTORY_DAYS = 7
MAX_WATCHLIST = 25


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


def _max_drawdown(series: list[float]) -> float:
    """Largest fall from a peak, in %."""
    peak, dd = (series[0] if series else 0.0), 0.0
    for x in series:
        peak = max(peak, x)
        dd = max(dd, (peak - x) / peak * 100 if peak else 0)
    return dd


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
        # dashboard changes are saved in the database and laid over config.toml
        self._base_raw = copy.deepcopy(settings.raw)
        controls.apply(settings.raw, self.db.get("controls", {}))
        controls.apply(self._base_raw, {})
        src = self.db.get("sources", {})
        m = settings["markets"]
        crypto, stocks = src.get("crypto", m["crypto"]), src.get("stocks", m["stocks"])
        self.stocks_enabled = bool(m.get("stocks_enabled", False))
        if not self.stocks_enabled:
            stocks = []  # crypto-only: Bitpanda can't trade stocks through its API
        # coins the Market Radar added on top of your own list (they come back after a restart)
        self.scanned: list[str] = [c for c in self.db.get("scanned", []) if c not in crypto]
        self.prices = PriceFeed(crypto + self.scanned, stocks, settings.simulate, self._log)
        self.social = SocialFeed(crypto + self.scanned + stocks, settings.simulate, self._log)
        self.universe = Universe(settings.simulate, self._log)
        self.fusion_coins: set[str] | None = None  # what Fusion can trade in our currency, once the key is checked
        self.wallet: dict | None = None            # the real Bitpanda account, refreshed every 30 seconds
        self._viewer = None                        # read-only Fusion connection while in paper mode
        if src.get("subreddits"):
            self.social.subreddits = src["subreddits"]
        if src.get("feeds"):
            self.social.feeds = src["feeds"]
        self._load_candles()
        self._dataset: tuple[float, float, backtest.Dataset] | None = None
        ab = settings["ai_budget"]
        self.budget = Budget(self.db, ab)
        self.llm = LLM(settings.anthropic_api_key, self.budget, self.db, ab["fast_model"], ab["deep_model"])
        self.bb = Blackboard()
        self.variants: dict[str, Variant] = {}
        self.live: FusionBroker | BitpandaBroker | None = None
        self.live_errors = 0
        self._last_said: dict[str, float] = {}
        self.started = time.time()

        h = lambda key, feed: (lambda: feed.healthy.get(key, False))  # noqa: E731
        self.sources = [
            Source(self, "src_kraken", "Kraken prices", "Live crypto prices (free public API)", h("crypto", self.prices),
                   "Kraken's free public API: last price every few seconds and 12 hours of 1-minute history at start. "
                   "The bot also stores every minute itself (7 days) for the backtester."),
            Source(self, "src_yahoo", "Yahoo stocks", "US stock prices, 1-minute bars", h("stock", self.prices),
                   "Yahoo Finance's public chart API, polled every minute. Stocks only trade 9:30-16:00 New York time, "
                   "Monday to Friday."),
            Source(self, "src_reddit", "Reddit", "Hot posts from crypto and stock subreddits", h("reddit", self.social),
                   "Hot posts from the subreddits in Controls > Sources, read through Reddit's public RSS feed."),
            Source(self, "src_coingecko", "CoinGecko trending", "Most searched coins", lambda: bool(self.social.trending),
                   "The coins people search most on CoinGecko right now."),
            Source(self, "src_feargreed", "Fear & Greed", "Crypto market sentiment index",
                   lambda: self.social.fear_greed is not None,
                   "alternative.me's daily Crypto Fear & Greed index, 0 (extreme fear) to 100 (extreme greed)."),
            Source(self, "src_news", "News feeds", "RSS headlines from crypto and finance news", h("news", self.social),
                   "The RSS feeds in Controls > Sources. Add any feed you like."),
            Source(self, "src_fusion", "Bitpanda Fusion", "Your real account: balance, prices, which coins trade",
                   lambda: bool(self.wallet and not self.wallet.get("error")),
                   "Read every 30 seconds with your Fusion key (Read + Trade, never withdrawal): cash, coins, prices "
                   "and the list of coins Fusion can trade, which the Market Radar uses."),
        ]
        if not self.stocks_enabled:
            self.sources = [x for x in self.sources if x.id != "src_yahoo"]
        self.team = [MarketRadar(self), CryptoAnalyst(self), MarketAnalyst(self), HypeScout(self), HypeDetective(self),
                     NewsHunter(self), Professor(self), Predictor(self), RiskOfficer(self), Buyer(self), LiveDesk(self),
                     Optimizer(self)]
        self._by_id = {a.id: a for a in self.sources + self.team}
        # remember news across restarts so headlines aren't re-read (and re-paid for) after every restart
        self.social._seen_links = set(self.db.get("news_seen", []))
        self.bb.news_events = [e for e in self.db.get("news_events", []) if time.time() - e["ts"] < 24 * 3600]
        prof = self.db.get("professor_last")
        if prof and time.time() - prof["detail"].get("ts", 0) < settings["engine"]["professor_every_minutes"] * 90:
            self.bb.risk_appetite, self.bb.avoid = prof["risk_appetite"], set(prof["avoid"])
            self.agent("professor").detail = prof["detail"]
        # test accounts are sized like your real account (set once the Fusion balance is known)
        if self.db.get("paper_start_usd"):
            settings.raw["money"]["starting_cash_usd"] = self.db.get("paper_start_usd")
        self._load_variants()
        self._tune_for_daily_brain()

    def _tune_for_daily_brain(self) -> None:
        """One-time settings update for the live account (2026-10-05): the daily brain may use the whole account
        in a few big positions, and the paper side gets sane limits. Your later changes win; fresh installs skip it."""
        if self.db.get("tuned") == "2026-10-05" or self.db.get("mode") != "live":
            return
        overrides = self.db.get("controls", {})
        overrides.update({
            "live.max_invest": 2000,             # = the whole account: the brain never uses more than the account holds
            "live.max_order": 150,               # one order per coin (a third of the account is about 115 CHF)
            "live.max_spread_pct": 1.0,
            "live.use_my_coins": True,
            "risk.max_position_pct": 35,         # paper strategies: a third each, like the brain
            "risk.max_open_positions": 5,
            "risk.max_daily_loss_pct": 30,
            "engine.professor_every_minutes": 720,  # its advice doesn't move real money now: twice a day is plenty
        })
        controls.apply(self.settings.raw, overrides)
        self.db.set("controls", overrides)
        b = self.brain()
        if b.get("on"):
            b.pop("day", None)  # decide again with the new limits right after this start
            self.db.set("brain", b)
        self.db.set("tuned", "2026-10-05")
        self.db.log("Engine", "info", "Settings tuned for the daily brain: whole account, about a third per coin, "
                                      "orders up to 150 CHF, Professor twice a day. Change anything in Controls.")

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

    # ------------------------------------------------------------------ dashboard settings
    def controls(self) -> dict:
        overrides = self.db.get("controls", {})
        return {"controls": controls.describe(self.settings.raw, overrides), "strategy_fields": STRATEGY_FIELDS}

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
        for v in self.variants.values():  # fee changes apply to every paper account
            v.broker.fee_pct = self.settings["paper"]["fee_pct"]
            v.broker.slippage_pct = self.settings["paper"]["slippage_pct"]
        self._log("Engine", "info", "Settings changed: " + ", ".join(
            f"{controls.BY_KEY[k]['label']} = {'default' if v is None else controls.coerce(k, v)}" for k, v in changes.items()))
        return self.controls()

    def sources_info(self) -> dict:
        held = {s for v in self.variants.values() for s in v.broker.positions}
        return {
            "crypto": self.core_crypto(), "stocks": [q.symbol for q in self.prices.stocks()], "scanned": self.scanned,
            "subreddits": self.social.subreddits, "feeds": self.social.feeds, "held": sorted(held),
            "status": self.social.source_status,
        }

    def core_crypto(self) -> list[str]:
        """Your own coin list, without the radar's additions."""
        return [q.symbol for q in self.prices.crypto() if q.symbol not in self.scanned]

    def held_anywhere(self, symbol: str) -> bool:
        return (any(symbol in v.broker.positions for v in self.variants.values())
                or self.db.get("live_qty", {}).get(symbol, 0) > 0)

    def _forget_symbol(self, symbol: str) -> None:
        self.prices.remove_symbol(symbol)
        for d in (self.bb.tech, self.bb.hype, self.bb.news, self.bb.market, self.bb.signals):
            d.pop(symbol, None)

    async def set_scanned(self, want: list[str], keep: set[str]) -> tuple[list[str], list[str]]:
        """Radar watchlist: add `want`, drop radar coins outside `keep` that nobody holds."""
        dropped = [s for s in self.scanned if s not in want and s not in keep and not self.held_anywhere(s)]
        for s in dropped:
            self.scanned.remove(s)
            self._forget_symbol(s)
        added = []
        for s in want:
            if s in self.prices.quotes:
                continue
            await self.prices.add_symbol(s, "crypto", self._history(s, HISTORY_DAYS * 1440))
            if not self.prices.quotes[s].candles:  # no price history at all: not worth watching
                self.prices.remove_symbol(s)
                continue
            self.scanned.append(s)
            added.append(s)
            if not self.settings.simulate:
                await asyncio.sleep(1.2)  # Kraken's public rate limit
        if added or dropped:
            self.social.symbols = list(self.prices.quotes)
            self.db.set("scanned", self.scanned)
        return added, dropped

    def radar(self) -> dict:
        r = self.agent("radar")
        return {"ts": self.universe.ts, "rows": r.ranked, "watching": self.scanned, "core": self.core_crypto(),
                "fusion": len(self.fusion_coins) if self.fusion_coins else None, "error": r.error,
                "held": sorted({s for v in self.variants.values() for s in v.broker.positions}),
                "live": sorted(self.db.get("live_qty", {}))}

    def _save_sources(self) -> None:
        self.db.set("sources", {k: v for k, v in self.sources_info().items() if k in ("crypto", "stocks", "subreddits", "feeds")})

    async def add_source(self, kind: str, value: str, name: str = "") -> dict:
        value = value.strip()
        if kind in ("crypto", "stocks"):
            sym = value.upper().lstrip("$")
            if not sym.replace(".", "").replace("-", "").isalnum() or len(sym) > 10:
                return {"ok": False, "error": "use a ticker like ARB or PLTR"}
            if sym in self.scanned:  # the radar found it first: now it's on your own list for good
                self.scanned.remove(sym)
                self.db.set("scanned", self.scanned)
                self._save_sources()
                return {"ok": True, **self.sources_info()}
            if sym in self.prices.quotes:
                return {"ok": False, "error": f"{sym} is already on the list"}
            mine = self.core_crypto() if kind == "crypto" else [q.symbol for q in self.prices.stocks()]
            if len(mine) >= MAX_WATCHLIST:
                return {"ok": False, "error": f"at most {MAX_WATCHLIST} per list, to keep the free APIs happy"}
            qkind = "crypto" if kind == "crypto" else "stock"
            why = await self.prices.validate(sym, qkind)
            if why:
                return {"ok": False, "error": why}
            await self.prices.add_symbol(sym, qkind, self._history(sym, HISTORY_DAYS * 1440))
            self.social.symbols = list(self.prices.quotes)
        elif kind == "subreddits":
            sub = value.removeprefix("r/").removeprefix("/r/").strip("/")
            if not sub.replace("_", "").isalnum():
                return {"ok": False, "error": "use a subreddit name like Bitcoin"}
            if sub in self.social.subreddits:
                return {"ok": False, "error": "already on the list"}
            why = await self.social.check_subreddit(sub)
            if why:
                return {"ok": False, "error": why}
            self.social.subreddits.append(sub)
        elif kind == "feeds":
            if not value.startswith(("http://", "https://")):
                return {"ok": False, "error": "paste the full feed address, starting with https://"}
            why = await self.social.check_feed(value)
            if why:
                return {"ok": False, "error": why}
            label = name.strip() or value.split("/")[2].removeprefix("www.")
            self.social.feeds[label] = value
        else:
            return {"ok": False, "error": "unknown list"}
        self._save_sources()
        self._log("Engine", "info", f"Added {value} to {kind}.")
        return {"ok": True, **self.sources_info()}

    def remove_source(self, kind: str, value: str) -> dict:
        if kind in ("crypto", "stocks"):
            held = self.sources_info()["held"]
            if value in held:
                return {"ok": False, "error": f"a strategy still holds {value}; it can be removed once it's sold"}
            if kind == "crypto" and len(self.core_crypto()) <= 1:
                return {"ok": False, "error": "keep at least one coin"}
            self._forget_symbol(value)
            self.social.symbols = list(self.prices.quotes)
        elif kind == "subreddits":
            if value in self.social.subreddits:
                self.social.subreddits.remove(value)
        elif kind == "feeds":
            self.social.feeds.pop(value, None)
        else:
            return {"ok": False, "error": "unknown list"}
        self._save_sources()
        self._log("Engine", "info", f"Removed {value} from {kind}.")
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

    def set_variant_config(self, vid: str, changes: dict, as_new: bool = False, name: str = "") -> Variant:
        v = self.variants.get(vid)
        if not v:
            raise KeyError(vid)
        cfg = StrategyConfig.from_dict({**v.config.to_dict(), **changes}).clamped()
        if as_new:
            return self.add_variant(cfg, name=name.strip()[:40] or None, parent_id=vid, note="tuned by you")
        v.config = cfg
        self.db.execute("UPDATE variants SET config_json=? WHERE id=?", (json.dumps(cfg.to_dict()), vid))
        self._board_cache = None
        self._log("Engine", "info", f"{v.name}'s strategy settings were edited.")
        return v

    # ------------------------------------------------------------------ price history and backtests
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
        trades = self.db.query(
            "SELECT t.ts,t.side,t.price,t.notional,t.pnl,t.reason,v.name variant,v.is_champion champion FROM trades t "
            "JOIN variants v ON v.id=t.variant_id WHERE t.symbol=? AND t.ts>=? AND t.mode='paper' ORDER BY t.ts",
            (symbol, since))
        return {"symbol": symbol, "kind": q.kind, "candles": rows, "trades": trades, "tradable": q.tradable}

    def dataset(self, hours: float) -> backtest.Dataset:
        """Recorded history as a backtest dataset (cached for two minutes per window length)."""
        if self._dataset and self._dataset[1] == hours and time.time() - self._dataset[0] < 120:
            return self._dataset[2]
        self._save_candles()
        since = time.time() - (hours * 60 + backtest.LOOKBACK_MIN + 5) * 60
        series = {q.symbol: self._history(q.symbol, 20000, since) for q in self.prices.quotes.values()}
        for q in self.prices.quotes.values():  # include what's in memory but not saved yet (e.g. fresh backfill)
            have = {int(ts) for ts, _ in series[q.symbol]}
            series[q.symbol] = sorted(series[q.symbol] + [(ts, c) for ts, c in q.candles if ts >= since and int(ts) not in have])
        kinds = {s: q.kind for s, q in self.prices.quotes.items()}
        hourly = {s: list(q.hourly) for s, q in self.prices.quotes.items()}
        ds = backtest.build_dataset(series, kinds, hours, step=1 if hours <= 12 else 2 if hours <= 48 else 4,
                                    hourly=hourly)
        if not ds:
            raise ValueError("not enough price history yet: let the bot run a little longer")
        self._dataset = (time.time(), hours, ds)
        return ds

    async def run_research(self) -> dict:
        """The 2-year test lab: daily candles for the liquid Fusion coins, every strategy vs holding Bitcoin."""
        if self.__dict__.get("_research_busy"):
            return {"busy": True}
        self._research_busy = True
        try:
            cd = await self._daily_candles(force=True)
            res = await asyncio.to_thread(research.run_all, cd)
            res["simulated"] = bool(self.settings.simulate)
            self.db.set("research", res)
            robust = [r for r in res["rows"] if r["robust"]]
            btc = next(r for r in res["rows"] if r["name"] == research.HoldBTC.name)
            self._log("Researcher", "info", f"History test {res['from']} to {res['to']} ({len(res['years'])} years, "
                      f"{res['strategies_tested']} strategies): {len(robust)} pass every robustness check"
                      + (f", best {robust[0]['name']} ({robust[0]['full'].get('cagr_pct')}%/yr, worst drop "
                         f"{robust[0]['full'].get('max_dd_pct')}%)" if robust else "")
                      + f". Holding Bitcoin: {btc['full'].get('cagr_pct')}%/yr, worst drop {btc['full'].get('max_dd_pct')}%.")
            b = self.brain()
            mine = next((r for r in res["rows"] if r["name"] == b.get("strategy")), None)
            if b.get("on") and mine and not mine["robust"]:
                self._log("Researcher", "warn", f"The daily brain's strategy {mine['name']} no longer passes every "
                          f"robustness check on the newest data. Consider switching in the Lab.")
            return res
        finally:
            self._research_busy = False

    def backtest_sync(self, cfg: StrategyConfig, hours: float = 24) -> dict:
        ds = self.dataset(hours)
        res = backtest.run(ds, cfg, self.settings.raw, self.settings["money"]["starting_cash_usd"])
        res["note"] = backtest.overfit_note(res)
        return res

    def autotune_sync(self, base: StrategyConfig, n: int = 40, hours: float = 24) -> list[dict]:
        ds = self.dataset(hours)
        return backtest.autotune(ds, base, self.settings.raw, n=n)

    def buys_since(self, vid: str, ts: float) -> int:
        return self.db.query("SELECT COUNT(*) n FROM trades WHERE variant_id=? AND side='BUY' AND mode='paper' AND ts>=?",
                             (vid, ts))[0]["n"]

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
        if not self.stocks_enabled:  # crypto-only: strategies that can only trade stocks have nothing to do
            for v in [v for v in self.variants.values() if not v.config.trade_crypto]:
                if v.champion:
                    heir = next((x for x in self.variants.values() if self.can_lead(x.config)), None)
                    if not heir:
                        continue
                    self.promote(heir.id)
                self.retire_variant(v.id)
                self._log("Optimizer", "info", f"Retired {v.name}: the bot is crypto-only now (Bitpanda has no stock API).")

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

    @staticmethod
    def can_lead(cfg: StrategyConfig) -> bool:
        """Only a strategy that trades crypto can drive real money (stocks are paper only, the benchmark never trades)."""
        return cfg.trade_crypto and not cfg.hold

    def promote(self, vid: str) -> None:
        if vid not in self.variants:
            raise KeyError(vid)
        if self.mode == "live" and not self.can_lead(self.variants[vid].config):
            raise ValueError(f"{self.variants[vid].name} doesn't trade crypto, so it can't trade your real money.")
        old = self.champion()
        self.db.execute("UPDATE variants SET is_champion=0")
        self.db.execute("UPDATE variants SET is_champion=1 WHERE id=?", (vid,))
        for v in self.variants.values():
            v.champion = v.id == vid
        self._board_cache = None
        self._log("Optimizer", "info", f"{self.variants[vid].name} is now the champion.")
        if old and old.id != vid:
            self._adopt_live_coins(old, self.variants[vid])

    def _adopt_live_coins(self, old: Variant, new: Variant) -> None:
        """The new champion takes over the coins the bot bought for real, instead of selling them at the switch.

        Each coin goes into the new champion's paper account at the same share of its money as it has in the real
        account, at today's price and without a fee (the real coins are already paid for). From then on the new
        champion's own stops, take-profit and sell signals decide when they go, and its sells are copied to Fusion.
        """
        live = self.db.get("live_qty", {})
        if not live:
            return
        prices = {s: q.price for s, q in self.prices.quotes.items() if q.price}
        wallet_coins = {c["symbol"]: c.get("value") or 0 for c in (self.wallet or {}).get("coins", [])}
        wallet_total = (self.wallet or {}).get("total") or 0
        old_eq, new_eq = old.broker.equity(prices), new.broker.equity(prices)
        adopted = []
        for sym in live:
            price = prices.get(sym)
            if not price or sym in new.broker.positions:
                continue
            if sym in old.broker.positions and old_eq > 0:
                share = old.broker.positions[sym].value(price) / old_eq   # what the live copy was sized from
            elif wallet_total > 0 and wallet_coins.get(sym):
                share = wallet_coins[sym] / wallet_total
            else:
                continue  # can't size it: the Live Desk sells it as before
            usd = min(share * new_eq, new.broker.cash)
            if usd < 0.5:
                continue
            new.broker.cash -= usd
            new.broker.positions[sym] = Position(sym, usd / price, price, time.time(), price)
            adopted.append(sym)
        if adopted:
            self._save_broker(new)
            self._log("Live Desk", "info", f"{new.name} takes over the bot's real {', '.join(adopted)} and will sell "
                                           f"by its own rules (no sale at the switch).")

    def leaderboard(self, max_age: float = 30) -> list[dict]:
        cached = getattr(self, "_board_cache", None)
        if cached and time.time() - cached[0] < max_age:
            return cached[1]
        board = self._leaderboard()
        self._board_cache = (time.time(), board)
        return board

    def _leaderboard(self) -> list[dict]:
        prices = {s: q.price for s, q in self.prices.quotes.items() if q.price}
        window_h = float(self.settings["optimizer"]["min_age_to_promote_h"])
        out = []
        for v in self.variants.values():
            eq = v.broker.equity(prices)
            ret = (eq / v.start_equity - 1) * 100
            rows = self.db.query(
                "SELECT ts, equity FROM equity WHERE variant_id=? AND mode='paper' ORDER BY ts", (v.id,))
            series = [v.start_equity] + [r["equity"] for r in rows] + [eq]
            dd = _max_drawdown(series)
            # the same recent window for everyone, so an old champion can't live off gains from long ago
            since = time.time() - window_h * 3600
            recent = [r["equity"] for r in rows if r["ts"] >= since] + [eq]
            if v.created >= since:
                recent = [v.start_equity] + recent
            r_ret = (recent[-1] / recent[0] - 1) * 100 if recent[0] else 0.0
            r_fit = r_ret - 0.5 * _max_drawdown(recent)
            t = self.db.query(
                "SELECT COUNT(*) n, COALESCE(SUM(fee),0) fees, SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) wins, "
                "SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END) sells FROM trades WHERE variant_id=? AND mode='paper'",
                (v.id,))[0]
            out.append({
                "id": v.id, "name": v.name, "champion": v.champion, "equity": round(eq, 2), "return_pct": round(ret, 2),
                "max_drawdown_pct": round(dd, 2), "fitness": round(ret - 0.5 * dd, 2), "trades": t["n"],
                "recent_return_pct": round(r_ret, 2), "recent_fitness": round(r_fit, 2), "window_h": window_h,
                "fees": round(t["fees"], 2), "win_rate": round((t["wins"] or 0) / t["sells"] * 100, 1) if t["sells"] else None,
                "age_h": round((time.time() - v.created) / 3600, 1), "config": v.config.to_dict(),
                "benchmark": v.config.hold,
                "positions": len(v.broker.positions),
            })
        return sorted(out, key=lambda b: -b["fitness"])

    # ------------------------------------------------------------------ trading
    async def execute(self, v: Variant, symbol: str, side: str, amount: float, price: float, reason: str,
                      fraction: float) -> None:
        """amount is USD for BUY and quantity for SELL. fraction is used to mirror the champion on live."""
        b = v.broker
        q = self.prices.quotes.get(symbol)
        is_stock = bool(q and q.kind == "stock")
        paper = self.settings["paper"]
        fee = paper["stock_fee_pct"] if is_stock else paper["fee_pct"]
        min_fee = paper["stock_min_fee_usd"] if is_stock else 0.0
        try:
            fill = (b.buy(symbol, amount, price, fee_pct=fee, min_fee=min_fee) if side == "BUY"
                    else b.sell(symbol, amount, price, fee_pct=fee, min_fee=min_fee))
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
            if self.mode == "live" and self.live and not self.brain_on():
                if is_stock:
                    self.throttled_say(self.agent("buyer"), "Stock trades stay on paper: live trading is crypto-only for now.", 3600)
                else:
                    await self._mirror_live(symbol, side, fraction)

    def _live_held(self, bal: dict, symbol: str) -> float:
        ids = getattr(self.live, "asset_ids", {})  # the app broker keys balances by asset id
        return float(bal.get(symbol.upper(), bal.get(ids.get(symbol.upper(), "?"), 0.0)) or 0.0)

    async def _mirror_live(self, symbol: str, side: str, fraction: float, reason: str = "mirror of champion") -> None:
        try:
            if side == "BUY":
                lv = self.settings["live"]
                pairs = getattr(self.live, "pairs", None)
                if pairs is not None and symbol not in pairs:
                    self.throttled_say(self.agent("livedesk"), f"{symbol} isn't tradable on {self.live.name}: paper only.", 3600)
                    return
                bal = await self.live.balances()
                fiat = bal.get("FIAT", 0.0)
                invested = sum(self.db.get("live_cost", {}).values())
                spare = await self._spare_coins(bal) if lv.get("use_my_coins") else {}
                # same share of the live account as the champion used, inside your caps
                base = min(fiat + invested + sum(v for _, v in spare.values()), lv["max_invest"])
                amount = min(fiat + sum(v for _, v in spare.values()), fraction * base, lv["max_order"],
                             lv["max_invest"] - invested)
                if amount < self.settings["risk"]["min_order_usd"]:
                    self.throttled_say(self.agent("livedesk"), f"Skipped live BUY {symbol}: "
                                       + ("live cap reached." if lv["max_invest"] - invested < 1 else "order would be too small."), 1800)
                    return
                min_amt = float(((pairs or {}).get(symbol) or {}).get("minOrderAmount") or 0)
                if amount < min_amt:
                    # Fusion won't take smaller orders: round up to its minimum when your caps and money allow it,
                    # otherwise a small account could never trade at all
                    room = min(lv["max_order"], lv["max_invest"] - invested, fiat + sum(v for _, v in spare.values()))
                    need = round(min_amt * 1.02, 2)
                    if lv["max_order"] >= min_amt:
                        need = min(need, lv["max_order"])  # exactly the minimum still counts
                    if need > room:
                        why = (f"raise 'Biggest single live order' to at least {need:.0f} {self.live.currency}"
                               if lv["max_order"] < need else "your cap or your money doesn't leave room for it")
                        self.throttled_say(self.agent("livedesk"), f"Skipped live BUY {symbol}: {amount:.2f} is below "
                                           f"Fusion's minimum of {min_amt:g} {self.live.currency}, and {why}.", 1800)
                        return
                    self.agent("livedesk").say(f"Rounded the live BUY of {symbol} up from {amount:.2f} to {need:.2f} "
                                               f"{self.live.currency}: Fusion's minimum order is {min_amt:g}.")
                    amount = need
                if hasattr(self.live, "spread_pct"):
                    spread = await self.live.spread_pct(symbol)
                    if spread > lv["max_spread_pct"]:
                        self.agent("livedesk").say(f"Skipped live BUY {symbol}: spread {spread:.2f}% is above your "
                                                   f"{lv['max_spread_pct']}% limit.", "warn")
                        return
                if amount > fiat:  # not enough cash: sell some of your other coins first (you allowed it)
                    fiat = await self._raise_cash(amount - fiat, spare, pairs or {})
                    amount = min(amount, fiat)
                    if amount < max(min_amt, self.settings["risk"]["min_order_usd"]):
                        return
                    bal = await self.live.balances()
                before = self._live_held(bal, symbol)
                res = await self.live.buy(symbol, amount)
                got = float((res or {}).get("execution", {}).get("quantity", 0) or 0)
                if got <= 0:  # venue didn't report the fill size: measure it from the balance
                    got = max(0.0, self._live_held(await self.live.balances(), symbol) - before)
                cost = self.db.get("live_cost", {})
                cost[symbol] = cost.get(symbol, 0.0) + amount
                self.db.set("live_cost", cost)
                owned = self.db.get("live_qty", {})
                owned[symbol] = owned.get(symbol, 0.0) + got
                self.db.set("live_qty", owned)
            else:
                # Only ever sell what the bot bought itself: coins you already owned stay untouched.
                owned = self.db.get("live_qty", {})
                mine = owned.get(symbol, 0.0)
                if mine <= 0:
                    return
                res = await self.live.sell_fraction(symbol, fraction, owned=mine)
                sold = float((res or {}).get("execution", {}).get("quantity", 0) or 0)
                left = mine - sold if fraction < 0.999 else 0.0
                cost = self.db.get("live_cost", {})
                if left <= 1e-12:
                    owned.pop(symbol, None)
                    cost.pop(symbol, None)
                else:
                    owned[symbol] = left
                    cost[symbol] = cost.get(symbol, 0.0) * left / mine
                self.db.set("live_qty", owned)
                self.db.set("live_cost", cost)
            if res:
                ex = res.get("execution", {})
                self.db.execute(
                    "INSERT INTO trades(ts,variant_id,mode,symbol,side,qty,price,notional,fee,pnl,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (time.time(), self.champion().id, "live", symbol, side, float(ex.get("quantity", 0) or 0),
                     float(ex.get("price", 0) or 0), float(ex.get("notional", 0) or 0), float(ex.get("fee", 0) or 0), None,
                     reason))
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
        self._log("Risk Officer", "live", f"LIVE trading ON via {self.live.name}. {bal.get('FIAT', 0):.2f} {self.live.currency} available, "
                                          f"{info['assets']} assets. The champion's next trades use real money.")
        return {"ok": True, "mode": "live", "fiat": bal.get("FIAT", 0)}

    # ------------------------------------------------------------------ "buy now" button
    async def force_buy(self, amount: float) -> dict:
        """You asked for a real buy of `amount`. The bot picks the best coin by the champion's score right now.
        If nothing is sensible yet (every score negative, spreads too wide), it keeps trying every minute for an hour
        and then tells you clearly why it didn't buy."""
        if self.mode != "live" or not self.live:
            raise ValueError("Switch to LIVE first: the buy-now button places a real order.")
        if amount < 1:
            raise ValueError("Amount must be at least 1.")
        if self.brain_on():
            raise ValueError("The daily brain manages your real money now; a coin bought by hand would be sold at its "
                             "next daily decision. Switch the brain off in the Lab to use buy-now.")
        self.db.set("force_buy", {"amount": float(amount), "ts": time.time(), "until": time.time() + 3600, "why": ""})
        self.agent("livedesk").say(f"You asked for a real buy of {amount:.2f} {self.live.currency}: "
                                   f"looking for the best coin now (up to one hour).", "live")
        return await self._try_force_buy()

    def cancel_force_buy(self) -> None:
        if self.db.get("force_buy"):
            self.db.set("force_buy", None)
            self.agent("livedesk").say("Buy-now request cancelled.")

    async def _force_pick(self) -> tuple[str | None, str]:
        champ = self.champion()
        scores = self.bb.scores.get(champ.id, {}) if champ else {}
        pairs = getattr(self.live, "pairs", None) or {}
        quotes = self.prices.quotes
        ranked = [(s, x) for s, x in sorted(scores.items(), key=lambda kv: -kv[1])
                  if quotes.get(s) and quotes[s].kind == "crypto" and (not pairs or s in pairs)]
        if not ranked:
            return None, "no coin has a score yet (prices still loading)"
        ok = [(s, x) for s, x in ranked if x > 0 and s not in self.bb.avoid]
        if not ok:
            best = ranked[0]
            return None, (f"every coin scores below zero for {champ.name} right now (best: {best[0]} {best[1]:+.2f}), "
                          f"so buying would go against all signals")
        wide = []
        for sym, _ in ok[:6]:
            spread = await self.live.spread_pct(sym) if hasattr(self.live, "spread_pct") else 0.0
            if spread <= self.settings["live"]["max_spread_pct"]:
                return sym, ""
            wide.append(f"{sym} {spread:.2f}%")
        return None, f"the best coins have spreads above your {self.settings['live']['max_spread_pct']}% limit: {', '.join(wide)}"

    async def _try_force_buy(self) -> dict:
        req = self.db.get("force_buy")
        if not req or self.mode != "live" or not self.live:
            return {"ok": False, "why": "nothing pending"}
        lv = self.agent("livedesk")
        sym, why = await self._force_pick()
        if not sym:
            if time.time() >= req["until"]:
                self.db.set("force_buy", None)
                lv.say(f"Buy-now: after one hour I did NOT buy, because {why}. That would not have been wise. "
                       f"Try again later, or lower the champion's buy line in Controls.", "warn")
                return {"ok": False, "why": why, "gave_up": True}
            if why != req.get("why"):
                lv.say(f"Buy-now: waiting, because {why}. I'll keep checking every minute until "
                       f"{time.strftime('%H:%M', time.localtime(req['until']))}.", "warn")
                self.db.set("force_buy", {**req, "why": why})
            return {"ok": False, "waiting": True, "why": why}
        self.db.set("force_buy", None)
        try:
            return await self._buy_now(sym, req["amount"])
        except Exception as ex:
            lv.say(f"Buy-now of {sym} failed: {ex}", "error")
            return {"ok": False, "why": str(ex)}

    async def _live_buy(self, sym: str, want: float, reason: str) -> tuple[float, float]:
        """One real buy of `want` in account currency, inside your caps, raising cash from your coins if allowed.
        Records it as the bot's coin. Returns (amount spent, quantity received)."""
        lv, cfg = self.agent("livedesk"), self.settings["live"]
        cur = self.live.currency
        invested = sum(self.db.get("live_cost", {}).values())
        amount = min(want, cfg["max_invest"] - invested)
        if amount < want - 0.01:
            lv.say(f"{reason}: lowered {want:.2f} to {amount:.2f} {cur} for {sym} to stay inside your cap of {cfg['max_invest']:.0f} "
                   f"{cur} in bot trades (raise it in Controls, Live money).", "warn")
        pairs = getattr(self.live, "pairs", None) or {}
        min_amt = float((pairs.get(sym) or {}).get("minOrderAmount") or 0)
        if amount < max(min_amt, 1):
            raise ValueError(f"{amount:.2f} {cur} is below the minimum order ({max(min_amt, 1):g} {cur}) or your cap is full")
        bal = await self.live.balances()
        fiat = bal.get("FIAT", 0.0)
        if amount > fiat:
            if not cfg.get("use_my_coins"):
                raise ValueError(f"only {fiat:.2f} {cur} cash, and the bot may not sell your coins (Controls, Live money)")
            fiat = await self._raise_cash(amount - fiat, await self._spare_coins(bal), pairs)
            amount = min(amount, fiat)
            if amount < max(min_amt, 1):  # selling your coins brought less than Fusion's minimum: don't send a doomed order
                raise ValueError(f"only {fiat:.2f} {cur} cash after selling your coins, below the {max(min_amt, 1):g} {cur} minimum")
            bal = await self.live.balances()
        before = self._live_held(bal, sym)
        res = await self.live.buy(sym, amount)
        ex = (res or {}).get("execution", {}) or {}
        got = float(ex.get("quantity", 0) or 0) or max(0.0, self._live_held(await self.live.balances(), sym) - before)
        cost = self.db.get("live_cost", {})
        cost[sym] = cost.get(sym, 0.0) + amount
        self.db.set("live_cost", cost)
        owned = self.db.get("live_qty", {})
        owned[sym] = owned.get(sym, 0.0) + got
        self.db.set("live_qty", owned)
        champ = self.champion()
        self.db.execute(
            "INSERT INTO trades(ts,variant_id,mode,symbol,side,qty,price,notional,fee,pnl,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), champ.id if champ else "", "live", sym, "BUY", got, float(ex.get("price", 0) or 0), amount,
             float(ex.get("fee", 0) or 0), None, reason))
        self.bus.publish("trade", {"mode": "live", "symbol": sym, "side": "BUY", "ts": time.time()})
        return amount, got

    async def _buy_now(self, sym: str, want: float) -> dict:
        amount, got = await self._live_buy(sym, want, "your buy-now button")
        cur, champ = self.live.currency, self.champion()
        self._log("Buyer", "live", f"LIVE BUY {sym}: {amount:.2f} {cur} filled (your buy-now button, best score of "
                                   f"{champ.name}). {champ.name} now manages it with its own stops and sell rules.")
        self._adopt_one(champ, sym, amount)
        return {"ok": True, "symbol": sym, "amount": round(amount, 2), "qty": got, "currency": cur}

    def _adopt_one(self, champ: Variant, sym: str, amount: float) -> None:
        """Put a real buy into the champion's paper account at the same share of its money, so its exits manage it."""
        prices = {s: q.price for s, q in self.prices.quotes.items() if q.price}
        total = (self.wallet or {}).get("total") or 0
        price = prices.get(sym)
        if not price or total <= 0 or sym in champ.broker.positions:
            return
        usd = min(amount / total * champ.broker.equity(prices), champ.broker.cash)
        if usd < 0.5:
            return
        champ.broker.cash -= usd
        champ.broker.positions[sym] = Position(sym, usd / price, price, time.time(), price)
        self._save_broker(champ)

    # ------------------------------------------------------------------ the daily brain (v2)
    def brain(self) -> dict:
        return self.db.get("brain", None) or {"on": False, "strategy": None}

    def brain_on(self) -> bool:
        return bool(self.brain().get("on"))

    async def set_brain(self, on: bool, strategy: str | None = None) -> dict:
        """Hand the real money to one strategy from the 2-year test, decided once a day on daily candles."""
        b = self.brain()
        if strategy:
            if not research.by_name(strategy):
                raise ValueError(f"unknown strategy: {strategy}")
            b["strategy"] = strategy
        if on and not b.get("strategy"):
            raise ValueError("pick a strategy from the 2-year test first")
        b["on"] = bool(on)
        b.pop("day", None)  # decide again right away
        self.db.set("brain", b)
        if on:
            self.cancel_force_buy()
            self._log("Brain", "live", f"Daily brain ON: {b['strategy']} now manages the bot's real coins. It decides "
                                       f"once a day on daily candles, exactly like in the history test.")
            asyncio.create_task(self.brain_tick())  # first decision now, in the background (history may take a minute)
        else:
            self._log("Brain", "info", "Daily brain OFF: the champion strategy steers the real money again.")
        return self.brain()

    async def _daily_candles(self, force: bool = False) -> "research.Candles":
        """All daily history for the test universe (cached in the database), updated once a new day has closed."""
        async with self.__dict__.setdefault("_hist_lock", asyncio.Lock()):  # the brain and the Researcher share it
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
        self._daily = research.Candles.from_rows(rows)
        return self._daily

    async def research_tick(self) -> None:
        """The Researcher: once a day, after the daily candle closes, re-runs every strategy on all history."""
        last = (self.db.get("research") or {}).get("ts", 0)
        if time.time() // 86400 > last // 86400 and time.time() % 86400 > 900:
            await self.run_research()

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
            note = await self._brain_rebalance(target, strat.name)
            b = {**self.brain(), "day": cd.days[-1], "ts": time.time(), "target": target, "note": note,
                 "regime_days": regime, "btc_ok": research.btc_uptrend(cd, len(cd.days) - 1, regime) if regime else None,
                 "explain": strat.explain}
            self.db.set("brain", b)
        except Exception as ex:
            self._log("Brain", "error", f"Daily decision failed: {str(ex) or type(ex).__name__}. Retrying in 5 minutes.")
        finally:
            self._brain_busy = False
            self._brain_target = {}

    async def _brain_rebalance(self, target: dict[str, float], name: str) -> str:
        """Sell the bot's coins the strategy no longer wants, then buy the ones it wants up to its share of your cap."""
        cfg, cur = self.settings["live"], self.live.currency
        pairs = getattr(self.live, "pairs", None) or {}
        prices = await self.live.prices() if hasattr(self.live, "prices") else {}
        owned = self.db.get("live_qty", {})
        total = (self.wallet or {}).get("total")
        if not total:
            raise ValueError("your Bitpanda balance hasn't been read yet")
        budget = min(cfg["max_invest"], total * 0.98)  # 2% left for fees when the whole account is in play
        self._brain_target = dict(target)
        done = []
        bal = await self.live.balances()
        for sym in [s for s in owned if s not in target]:
            value = min(owned[sym], bal.get(sym, 0.0)) * prices.get(sym, 0.0)
            min_amt = float((pairs.get(sym) or {}).get("minOrderAmount") or 0)
            if 0 < value < min_amt:  # (0 = you sold it yourself: the sell below finds nothing and forgets it)
                # Fusion rejects orders under its minimum, sells too: retrying every day would only log errors
                done.append(f"{sym} ({value:.2f} {cur}) is below Fusion's {min_amt:g} {cur} minimum and can't be sold "
                            f"by the bot: sell it in the Bitpanda app, the bot then forgets it")
                continue
            await self._mirror_live(sym, "SELL", 1.0, reason=f"daily brain: {name} no longer holds it")
            done.append(f"sold {sym}")
        owned = self.db.get("live_qty", {})
        for sym, w in sorted(target.items(), key=lambda kv: -kv[1]):
            if pairs and sym not in pairs:
                done.append(f"{sym} isn't on Fusion")
                continue
            want = w * budget - owned.get(sym, 0.0) * prices.get(sym, 0.0)
            if want < 0.25 * w * budget:
                continue  # already about right: no trades for small drift
            min_amt = max(float((pairs.get(sym) or {}).get("minOrderAmount") or 0), self.settings["risk"]["min_order_usd"])
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
                except ValueError as ex:
                    done.append(f"{sym} not bought: {ex}")
                    break
                self._log("Buyer", "live", f"LIVE BUY {sym}: {amount:.2f} {cur} filled (daily brain: {name}).")
                spent += amount
                if amount < chunk - 0.01:
                    break  # cap or cash ran out
            if spent:
                done.append(f"bought {sym} for {spent:.2f} {cur}")
        holds = ", ".join(sorted(target)) or "nothing (cash)"
        note = f"holds {holds}" + (f"; {'; '.join(done)}" if done else "; no trades needed")
        self._log("Brain", "live", f"Daily decision ({name}): {note}.")
        return note

    def blockers(self) -> dict:
        """Why the champion isn't buying right now, counted by reason: shows whether rules block each other."""
        out: dict[str, list[str]] = {}
        for sym, why in (self.agent("buyer").detail.get("why_not") or {}).items():
            out.setdefault(why, []).append(sym)
        return dict(sorted(out.items(), key=lambda kv: -len(kv[1])))

    async def _spare_coins(self, bal: dict) -> dict[str, tuple[float, float]]:
        """Your coins the bot may sell for cash: tradable, not held by the champion, not bought by the bot.
        symbol -> (quantity, value in account currency)."""
        if not hasattr(self.live, "prices"):
            return {}
        prices = await self.live.prices()
        champ = self.champion()
        mine = self.db.get("live_qty", {})
        pairs = getattr(self.live, "pairs", {}) or {}
        out = {}
        for sym, qty in bal.items():
            if sym in ("FIAT", self.live.currency) or sym in mine or sym not in pairs or not qty:
                continue
            if champ and sym in champ.broker.positions and not self.brain_on():
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
            frac = min(1.0, need * 1.01 / value)
            if value * frac < float((pairs.get(sym) or {}).get("minOrderAmount") or 1):
                frac = min(1.0, float((pairs.get(sym) or {}).get("minOrderAmount") or 1) * 1.05 / value)
            res = await self.live.sell_fraction(sym, frac)
            got = float(((res or {}).get("execution") or {}).get("notional", 0) or 0) - \
                float(((res or {}).get("execution") or {}).get("fee", 0) or 0)
            self.agent("livedesk").say(f"Sold {frac * 100:.0f}% of your {sym} for {got:.2f} {self.live.currency} "
                                       f"to fund a buy (you allowed the bot to use your coins).", "live")
            need -= got
        return (await self.live.balances()).get("FIAT", 0.0)

    async def _reconcile_live(self) -> None:
        """Keep your real account in line with the champion, once a minute:
        sell live coins the champion doesn't hold any more, and copy positions the champion holds on paper but
        your account doesn't have yet (bought before LIVE was on, while the Mac was offline, or by a new champion).
        Without the second part a champion with a full paper portfolio would never buy anything for real."""
        if self.mode != "live" or not self.live or self.brain_on():
            return
        champ = self.champion()
        if not champ:
            return
        if self.settings["live"]["sell_orphans"]:
            for sym in list(self.db.get("live_qty", {})):
                if sym not in champ.broker.positions:
                    self.agent("livedesk").say(f"{champ.name} doesn't hold {sym}: selling the bot's live {sym}.")
                    await self._mirror_live(sym, "SELL", 1.0)
        prices = {s: q.price for s, q in self.prices.quotes.items() if q.price}
        equity = champ.broker.equity(prices)
        tried = self.__dict__.setdefault("_synced", set())
        live = self.db.get("live_qty", {})
        scores = self.bb.scores.get(champ.id, {})
        for sym, pos in list(champ.broker.positions.items()):
            q = self.prices.quotes.get(sym)
            if sym in live or (champ.id, sym) in tried or not q or q.kind != "crypto" or not q.price or equity <= 0:
                continue
            tried.add((champ.id, sym))  # one attempt per champion and coin, so a skipped copy doesn't repeat every minute
            if scores.get(sym, 0.0) < champ.config.exit_score or sym in self.bb.avoid:
                continue  # the champion is about to sell it anyway (weak score or on the Professor's avoid list)
            self.agent("livedesk").say(f"{champ.name} holds {sym} on paper but your account doesn't: copying it now.", "live")
            await self._mirror_live(sym, "BUY", pos.value(q.price) / equity)
            if sym in self.db.get("live_qty", {}):
                # The real coin was bought just now, so the minimum hold counts from here, not from the paper buy.
                pos.opened = time.time()

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
        coins = []
        for sym, q in qty.items():
            px = prices.get(sym, 0.0)
            coins.append({"symbol": sym, "qty": q, "cost": round(cost.get(sym, 0.0), 2), "price": px,
                          "value": round(q * px, 2), "pnl": round(q * px - cost.get(sym, 0.0), 2) if px else None})
        cur = b.currency
        yours = {}
        for k, v in bal.items():
            left = (v or 0) - qty.get(k, 0.0)  # your part: whatever the bot didn't buy
            if k not in ("FIAT", cur) and left > 1e-12:
                yours[k] = left
        own = [{"symbol": k, "qty": round(v, 8), "price": prices.get(k), "value": round(v * prices.get(k, 0), 2),
                "tradable": k in (getattr(b, "pairs", None) or {})} for k, v in yours.items()]
        own.sort(key=lambda c: -c["value"])
        bot_value = sum(c["value"] for c in coins)
        yours_value = sum(c["value"] for c in own)
        fiat = bal.get("FIAT", 0.0)
        self.wallet = {
            "venue": b.name, "currency": cur, "fiat": round(fiat, 2), "coins": coins,
            "bot_value": round(bot_value, 2), "bot_cost": round(sum(cost.values()), 2),
            "bot_pnl": round(bot_value - sum(cost.get(c["symbol"], 0) for c in coins if c["price"]), 2),
            "yours": {c["symbol"]: c["qty"] for c in own}, "own_coins": own, "yours_value": round(yours_value, 2),
            "total": round(fiat + bot_value + yours_value, 2), "use_my_coins": self.settings["live"]["use_my_coins"],
            "max_invest": self.settings["live"]["max_invest"], "ts": time.time(),
        }
        total = self.wallet["total"]
        # every coin in the account, one list: what you hold is what you hold
        allc = {c["symbol"]: c for c in own}
        for c in coins:
            a = allc.setdefault(c["symbol"], {"symbol": c["symbol"], "qty": 0.0, "price": c["price"], "value": 0.0,
                                              "tradable": True})
            a["qty"] = round(a["qty"] + c["qty"], 8)
            a["value"] = round(a["value"] + c["value"], 2)
            a["bot"] = True
        self.wallet["all_coins"] = sorted(allc.values(), key=lambda c: -c["value"])
        self.wallet["coins_value"] = round(total - fiat, 2)
        held = {k: float(v or 0) for k, v in bal.items() if k not in ("FIAT", cur) and (v or 0) > 1e-12}
        self.wallet.update(self._track_account(total, fiat, held, prices))
        if not self.db.get("paper_rebased") and total > 5:
            try:
                usd = total * await self.prices._usd_rate(cur)
                self._rebase_paper(round(usd, 2))
                self.db.set("paper_rebased", {"ts": time.time(), "total": total, "currency": cur})
            except Exception as ex:
                self._log("Engine", "warn", f"Couldn't size test accounts to your balance yet: {ex}")

    def _rebase_paper(self, new_start: float) -> None:
        """Resize every test account so it starts like your real account. Everything scales by the same factor,
        so returns, rankings and positions stay the same, only the amounts match your real money."""
        old = self.settings["money"]["starting_cash_usd"]
        k = new_start / old if old else 1.0
        for v in self.variants.values():
            b = v.broker
            b.cash *= k
            for pos in b.positions.values():
                pos.qty *= k
            v.start_equity = new_start
            v.day_start_equity *= k
            self._save_broker(v)
        self.db.execute("UPDATE equity SET equity=equity*?, cash=cash*? WHERE mode='paper'", (k, k))
        self.db.execute("UPDATE trades SET qty=qty*?, notional=notional*?, fee=fee*?, pnl=pnl*? WHERE mode='paper'",
                        (k, k, k, k))
        self.settings.raw["money"]["starting_cash_usd"] = new_start
        self.db.set("paper_start_usd", new_start)
        self._board_cache = None
        self._log("Engine", "info", f"Test strategies now run at your real account size: {new_start:.2f} USD each "
                                    f"instead of {old:.2f}.")

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
        return {"bot_edge": edge, "bot_edge_since": hs.get("ts"),
                "bot_edge_pct": round(edge / (total - edge) * 100, 2) if edge is not None and total - edge else None,
                "start_total": round(start["total"], 2), "start_ts": start["ts"],
                "change": round(total - start["total"], 2),
                "change_pct": round((total / start["total"] - 1) * 100, 2) if start["total"] else 0.0,
                "change_24h": round(total - day, 2), "history": hist[::step] + ([hist[-1]] if hist and len(hist) % step else [])}

    async def _scan(self) -> None:
        await self.agent("radar").scan()

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
        try:
            if not self.settings.simulate:
                await self.universe._load_pairs()  # exact Kraken pair names for radar coins
        except Exception as ex:
            self._log("Data", "warn", f"Kraken pair list failed: {ex}")
        await self._poll_wallet()
        await self.prices.backfill()
        self._log("Engine", "info", f"Team online. AI {'on' if self.llm.available else 'off (no API key): free math mode'}.")
        e = self.settings["engine"]  # read on every loop, so dashboard changes apply without a restart
        await asyncio.gather(
            self._every(lambda: e["price_poll_seconds"], self.prices.poll_crypto),
            self._every(lambda: e["stock_poll_seconds"], self.prices.poll_stocks),
            self._every(lambda: 4 * 3600, self._poll_hourly),
            self._every(lambda: e["hype_poll_seconds"], self.social.poll_hype),
            self._every(lambda: e["news_poll_seconds"], self._poll_news),
            self._every(lambda: e["tick_seconds"], self.tick),
            self._every(lambda: 60, self.snapshot_equity),
            self._every(lambda: 2, self.publish_prices),
            self._every(lambda: self.settings["scanner"]["every_minutes"] * 60, self._scan),
            self._every(lambda: 30, self._poll_wallet),
            self._every(lambda: 60, self._reconcile_live),
            self._every(lambda: 300, self.brain_tick),
            self._every(lambda: 1800, self.research_tick),
            self._every(lambda: 60, self._try_force_buy),
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

    async def _poll_hourly(self) -> None:
        if time.time() - self.started > 600:  # the boot backfill already loaded it
            await self.prices.poll_hourly()

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

    async def snapshot_equity(self) -> None:
        prices = {s: q.price for s, q in self.prices.quotes.items() if q.price}
        if not prices:
            return
        self._save_candles()
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
            "signals": {sym: {k: round(x, 3) for k, x in sig.items()} for sym, sig in self.bb.signals.items()},
            "weights": champ.config.weights() if champ else {},
            "champion_config": champ.config.to_dict() if champ else {},
            "why_not": self.agent("buyer").detail.get("why_not", {}),
            "avoid": sorted(self.bb.avoid),
            "us_market_open": us_market_open(),
            "markets_open": self.markets_open(),
            "stats": self.champion_stats(champ),
            "news": [{k: e.get(k) for k in ("ts", "source", "title", "link", "symbols", "sentiment", "impact", "event", "ai")}
                     for e in self.bb.news_events[:25]],
            "hype": {"posts": self.social.posts[:10], "trending": self.social.trending[:7],
                     "mentions": self.social.mention_counts, "scores": {k: round(v, 2) for k, v in self.bb.hype.items()}},
            "professor": self.agent("professor").detail,
            "next_professor": getattr(self.agent("professor"), "next_due", 0),
            "variants": len(self.variants),
            "wallet": self.wallet,
            "radar": {"scanned": len(self.agent("radar").ranked), "watching": self.scanned,
                      "fusion": len(self.fusion_coins) if self.fusion_coins else None,
                      "hot": [{k: r[k] for k in ("symbol", "heat", "change", "volume_usd")}
                              for r in self.agent("radar").ranked if r["heat"] is not None][:12]},
            "stocks_enabled": self.stocks_enabled,
            "live_trades": self.db.query("SELECT COUNT(*) n, MAX(ts) last FROM trades WHERE mode='live'")[0],
            "force_buy": self.db.get("force_buy"),
            "brain": self.brain(),
            "blockers": self.blockers(),
            "live_caps": {k: self.settings["live"][k] for k in ("max_invest", "max_order", "max_spread_pct", "use_my_coins")},
        }
        if not light:
            s["ticker"] = self.ticker()
            s["nodes"] = [n.node() for n in self.sources + self.team]
            s["log"] = self.db.query("SELECT ts,agent,level,message FROM agent_log ORDER BY id DESC LIMIT 150")[::-1]
            s["trades"] = self.recent_trades()
        return s

    def markets_open(self) -> dict:
        """Open/closed per stock exchange on the watchlist, plus crypto."""
        out = {"Crypto": True}
        for q in self.prices.stocks():
            out[q.exchange] = market_open(q.symbol)
        return out

    def champion_stats(self, champ) -> dict:
        if not champ:
            return {}
        t = self.db.query(
            "SELECT COUNT(*) n, COALESCE(SUM(fee),0) fees, COALESCE(SUM(pnl),0) pnl, "
            "SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) wins, SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END) sells, "
            "SUM(CASE WHEN ts>? THEN 1 ELSE 0 END) today FROM trades WHERE variant_id=? AND mode='paper'",
            (time.time() - 86400, champ.id))[0]
        prices = {s: q.price for s, q in self.prices.quotes.items() if q.price}
        invested = sum(p.value(prices.get(s, p.avg_price)) for s, p in champ.broker.positions.items())
        return {"trades": t["n"], "trades_24h": t["today"] or 0, "fees": round(t["fees"], 2),
                "realized": round(t["pnl"], 2), "win_rate": round((t["wins"] or 0) / t["sells"] * 100, 1) if t["sells"] else None,
                "invested": round(invested, 2), "buys_1h": self.buys_since(champ.id, time.time() - 3600)}

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
