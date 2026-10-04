"""The Optimizer: runs strategy variants side by side on the same live data, keeps the good ones,
breeds new ones from the best, and (only if you allow it) promotes a challenger to champion.

It can change strategy settings, never the Risk Officer's limits.
"""
from __future__ import annotations

import random
import time

from .base import Agent, Blackboard

MAX_VARIANTS = 8
EVERY_SECONDS = 3600
MIN_AGE_TO_JUDGE_H = 6
MIN_AGE_TO_PROMOTE_H = 48
PROMOTE_MARGIN_PCT = 2.0


class Optimizer(Agent):
    id = "optimizer"
    name = "Optimizer"
    role = "Runs strategy variants in parallel, breeds better ones, retires losers, proposes a new champion"
    inputs = ["buyer"]

    def __init__(self, ctx):
        super().__init__(ctx)
        self.next_due = time.time() + 600  # first evolution 10 minutes after start
        self.rng = random.Random()

    async def run(self, bb: Blackboard) -> None:
        board = self.ctx.leaderboard()
        if board:
            best = board[0]
            self.summary = f"{len(board)} variants; leader {best['name']} {best['return_pct']:+.2f}%"
            self.detail = {"leaderboard": [{k: b[k] for k in ("name", "return_pct", "fitness", "trades")} for b in board]}
        if time.time() < self.next_due:
            return
        self.next_due = time.time() + EVERY_SECONDS
        await self.evolve(board)

    async def evolve(self, board: list[dict]) -> None:
        mature = [b for b in board if b["age_h"] >= MIN_AGE_TO_JUDGE_H]
        champ = next((b for b in board if b["champion"]), None)

        # retire the weakest mature non-champion when the lab is full
        if len(board) >= MAX_VARIANTS and mature:
            worst = min((b for b in mature if not b["champion"]), key=lambda b: b["fitness"], default=None)
            if worst:
                self.ctx.retire_variant(worst["id"])
                self.say(f"Retired {worst['name']} ({worst['return_pct']:+.2f}% after {worst['age_h']:.0f}h).")

        # breed: mutate the best mature variant (or the champion while nothing is mature yet)
        parent = max(mature, key=lambda b: b["fitness"], default=champ)
        if parent and len(self.ctx.variants) < MAX_VARIANTS:
            pv = self.ctx.variants[parent["id"]]
            child = pv.config.mutate(self.rng)
            v = self.ctx.add_variant(child, parent_id=pv.id, note=f"mutation of {pv.name}")
            self.say(f"Bred new variant {v.name} from {pv.name}. It starts with a fresh paper account.")

        # promotion
        if champ:
            rivals = [b for b in board if not b["champion"] and b["age_h"] >= MIN_AGE_TO_PROMOTE_H]
            best = max(rivals, key=lambda b: b["fitness"], default=None)
            if best and best["fitness"] > champ["fitness"] + PROMOTE_MARGIN_PCT:
                if self.ctx.db.get("auto_promote", False):
                    self.ctx.promote(best["id"])
                    self.say(f"Promoted {best['name']} to champion (fitness {best['fitness']:+.2f} vs {champ['fitness']:+.2f}).")
                else:
                    self.say(f"Suggestion: {best['name']} is beating the champion "
                             f"({best['fitness']:+.2f} vs {champ['fitness']:+.2f}). Promote it in Experiments if you agree.")
