"""Claude access with a hard spending budget.

The budget is the total you allow (20 USD by default) plus a share of the bot's realized profit.
Every call is priced from the real token usage the API reports and stored in the database.
If there's no API key or the budget is used up, agents fall back to their free math-only logic.
"""
from __future__ import annotations

import json
import re
import time

import anthropic

# USD per million tokens (input, output)
PRICES = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}


class Budget:
    def __init__(self, db, cfg: dict):
        self.db = db
        self.cfg = cfg  # read live, so dashboard changes apply at once

    @property
    def total(self) -> float:
        return float(self.cfg["total_usd"])

    @property
    def days(self) -> int:
        return max(1, int(self.cfg["spread_over_days"]))

    @property
    def share(self) -> float:
        return float(self.cfg["reinvest_profit_share"])

    def spent_total(self) -> float:
        return self.db.query("SELECT COALESCE(SUM(cost_usd),0) s FROM llm_calls")[0]["s"]

    def spent_today(self) -> float:
        since = time.time() - 86400
        return self.db.query("SELECT COALESCE(SUM(cost_usd),0) s FROM llm_calls WHERE ts>?", (since,))[0]["s"]

    def realized_profit(self) -> float:
        # profit of whatever drives the real decisions (the champion), across paper and live
        rows = self.db.query(
            "SELECT COALESCE(SUM(t.pnl),0) p FROM trades t JOIN variants v ON v.id=t.variant_id WHERE v.is_champion=1"
        )
        return rows[0]["p"]

    def cap_total(self) -> float:
        return self.total + self.share * max(0.0, self.realized_profit())

    def cap_today(self) -> float:
        return self.cap_total() / self.days

    def can_spend(self, estimate: float) -> bool:
        return (self.spent_total() + estimate <= self.cap_total()) and (self.spent_today() + estimate <= self.cap_today())

    def snapshot(self) -> dict:
        return {
            "spent_total": round(self.spent_total(), 4),
            "cap_total": round(self.cap_total(), 2),
            "spent_today": round(self.spent_today(), 4),
            "cap_today": round(self.cap_today(), 4),
        }


class LLM:
    def __init__(self, api_key: str | None, budget: Budget, db, fast_model: str, deep_model: str):
        self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2) if api_key else None
        self.budget = budget
        self.db = db
        self.fast_model = fast_model
        self.deep_model = deep_model

    @property
    def available(self) -> bool:
        return self.client is not None

    def _price(self, model: str, inp: int, out: int) -> float:
        pin, pout = PRICES.get(model, (5.0, 25.0))
        return inp / 1e6 * pin + out / 1e6 * pout

    async def json_call(self, agent: str, system: str, prompt: str, schema: dict, deep: bool = False,
                        max_tokens: int = 1500, model: str | None = None) -> dict | None:
        """Ask Claude for JSON matching `schema`. Returns None if unavailable, over budget or failed."""
        if not self.client:
            return None
        model = model or (self.deep_model if deep else self.fast_model)
        deep = deep or model != self.fast_model
        # worst case estimate: prompt chars/3 tokens in, full max_tokens out (thinking included on deep)
        estimate = self._price(model, len(system + prompt) // 3, max_tokens)
        if not self.budget.can_spend(estimate):
            return {"_over_budget": True}
        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if deep:
            kwargs["output_config"]["effort"] = "low"
        try:
            try:
                resp = await self.client.messages.create(**kwargs)
            except anthropic.BadRequestError:
                # model without structured outputs: ask for plain JSON instead
                kwargs.pop("output_config")
                kwargs["system"] = system + "\nReply with only a JSON object matching this schema:\n" + json.dumps(schema)
                resp = await self.client.messages.create(**kwargs)
        except anthropic.APIError as e:
            self.db.log(agent, "warn", f"Claude call failed: {e.__class__.__name__}")
            return None
        cost = self._price(model, resp.usage.input_tokens, resp.usage.output_tokens)
        self.db.execute(
            "INSERT INTO llm_calls(ts,agent,model,input_tokens,output_tokens,cost_usd) VALUES(?,?,?,?,?,?)",
            (time.time(), agent, model, resp.usage.input_tokens, resp.usage.output_tokens, cost),
        )
        if resp.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                return None
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        data["_cost"] = cost
        return data
