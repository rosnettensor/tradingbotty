"""Market Radar (picks which coins to watch) and Live Desk (looks after the real-money account)."""
from __future__ import annotations

import time

from ..data.universe import rank
from .base import Agent, Blackboard


class MarketRadar(Agent):
    id = "radar"
    name = "Market Radar"
    role = "Scans every coin on the market and puts the hottest on the watchlist"
    inputs = ["src_kraken", "src_fusion", "src_coingecko"]
    explain = ("Pure math, free. Every few minutes it reads the 24h stats of every coin with a USD market on Kraken "
               "in one request (a few hundred coins), keeps the ones Bitpanda Fusion can trade, and drops coins that "
               "trade too little, have a wide spread or already pumped more than 60%. The rest get a heat score: 24h "
               "momentum, how close the price is to its 24h high, how much it trades, plus a bonus for CoinGecko "
               "trending and Reddit buzz. The hottest join the watchlist so every strategy can trade them; cold ones "
               "leave again unless a strategy holds them. Your own coin list always stays.")
    outputs = "the watchlist of hot coins, and the radar map in the dashboard"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.ranked: list[dict] = []
        self.error = ""

    async def scan(self) -> None:
        """Runs in its own loop (not the tick), because adding a coin loads its price history."""
        e = self.ctx
        cfg = e.settings["scanner"]
        if not cfg["on"]:
            await e.set_scanned([], keep=set())
            self.ranked = []
            return
        try:
            rows = await e.universe.scan()
            self.error = ""
        except Exception as ex:
            self.error = str(ex)
            raise
        self.ranked = rank(rows, fusion=e.fusion_coins, min_volume_usd=cfg["min_volume_musd"] * 1e6,
                           max_spread_pct=cfg["max_spread_pct"], trending=e.social.trending,
                           mentions=e.social.mention_counts)
        core = set(e.core_crypto())
        n = cfg["max_hot"]
        cands = [r for r in self.ranked if r["heat"] is not None and r["symbol"] not in core]
        want = [r["symbol"] for r in cands if r["heat"] > 0][:n]
        # hysteresis: a watched coin only leaves once it falls well out of the top
        added, dropped = await e.set_scanned(want, keep={r["symbol"] for r in cands[: n * 2]})
        if added or dropped:
            parts = []
            if added:
                parts.append("watching " + ", ".join(added))
            if dropped:
                parts.append("dropped " + ", ".join(dropped))
            self.say("Radar: " + "; ".join(parts) + ".")

    async def run(self, bb: Blackboard) -> None:
        if not self.ctx.settings["scanner"]["on"]:
            self.status, self.summary = "off", "radar off in Controls: only your own coin list is watched"
            return
        if self.error:
            self.status, self.summary = "warn", f"scan failed: {self.error[:80]}"
            return
        if not self.ranked:
            self.summary = "first scan running…"
            return
        usable = [r for r in self.ranked if r["heat"] is not None]
        hot = [r for r in usable if r["symbol"] in self.ctx.scanned]
        fus = self.ctx.fusion_coins
        self.summary = (f"{len(self.ranked)} coins scanned{f', {len(fus)} on Fusion' if fus else ''}, "
                        f"{len(usable)} pass the filters, {len(hot)} hot ones watched: "
                        + ", ".join(r["symbol"] for r in hot[:5]))
        self.detail = {"scanned": len(self.ranked), "usable": len(usable), "watching": sorted(self.ctx.scanned),
                       "top": [{k: r[k] for k in ("symbol", "heat", "change")} for r in usable[:8]]}


class LiveDesk(Agent):
    id = "livedesk"
    name = "Live Desk"
    role = "Looks after the real Bitpanda account: wallet, spend cap, spread check, leftover coins"
    inputs = ["buyer", "src_fusion"]
    kind = "gate"
    explain = ("Plain code, no AI. Only acts when LIVE is on. It copies the champion's trades to Bitpanda Fusion with "
               "extra brakes: never more than your live cap in coins, never a bigger order than your order cap, and "
               "no buy when Fusion's order book shows a wide spread. It only ever sells coins the bot bought itself "
               "(your own coins only if you allow it), and when a new champion takes over, the new one takes "
               "over the bot's coins and sells them by its own rules instead of selling them at the switch. "
               "Three live errors in a row switch everything back to paper.")
    outputs = "real orders on Bitpanda Fusion, the live wallet in the cockpit"

    async def run(self, bb: Blackboard) -> None:
        w = self.ctx.wallet
        if not w:
            self.status = "idle"
            self.summary = ("no Fusion key: add BITPANDA_FUSION_API_KEY to .env" if not self.ctx.settings.fusion_api_key
                            else "connecting to Fusion…")
            return
        if w.get("error"):
            self.status, self.summary = "warn", f"Fusion: {w['error'][:80]}"
            return
        cur, lv = w["currency"], self.ctx.settings["live"]
        mode = "LIVE" if self.ctx.mode == "live" else "watching (paper mode)"
        self.summary = (f"{mode}: {w['fiat']:.2f} {cur} cash, bot coins {w['bot_value']:.2f} {cur} "
                        f"of max {lv['max_invest']:.0f}, P&L {w['bot_pnl']:+.2f} {cur}")
        self.detail = {"wallet_age_s": round(time.time() - w["ts"]), "coins": [c["symbol"] for c in w["coins"]]}
