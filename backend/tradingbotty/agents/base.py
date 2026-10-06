"""Shared agent plumbing: every agent has a node in the graph, a status, a log voice and a cost counter."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Blackboard:
    """What agents share. Each agent reads from it and writes its own results into it."""
    news: dict[str, float] = field(default_factory=dict)                # symbol -> news tone -1..1 (fades in hours)
    news_events: list[dict] = field(default_factory=list)               # scored headlines, newest first


class Agent:
    id = "agent"
    name = "Agent"
    role = ""
    kind = "agent"            # agent | source | gate
    inputs: list[str] = []    # ids of upstream nodes, drawn as wires in the node view
    uses_ai = False
    explain = ""              # what it does, in plain words, for the dashboard
    outputs = ""              # what it hands to the next agents
    cadence = ""              # how often it works, in plain words
    can_disable = False       # optional agents can be switched off from the dashboard
    default_prompt = ""       # AI agents: the instructions Claude gets (editable from the dashboard)
    default_model = ""        # AI agents: "fast" or "deep" or a model id

    def __init__(self, ctx):
        self.ctx = ctx        # the Engine: settings, db, bus, feeds, llm
        self.status = "idle"
        self.summary = "waiting for first run"
        self.last_run = 0.0
        self.runs = 0
        self.cost = 0.0
        self.next_run = 0.0   # when its next real piece of work is due (0 = every tick)
        # what the dashboard shows inside the agent: "did" = steps of its last real work, "facts" = [label, value]
        # pairs, "table" = {"cols": [...], "rows": [[...]]}, plus agent-specific data
        self.detail: dict = {}

    def say(self, message: str, level: str = "info") -> None:
        if level in ("warn", "error"):  # a repeating problem is said once per half hour, not every tick
            said = self.__dict__.setdefault("_said", {})
            key = message[:50]
            if time.time() - said.get(key, 0) < 1800:
                return
            said[key] = time.time()
        entry = self.ctx.db.log(self.name, level, message)
        self.ctx.bus.publish("log", entry)

    # ---- dashboard-editable settings, saved in the database
    @property
    def enabled(self) -> bool:
        return not self.can_disable or self.id not in self.ctx.db.get("agents_off", [])

    @property
    def prompt(self) -> str:
        return self.ctx.db.get(f"prompt:{self.id}") or self.default_prompt

    @property
    def model(self) -> str | None:
        """The Claude model this agent uses: the dashboard choice, else its default tier."""
        choice = self.ctx.db.get(f"model:{self.id}") or self.default_model
        llm = self.ctx.llm
        return {"fast": llm.fast_model, "deep": llm.deep_model}.get(choice, choice) or None

    def on_disable(self, bb: Blackboard) -> None:
        """Clear whatever this agent contributes, so switching it off means neutral, not stale."""

    def node(self) -> dict:
        n = {
            "id": self.id, "name": self.name, "role": self.role, "kind": self.kind, "inputs": self.inputs,
            "status": self.status, "summary": self.summary, "last_run": self.last_run, "runs": self.runs,
            "cost": round(self.cost, 4), "uses_ai": self.uses_ai, "detail": self.detail,
            "explain": self.explain, "outputs": self.outputs, "can_disable": self.can_disable, "enabled": self.enabled,
            "cadence": self.cadence, "next_run": self.next_run,
        }
        if self.uses_ai:
            n.update(prompt=self.prompt, prompt_changed=self.prompt != self.default_prompt, model=self.model)
        return n

    async def step(self, bb: Blackboard) -> None:
        if not self.enabled:
            self.on_disable(bb)
            self.status, self.summary = "off", "switched off in the dashboard"
            return
        self.status = "running"
        try:
            await self.run(bb)
            if self.status == "running":
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

    def __init__(self, ctx, id: str, name: str, role: str, health, explain: str = ""):
        super().__init__(ctx)
        self.id, self.name, self.role, self.explain = id, name, role, explain
        self._health = health

    async def run(self, bb: Blackboard) -> None:
        ok = self._health()
        self.status = "ok" if ok else "warn"
        self.summary = "live" if ok else "no data yet / failing"

    async def step(self, bb: Blackboard) -> None:
        await self.run(bb)
        self.last_run = time.time()
        self.runs += 1
