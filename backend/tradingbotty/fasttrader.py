"""The fast pot: a small, separate pot of real money for one fast rule from the fast lab.

- Its own money: a fixed amount in your currency (gains and losses stay in the pot, so it compounds) or a share of
  the account. The daily brain keeps that amount out of its budget and never spends the pot's cash.
- Its own coins (kv fast_qty / fast_cost): it only ever sells what it bought; it never sells your coins or the
  daily brain's, and never buys a coin the brain holds.
- Exactly the tested rule: every 4 hours, right after a 4-hour candle closes, it asks the strategy from the fast lab
  what to hold, with the pot's real positions (entry price, peak, entry time) handed to it, and trades the difference.
- Every order goes through the same Risk Officer checks (kill switch, Fusion minimum, spread, cash with fee room).
- A floor (kv fast "floor", 30 by default): when the pot is worth less than that (its cash plus its coins at live
  prices), it sells its own coins, switches itself off and tells your phone. Only you switch it on again.
"""
from __future__ import annotations

import bisect
import math
import time

from . import fastlab, research, scoreboard
from .readiness import evidence

DEFAULT = "Dip buyer: down 15%+ in 1d inside an uptrend, 2 slots, take +10% / stop -10%"  # the one robust fast rule
MIN_SLOT = 32.0      # per position: Fusion's 25 (some coins 30) minimum, with room for a 20% drop before it can't be sold
SETTLE = 120         # seconds after a candle closes before the exchange is sure to have it
FLOOR = 30.0         # default floor: below this value the pot sells its coins and switches itself off


