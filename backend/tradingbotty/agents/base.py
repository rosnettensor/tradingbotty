"""Shared agent plumbing: every agent has a node in the graph, a status, a log voice and a cost counter."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Blackboard:
    """What agents share. Each agent reads from it and writes its own results into it."""
    tech: dict[str, dict[str, float]] = field(default_factory=dict)     # symbol -> technical signals
    hype: dict[str, float] = field(default_factory=dict)                # symbol -> -1..1
    news: dict[str, float] = field(default_factory=dict)                # symbol -> -1..1
    news_events: list[dict] = field(default_factory=list)               # scored headlines, newest first
    market: dict[str, float] = field(default_factory=dict)              # symbol -> regime signal -1..1
    regime: dict[str, Any] = field(default_factory=dict)                # summary for the dashboard
    risk_appetite: float = 1.0                                          # Professor's multiplier 0.5..1.5
    avoid: set[str] = field(default_factory=set)                        # symbols the Professor vetoed
    scores: dict[str, dict[str, float]] = field(default_factory=dict)   # variant -> symbol -> score
    notes: list[str] = field(default_factory=list)


class Agent:
    id = "agent"
    name = "Agent"
    role = ""
    kind = "agent"            # agent | source | gate
    inputs: list[str] = []    # ids of upstream nodes, drawn as wires in the node view
    uses_ai = False

    def __init__(self, ctx):
        self.ctx = ctx        # the Engine: settings, db, bus, feeds, llm
        self.status = "idle"
        self.summary = "waiting for first run"
        self.last_run = 0.0
        self.runs = 0
        self.cost = 0.0
        self.detail: dict = {}

    def say(self, message: str, level: str = "info") -> None:
        entry = self.ctx.db.log(self.name, level, message)
        self.ctx.bus.publish("log", entry)

    def node(self) -> dict:
        return {
            "id": self.id, "name": self.name, "role": self.role, "kind": self.kind, "inputs": self.inputs,
            "status": self.status, "summary": self.summary, "last_run": self.last_run, "runs": self.runs,
            "cost": round(self.cost, 4), "uses_ai": self.uses_ai, "detail": self.detail,
        }

    async def step(self, bb: Blackboard) -> None:
        self.status = "running"
        try:
            await self.run(bb)
            self.status = "ok"
        except Exception as e:  # one broken agent must not stop the team
            self.status = "error"
            self.summary = f"error: {e}"
            self.say(f"I hit an error: {e}", "error")
        self.last_run = time.time()
        self.runs += 1

    async def run(self, bb: Blackboard) -> None:
        raise NotImplementedError


class Source(Agent):
    """A data feed shown as a node; it doesn't think, it just reports health."""
    kind = "source"

    def __init__(self, ctx, id: str, name: str, role: str, health):
        super().__init__(ctx)
        self.id, self.name, self.role = id, name, role
        self._health = health

    async def run(self, bb: Blackboard) -> None:
        ok = self._health()
        self.status = "ok" if ok else "warn"
        self.summary = "live" if ok else "no data yet / failing"

    async def step(self, bb: Blackboard) -> None:
        await self.run(bb)
        self.last_run = time.time()
        self.runs += 1
