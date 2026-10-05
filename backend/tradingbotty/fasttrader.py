"""The fast pot: a small, separate pot of real money for one fast rule from the fast lab.

- Its own money: a fixed amount in your currency (gains and losses stay in the pot, so it compounds) or a share of
  the account. The daily brain keeps that amount out of its budget and never spends the pot's cash.
- Its own coins (kv fast_qty / fast_cost): it only ever sells what it bought; it never sells your coins or the
  daily brain's, and never buys a coin the brain holds.
- Exactly the tested rule: every 4 hours, right after a 4-hour candle closes, it asks the strategy from the fast lab
  what to hold, with the pot's real positions (entry price, peak, entry time) handed to it, and trades the difference.
- Every order goes through the same Risk Officer checks (kill switch, Fusion minimum, spread, cash with fee room).
"""
from __future__ import annotations

import bisect
import math
import time

from . import fastlab, research

DEFAULT = "Pump rider: up 15%+ in 1d, 2 slots, take +20% / stop -8%, max 2d, volume 3x"
MIN_SLOT = 40.0      # per position: Fusion's 25 minimum, with room for a 35% drop before it can't be sold anymore
SETTLE = 120         # seconds after a candle closes before the exchange is sure to have it


class FastTrader:
    def __init__(self, engine):
        self.e = engine

    # ------------------------------------------------------------------ settings and state (kv "fast")
    def cfg(self) -> dict:
        c = self.e.db.get("fast") or {}
        return {"on": False, "strategy": DEFAULT, "mode": "chf", "chf": 40.0, "pct": 13.0, "realized": 0.0,
                "trades": 0, "wins": 0, "bar": None, "pos": {}, "steps": [], "note": "", **c}

    def save(self, c: dict) -> None:
        self.e.db.set("fast", c)

    def on(self) -> bool:
        return bool(self.cfg().get("on"))

    def pot_size(self, c: dict | None = None) -> float:
        """What the pot may trade with: the amount you set plus everything it has won or lost since (compounding),
        or your share of the whole account."""
        c = c or self.cfg()
        if c["mode"] == "pct":
            total = (self.e.wallet or {}).get("total") or 0.0
            return round(total * c["pct"] / 100, 2)
        return round(max(0.0, c["chf"] + c["realized"]), 2)

    def slots(self, c: dict | None = None) -> int:
        c = c or self.cfg()
        strat = fastlab.by_name(c["strategy"])
        return min(getattr(strat, "slots", 1), int(self.pot_size(c) // MIN_SLOT))

    def cash_reserve(self) -> float:
        """Cash the daily brain must leave alone: the pot minus what the pot has in coins right now."""
        c = self.cfg()
        if not c["on"]:
            return 0.0
        in_coins = sum(self.e.db.get("fast_cost", {}).values())
        return round(max(0.0, self.pot_size(c) - in_coins), 2)

    def status(self) -> dict:
        c = self.cfg()
        nxt = (time.time() // fastlab.BAR + 1) * fastlab.BAR + SETTLE
        return {**{k: c[k] for k in ("on", "strategy", "mode", "chf", "pct", "realized", "trades", "wins", "steps",
                                     "note", "pos")},
                "decided": c.get("ts"), "pot": self.pot_size(c), "slots": self.slots(c), "next": nxt,
                "min_slot": MIN_SLOT, "robust": c.get("robust")}

    async def set(self, on: bool | None = None, strategy: str | None = None, mode: str | None = None,
                  chf: float | None = None, pct: float | None = None) -> dict:
        c = self.cfg()
        if strategy:
            if not fastlab.by_name(strategy) or not strategy.startswith(("Fast", "Pump", "Dip")):
                raise ValueError(f"unknown fast rule: {strategy}")
            c["strategy"] = strategy
            c["bar"] = None  # decide again at once with the new rule
        if mode in ("chf", "pct"):
            c["mode"] = mode
        if chf is not None:
            c["chf"] = max(0.0, float(chf))
        if pct is not None:
            c["pct"] = max(0.0, min(100.0, float(pct)))
        if on is not None:
            if on and self.slots(c) < 1:
                raise ValueError(f"the pot ({self.pot_size(c):.2f}) is below {MIN_SLOT:g} per coin: Fusion's 25 minimum "
                                 f"plus room to sell after a drop. Make the pot bigger.")
            c["on"] = bool(on)
            c["bar"] = None
            cur = getattr(self.e.live, "currency", "CHF")
            if on:
                self.e._log("Fast Trader", "live", f"Fast pot ON: {self.pot_size(c):.2f} {cur} of real money for "
                                                   f"\"{c['strategy']}\", deciding every 4 hours.")
            else:
                self.e._log("Fast Trader", "info", "Fast pot OFF: no new fast trades; its coins stay until you sell them "
                                                   "or switch it on again.")
        self.save(c)
        if c["on"] and on:
            import asyncio
            asyncio.create_task(self.tick())
        return self.status()

    def booked_sell(self, sym: str, ex: dict) -> float:
        """Book a sale of a pot coin: its gain or loss goes into the pot."""
        c = self.cfg()
        pos = c["pos"].pop(sym, None) or {}
        got = float(ex.get("notional", 0) or 0) - float(ex.get("fee", 0) or 0)
        pnl = round(got - pos.get("cost", got), 2)
        c["realized"] = round(c["realized"] + pnl, 2)
        c["trades"] += 1
        c["wins"] += 1 if pnl > 0 else 0
        self.save(c)
        return pnl

    async def close_all(self) -> dict:
        """Sell every coin of the fast pot now (your button)."""
        e = self.e
        if e.mode != "live" or not e.live:
            raise ValueError("live trading is off")
        done = []
        for sym in list(e.db.get("fast_qty", {})):
            ex = await e._live_sell(sym, "fast pot: sold by you", book="fast")
            if ex:
                pnl = self.booked_sell(sym, ex)
                done.append(f"sold {sym} ({pnl:+.2f})")
            elif sym in e.db.get("fast_qty", {}):
                done.append(f"{sym}: Fusion refused, sell it in the app")
        c = self.cfg()
        c["pos"] = {k: v for k, v in c["pos"].items() if k in e.db.get("fast_qty", {})}
        self.save(c)
        return {"done": done, **self.status()}

    # ------------------------------------------------------------------ the decision, every 4 hours
    async def tick(self) -> None:
        e = self.e
        c = self.cfg()
        if not c["on"] or e.mode != "live" or not e.live or e.kill_switch or e.__dict__.get("_fast_trading"):
            return
        if not (e.wallet or {}).get("total"):
            return
        now = time.time()
        bar = (now // fastlab.BAR - 1) * fastlab.BAR  # the newest closed 4-hour candle (its open time)
        if c.get("bar") == bar or now < bar + fastlab.BAR + SETTLE:
            return
        e._fast_trading = True
        try:
            await self._decide(c, bar)
        except Exception as ex:
            e._log("Fast Trader", "error", f"Fast decision failed: {str(ex)[:160] or type(ex).__name__}. Retrying next minute.")
        finally:
            e._fast_trading = False

    async def _candles(self, held: list[str]) -> research.Candles:
        e = self.e
        if e.settings.simulate:
            rows = fastlab.synthetic_4h(["BTC", *research.UNIVERSE[1:16], *held])
        else:
            coins = (e.db.get("fast_coins") or {})
            if not coins.get("list") or time.time() - coins.get("ts", 0) > 86400:
                coins = {"ts": time.time(), "list": await fastlab.pick_coins(e.prices.client, e.fusion_coins)}
                e.db.set("fast_coins", coins)
            rows = await fastlab.update_4h(e.prices.client, e.db, list(dict.fromkeys([*coins["list"], *held])), e._log)
        if "BTC" not in rows:
            raise ValueError("no 4-hour Bitcoin candles")
        return research.Candles.from_rows(rows)

    async def _decide(self, c: dict, bar: float) -> None:
        e = self.e
        cur = e.live.currency
        name = c["strategy"]
        strat = fastlab.by_name(name)
        if not strat:
            raise ValueError(f"fast rule {name} no longer exists")
        held = [s for s in c["pos"] if s in e.db.get("fast_qty", {})]
        cd = await self._candles(held)
        i = len(cd.days) - 1
        stamp = time.strftime("%d.%m %H:%M UTC", time.gmtime(cd.days[i] + fastlab.BAR))
        if cd.days[i] < bar:
            raise ValueError(f"the newest 4-hour candle isn't there yet ({stamp})")
        slots = self.slots(c)
        strat.slots = max(1, slots)
        # hand the rule the pot's real positions, so it decides exactly like in the test
        for s in held:
            p = c["pos"][s]
            k = max(0, bisect.bisect_left(cd.days, p["bar"]))
            closes = [x for x in (cd.c.get(s) or [])[k:i + 1] if x is not None]
            if hasattr(strat, "entry_px"):
                strat.entry_px[s] = p["entry"]
            if hasattr(strat, "peak"):
                strat.peak[s] = max([p["entry"], *closes])
            if hasattr(strat, "since"):
                strat.since[s] = k
        out = strat.target(cd, i, held)
        want = list(out) if out is not None else list(held)
        how = "your amount + its own gains and losses" if c["mode"] == "chf" else f"{c['pct']:g}% of your account"
        steps = [f"4-hour candle up to {stamp} closed: \"{name}\" on {len(cd.coins)} coins",
                 f"Pot {self.pot_size(c):.2f} {cur} ({how}), {slots} coin{'s' if slots != 1 else ''} at a time",
                 "Pot holds: " + (", ".join(f"{s} (in at {c['pos'][s]['entry']:.6g})" for s in held) or "nothing")]
        if slots < 1:
            steps.append(f"Pot below {MIN_SLOT:g} {cur}: no new buys")
        done = []
        # sells first
        for s in [s for s in held if s not in want]:
            px = cd.c[s][i]
            p = c["pos"][s]
            why = ("take profit" if px and px >= p["entry"] * 1.1 else "stop" if px and px < p["entry"] else "time or exit rule")
            ex = await e._live_sell(s, f"fast pot: {why} ({name})", book="fast")
            if ex:
                pnl = self.booked_sell(s, ex)
                done.append(f"sold {s}: {why}, {pnl:+.2f} {cur}")
                c = self.cfg()
            else:
                done.append(f"{s}: sell didn't go through (below Fusion's minimum? sell it in the app)")
        # then buys, best signal first, each a slot of the pot
        brain_coins = e.db.get("live_qty", {})
        pairs = getattr(e.live, "pairs", None) or {}
        for s in [s for s in want if s not in held]:
            if slots < 1:
                break
            if pairs and s not in pairs:
                done.append(f"{s}: signal, but not on Fusion")
                continue
            if s in brain_coins:
                done.append(f"{s}: signal, but the daily brain holds it (no doubling up)")
                continue
            if e.guard().get(s):
                done.append(f"{s}: signal, but the Guardian blocks it")
                continue
            in_pot = len([x for x in c["pos"] if x in e.db.get("fast_qty", {})])
            if in_pot >= slots:
                break
            size = round(self.pot_size(c) / slots, 2)
            invested = sum(e.db.get("fast_cost", {}).values())
            size = min(size, self.pot_size(c) - invested)
            try:
                amount, got = await e._live_buy(s, size, f"fast pot: {name}", book="fast")
            except Exception as ex:
                done.append(f"{s}: not bought ({str(ex)[:140]})")
                continue
            c = self.cfg()
            c["pos"][s] = {"entry": cd.c[s][i], "bar": cd.days[i], "ts": time.time(), "cost": amount, "qty": got}
            self.save(c)
            e._log("Live Desk", "live", f"LIVE BUY {s}: {amount:.2f} {cur} filled (fast pot: {name}).")
            done.append(f"bought {s} for {amount:.2f} {cur}")
        c = self.cfg()
        c["pos"] = {k: v for k, v in c["pos"].items() if k in e.db.get("fast_qty", {})}
        trades = [d for d in done if d.startswith(("bought", "sold"))]
        c.update(bar=bar, ts=time.time(), steps=steps + (done or ["No signal and nothing to sell: waiting for the next candle"]),
                 note=("; ".join(trades) if trades else "no trades") + f"; holds {', '.join(c['pos']) or 'nothing'}")
        lab = {r["name"]: r for r in (e.db.get("fastlab") or {}).get("rows", [])}
        c["robust"] = bool(lab.get(name, {}).get("robust"))
        self.save(c)
        e._log("Fast Trader", "live" if trades else "info", f"Fast decision ({stamp}): {c['note']}.")