class FastTrader:
    def __init__(self, engine):
        self.e = engine

    # ------------------------------------------------------------------ settings and state (kv "fast")
    def cfg(self) -> dict:
        c = self.e.db.get("fast") or {}
        return {"on": False, "strategy": DEFAULT, "mode": "chf", "chf": 40.0, "pct": 13.0, "realized": 0.0,
                "trades": 0, "wins": 0, "bar": None, "pos": {}, "steps": [], "note": "", "floor": FLOOR, **c}

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

    def value(self, prices: dict | None = None, c: dict | None = None) -> float:
        """What the pot is worth right now: its cash (the pot minus what it paid for its coins) plus its coins at
        live prices. Without `prices`, the prices of the last account read. A coin without a price counts at cost,
        so a missing price can never look like a crash."""
        c = c or self.cfg()
        qty, cost = self.e.db.get("fast_qty", {}), self.e.db.get("fast_cost", {})
        if not qty:
            return self.pot_size(c)
        if prices is None:
            prices = {x["symbol"]: x.get("price") for x in (self.e.wallet or {}).get("fast_coins") or []}
        coins = sum(q * prices[s] if prices.get(s) else cost.get(s, 0.0) for s, q in qty.items())
        return round(self.pot_size(c) - sum(cost.values()) + coins, 2)

    def status(self) -> dict:
        c = self.cfg()
        nxt = (time.time() // fastlab.BAR + 1) * fastlab.BAR + SETTLE
        return {**{k: c[k] for k in ("on", "strategy", "mode", "chf", "pct", "realized", "trades", "wins", "steps",
                                     "note", "pos", "floor")},
                "decided": c.get("ts"), "pot": self.pot_size(c), "slots": self.slots(c), "next": nxt,
                "entry_check": evidence(self.e.db.get("fastlab"), c["strategy"]),
                "min_slot": MIN_SLOT, "robust": c.get("robust"), "value": self.value(c=c), "stopped": c.get("stopped")}

    async def set(self, on: bool | None = None, strategy: str | None = None, mode: str | None = None,
                  chf: float | None = None, pct: float | None = None, floor: float | None = None) -> dict:
        c = self.cfg()
        if strategy:
            if not fastlab.by_name(strategy) or not strategy.startswith(("Fast", "Pump", "Dip")):
                raise ValueError(f"unknown fast rule: {strategy}")
            c["strategy"] = strategy
            c["bar"] = None  # decide again at once with the new rule
        if mode in ("chf", "pct"):
            c["mode"] = mode
        for label, value in (("amount", chf), ("share", pct), ("floor", floor)):
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{label} must be finite")
        if chf is not None:
            c["chf"] = max(0.0, float(chf))
        if pct is not None:
            c["pct"] = max(0.0, min(100.0, float(pct)))
        if floor is not None:
            c["floor"] = max(0.0, float(floor))
        if on is not None:
            if on and self.slots(c) < 1:
                raise ValueError(f"the pot ({self.pot_size(c):.2f}) is below {MIN_SLOT:g} per coin: Fusion's 25 minimum "
                                 f"plus room to sell after a drop. Make the pot bigger.")
            if on and self.value(c=c) < c["floor"]:
                raise ValueError(f"the pot is worth {self.value(c=c):.2f}, below your floor of {c['floor']:.2f}: it would "
                                 "stop again at once. Raise the amount or lower the floor first.")
            c["on"] = bool(on)
            if on:
                c.pop("stopped", None)  # you switched it on again: the floor note is done
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
        pos = c["pos"].get(sym) or {}
        sold = float(ex.get("quantity", 0) or 0)
        fraction = min(1.0, sold / pos["qty"]) if pos.get("qty") else 1.0
        sold_cost = pos.get("cost", 0) * fraction
        if fraction < 1:
            c["pos"][sym] = {**pos, "qty": pos["qty"] - sold, "cost": pos["cost"] - sold_cost}
        else:
            c["pos"].pop(sym, None)
        got = float(ex.get("notional", 0) or 0) - float(ex.get("fee", 0) or 0)
        pnl = round(got - sold_cost, 2)
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

    # ------------------------------------------------------------------ live exits, every minute
    async def watch(self) -> None:
        """Take profit and stop on the live price, every minute, instead of only when a 4-hour candle closes.
        Measured against what the pot really paid (amount / quantity, in your currency). API3 fell to -13.7% before
        the 4-hour check could sell it at its -10% stop; this sells it close to -10%."""
        e = self.e
        c = self.cfg()
        if (not c["on"] or not c.get("live_exits", True) or e.mode != "live" or not e.live or e.kill_switch
                or e.__dict__.get("_fast_trading")):
            return
        strat = fastlab.by_name(c["strategy"])
        tp, stop = getattr(strat, "tp", None), getattr(strat, "stop", None)
        w = e.wallet or {}
        fresh = w.get("ts") and 0 <= time.time() - w["ts"] < 90 and not w.get("error") and not w.get("stale")
        prices = (e.__dict__.get("fusion_prices") or {}) if fresh else await e.live.prices()
        qty, cost = e.db.get("fast_qty", {}), e.db.get("fast_cost", {})
        hits = []
        for s, q in qty.items():
            px, paid = prices.get(s), cost.get(s)
            if not px or not paid or not q or s not in c["pos"]:
                continue
            r = px * q / paid
            if tp and r >= 1 + tp:
                hits.append((s, "take profit", r))
            elif stop and r <= 1 - stop:
                hits.append((s, "stop", r))
        if not hits:
            return
        e._fast_trading = True
        try:
            for s, why, r in hits:
                e.why("fast", s, "SELL", [["ok" if why == "take profit" else "no",
                                           f"Live-{'Ziel' if why == 'take profit' else 'Stopp'}: {(r - 1) * 100:+.1f}% "
                                           f"gegenüber dem Kaufpreis, sofort verkauft statt bis zur 4-Stunden-Kerze zu warten"]])
                ex = await e._live_sell(s, f"fast pot: live {why} ({c['strategy']})", book="fast")
                if ex:
                    pnl = self.booked_sell(s, ex)
                    e._log("Fast Trader", "live", f"Live {why} on {s}: {pnl:+.2f} {e.live.currency} ({(r - 1) * 100:+.1f}%).")
        finally:
            e._fast_trading = False

    # ------------------------------------------------------------------ the decision, every 4 hours
    async def tick(self) -> None:
        e = self.e
        c = self.cfg()
        if not c["on"] or e.mode != "live" or not e.live or e.kill_switch or e.__dict__.get("_fast_trading"):
            return
        if not (e.wallet or {}).get("total"):
            return
        if await self._below_floor(c):
            return
        now = time.time()
        bar = (now // fastlab.BAR - 1) * fastlab.BAR  # the newest closed 4-hour candle (its open time)
        newly_ready = (c.get("entry_check", {}).get("ready") is False
                       and evidence(e.db.get("fastlab"), c["strategy"])["ready"])
        if (c.get("bar") == bar and not newly_ready) or now < bar + fastlab.BAR + SETTLE:
            return
        e._fast_trading = True
        try:
            await self._decide(c, bar)
        except Exception as ex:
            msg = f"Fast decision failed: {str(ex)[:160] or type(ex).__name__}. Retrying every minute."
            if e.__dict__.get("_fast_err") != (bar, msg[:60]):  # say it once per 4-hour candle, not every minute
                e._fast_err = (bar, msg[:60])
                e._log("Fast Trader", "error", msg)
        finally:
            e._fast_trading = False

    async def _below_floor(self, c: dict) -> bool:
        """The floor, checked every minute: below it the pot sells its own coins at once (play money, you want it
        protected), switches itself off and sends your phone the numbers. The brain's coins and yours are never
        touched: _live_sell(book="fast") only sells what the pot bought itself."""
        e = self.e
        floor = float(c.get("floor") or 0)
        if floor <= 0:
            return False
        held = e.db.get("fast_qty", {})
        prices = None
        w = e.wallet or {}
        fresh = (w.get("fast_coins") and 0 <= time.time() - (w.get("ts") or 0) < 120
                 and not w.get("error") and not w.get("stale"))  # the account read every 30 s will do
        if held and not fresh and hasattr(e.live, "prices"):
            try:
                prices = await e.live.prices()
            except Exception:
                return False  # missing prices cannot turn an old snapshot into a liquidation signal
        worth = self.value(prices, c)
        if worth >= floor:
            return False
        cur = getattr(e.live, "currency", "CHF")
        done, phone = [], []  # the dashboard's words (English) and the phone's (German)
        e._fast_trading = True
        try:
            c["on"] = False  # off first: nothing may buy while the coins are sold
            self.save(c)
            for sym in list(held):
                ex = await e._live_sell(sym, f"fast pot: worth {worth:.2f} {cur}, below your floor of {floor:.2f}",
                                        book="fast")
                if ex:
                    pnl = self.booked_sell(sym, ex)
                    done.append(f"sold {sym} ({pnl:+.2f} {cur})")
                    phone.append(f"🔴 {sym} verkauft ({pnl:+.2f} {cur})")
                elif sym in e.db.get("fast_qty", {}):
                    done.append(f"{sym}: Fusion refused the sell, sell it in the app")
                    phone.append(f"⚠️ {sym}: Fusion hat den Verkauf abgelehnt, bitte in der App verkaufen")
        finally:
            e._fast_trading = False
        c = self.cfg()
        c["pos"] = {k: v for k, v in c["pos"].items() if k in e.db.get("fast_qty", {})}
        c["stopped"] = {"ts": time.time(), "value": worth, "floor": floor, "done": done,
                        "pot": self.pot_size(c), "realized": c["realized"]}
        c["note"] = f"stopped: worth {worth:.2f} {cur}, below the floor of {floor:.2f}"
        self.save(c)
        e._log("Fast Trader", "live", f"Fast pot STOPPED: worth {worth:.2f} {cur}, below your floor of {floor:.2f} {cur}. "
               + (f"Sold its coins: {'; '.join(done)}. " if done else "It held no coins. ")
               + "Only you can switch it on again (raise the amount or lower the floor first).")
        sold = "\n".join(phone) if phone else "Er hielt keine Coins."
        e._notify_later(f"Der Fast-Topf ist nur noch {worth:.2f} {cur} wert, unter deiner Grenze von {floor:.2f} {cur}. "
                        f"Er hat sich selbst ausgeschaltet.\n\n{sold}\n\n"
                        f"Topf jetzt {self.pot_size(c):.2f} {cur} · gebucht {c['realized']:+.2f} {cur} seit Start.\n"
                        "Deine Coins und die des Daily Brain bleiben unberührt. Einschalten nur durch dich "
                        "(Research, Fast Trader Lab: Betrag erhöhen oder Grenze senken).",
                        title="⚡ Fast-Topf gestoppt", tags=["octagonal_sign"], priority=4)
        return True

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
            e.why("fast", s, "SELL", scoreboard.fast_why(cd, strat, s, i, "SELL", p["entry"], p.get("ts")))
            ex = await e._live_sell(s, f"fast pot: {why} ({name})", book="fast")
            if ex:
                pnl = self.booked_sell(s, ex)
                done.append(f"sold {s}: {why}, {pnl:+.2f} {cur}")
                c = self.cfg()
            else:
                done.append(f"{s}: sell didn't go through (below Fusion's minimum? sell it in the app)")
        # Evidence gates only new entries. Existing exit rules above remain active.
        proof = evidence(e.db.get("fastlab"), name)
        if not proof["ready"]:
            steps.append("Neue Käufe gesperrt: " + proof["reason"])
        # then buys, best signal first, each a slot of the pot
        brain_coins = e.db.get("live_qty", {})
        pairs = getattr(e.live, "pairs", None) or {}
        course = e.stance.get()
        hours = (getattr(strat, "max_hold", None) or 18) * fastlab.BAR / 3600

        def ghost(s, who, why):
            e.ghosts.add("fast", s, who, why, self.pot_size(c) / max(1, slots), hours=hours,
                         tp=getattr(strat, "tp", None), stop=getattr(strat, "stop", None))

        for s in [s for s in want if s not in held]:
            if slots < 1 or not proof["ready"]:
                break
            if not course["buys"]:
                done.append(f"{s}: signal, but your course is {course['name']}")
                ghost(s, "course", f"Kurs {course['name']}: Dip-Signal nicht genutzt")
                continue
            if s in e.stance.locked():
                done.append(f"{s}: signal, but Bunkern locked its gain earlier")
                ghost(s, "course", "Bunkern: nach gesichertem Gewinn nicht wieder gekauft")
                continue
            if pairs and s not in pairs:
                done.append(f"{s}: signal, but not on Fusion")
                continue
            if s in brain_coins:
                done.append(f"{s}: signal, but the daily brain holds it (no doubling up)")
                continue
            g = e.guard().get(s)
            if g:
                done.append(f"{s}: signal, but the Guardian blocks it")
                ghost(s, "professor" if g.get("sources") == ["The Professor"] else "guardian",
                      f"Kauf gesperrt: {(g.get('titles') or [g.get('reason', '?')])[0]}")
                continue
            in_pot = len([x for x in c["pos"] if x in e.db.get("fast_qty", {})])
            if in_pot >= slots:
                break
            size = round(self.pot_size(c) / slots * course["size"], 2)  # Mutig / Bunkern
            invested = sum(e.db.get("fast_cost", {}).values())
            size = min(size, self.pot_size(c) - invested)  # never more than the pot
            e.why("fast", s, "BUY", scoreboard.fast_why(cd, strat, s, i, "BUY")
                  + [["info", f"Grösse: {size:.2f} {cur} = Topf {self.pot_size(c):.2f} / {slots} Platz"
                      + (f" × Kurs {course['size']:g}" if course["size"] != 1 else "")]])
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
        c["entry_check"] = proof
        self.save(c)
        e._log("Fast Trader", "live" if trades else "info", f"Fast decision ({stamp}): {c['note']}.")
