"""The Optimizer: runs strategy variants side by side on the same live data, keeps the good ones,
breeds new ones from the best, and (only if you allow it) promotes a challenger to champion.

Before adding a child it backtests several mutations on recent price history and keeps the best one,
so new variants start from something that would at least have worked lately.
It can change strategy settings, never the Risk Officer's limits.
"""
from __future__ import annotations

import asyncio
import random
import time

from ..strategy import StrategyConfig
from .base import Agent, Blackboard

PROMOTE_MARGIN_PCT = 2.0


class Optimizer(Agent):
    id = "optimizer"
    name = "Optimizer"
    role = "Runs strategy variants in parallel, breeds better ones, retires losers, proposes a new champion"
    inputs = ["buyer"]
    explain = ("Free, no AI. Every strategy trades its own paper account on the same live data. On a schedule the "
               "Optimizer retires the weakest mature strategy, then breeds a new one: it mutates the best strategy "
               "several times, backtests each mutation on recent price history, and adds only the winner. It "
               "suggests a new champion when a challenger beats it clearly, and promotes it only if you allow that. "
               "Fitness = return minus half the worst drawdown, so wild swings are punished.")
    outputs = "new and retired strategies, champion suggestions"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.next_due = time.time() + 600  # first evolution 10 minutes after start
        self.rng = random.Random()

    @property
    def cfg(self) -> dict:
        return self.ctx.settings["optimizer"]

    async def run(self, bb: Blackboard) -> None:
        board = self.ctx.leaderboard()
        if board:
            best = board[0]
            self.summary = f"{len(board)} variants; leader {best['name']} {best['return_pct']:+.2f}%"
            mins = max(0, (self.next_due - time.time()) / 60)
            self.detail = {"leaderboard": [{k: b[k] for k in ("name", "return_pct", "fitness", "trades")} for b in board],
                           "next_evolution_min": round(mins)}
        if time.time() < self.next_due:
            return
        self.next_due = time.time() + self.cfg["every_minutes"] * 60
        await self.evolve(board)

    async def evolve(self, board: list[dict]) -> None:
        c = self.cfg
        mature = [b for b in board if b["age_h"] >= c["min_age_to_judge_h"]]
        champ = next((b for b in board if b["champion"]), None)

        # retire the weakest mature non-champion when the lab is full
        while len(self.ctx.variants) >= c["max_variants"] and mature:
            worst = min((b for b in mature if not b["champion"]), key=lambda b: b["fitness"], default=None)
            if not worst:
                break
            mature.remove(worst)
            self.ctx.retire_variant(worst["id"])
            self.say(f"Retired {worst['name']} ({worst['return_pct']:+.2f}% after {worst['age_h']:.0f}h).")

        # breed: mutate the best mature variant (or the champion while nothing is mature yet)
        parent = max(mature, key=lambda b: b["fitness"], default=champ)
        if parent and len(self.ctx.variants) < c["max_variants"]:
            pv = self.ctx.variants[parent["id"]]
            child, note = await self._screened_child(pv)
            v = self.ctx.add_variant(child, parent_id=pv.id, note=note)
            self.say(f"Bred {v.name} ({note}). It starts with a fresh paper account.")

        # promotion
        if champ:
            rivals = [b for b in board if not b["champion"] and b["age_h"] >= c["min_age_to_promote_h"]
                      and b["id"] in self.ctx.variants]
            best = max(rivals, key=lambda b: b["fitness"], default=None)
            if best and best["fitness"] > champ["fitness"] + PROMOTE_MARGIN_PCT:
                if self.ctx.db.get("auto_promote", False):
                    self.ctx.promote(best["id"])
                    self.say(f"Promoted {best['name']} to champion (fitness {best['fitness']:+.2f} vs {champ['fitness']:+.2f}).")
                else:
                    self.say(f"Suggestion: {best['name']} is beating the champion "
                             f"({best['fitness']:+.2f} vs {champ['fitness']:+.2f}). Promote it in the Lab if you agree.")

    async def _screened_child(self, parent):
        n = int(self.cfg["screen_candidates"])
        if n <= 1:
            return parent.config.mutate(self.rng), f"mutation of {parent.name}"
        try:
            ranked = await asyncio.to_thread(self.ctx.autotune_sync, parent.config, n, 24)
        except Exception as e:  # no history yet: fall back to a blind mutation
            self.say(f"Backtest screening skipped ({e}); using a plain mutation.", "warn")
            return parent.config.mutate(self.rng), f"mutation of {parent.name}"
        best = next((r for r in ranked if r["label"] != "current settings" and r["trades"] > 0), None)
        if not best:
            return parent.config.mutate(self.rng), f"mutation of {parent.name}"
        cfg = StrategyConfig.from_dict(best["config"])
        return cfg, (f"mutation of {parent.name}: best of {n} backtested candidates "
                     f"({best['return_pct']:+.2f}% over the last {best['hours']:.0f}h)")
