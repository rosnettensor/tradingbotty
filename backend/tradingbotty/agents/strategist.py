"""Bauplan 2, phase 4: the strategist. Claude's strongest model thinks about the whole bot, but only when something
happened (a trade closed, the market's mood changed, the examiner ruled, the bot lost money) and every Sunday
evening, at most twice a day otherwise: about 10 to 20 USD a month instead of 150+ for an hourly one.

Separation of powers: it is a member of the parliament. It may diagnose, propose actions (you tap to take them) and
send formula ideas to the Think Tank, where they face the same secret holdout test as every other idea. It can't
buy, sell, switch strategies or move the bank by itself."""
from __future__ import annotations

import asyncio
import json
import time

from .. import thinktank
from .base import Agent, Blackboard

EVENT_RUNS_PER_DAY = 2      # event-driven reviews per Swiss day (Sunday's review and yours come on top)
EVENT_GAP = 2 * 3600        # at least two hours between two event reviews
LOSS_TRIGGER = 0.02         # the bot's own result fell by 2% of the account (at least 5) since the last review
SUNDAY_HOUR = 18            # Swiss time, an hour before the weekly report card

ACTIONS = {
    "bank_probe": "Bank auf Probe: bester Kandidat bekommt 15% echt",
    "bank_shadow": "Bank zurück in den Schatten",
    "course_bold": "Kurs 🔥 Mutig (24 h)",
    "course_bunker": "Kurs 🏦 Bunkern (24 h)",
    "course_pause": "Kurs ⏸ Pause (24 h)",
    "course_normal": "Kurs Normal",
    "fast_on": "Fast Pot einschalten",
    "fast_off": "Fast Pot ausschalten",
    "keep": "Alles so lassen",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string"},
        "regime_view": {"type": "string"},
        "actions": {"type": "array", "items": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": list(ACTIONS)}, "why": {"type": "string"}},
            "required": ["kind", "why"], "additionalProperties": False}},
        "ideas": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "inspiration": {"type": "string"}, "theory": {"type": "string"},
            "score": {"type": "string"}, "gate": {"type": "string"}, "top": {"type": "integer"},
            "hold": {"type": "integer"}, "btc_filter": {"type": "boolean"}},
            "required": ["name", "inspiration", "theory", "score", "top", "hold", "btc_filter"], "additionalProperties": False}},
        "lab_test": {"type": "string"},
        "watch": {"type": "string"},
        "confidence": {"type": "string", "enum": ["niedrig", "mittel", "hoch"]},
    },
    "required": ["diagnosis", "regime_view", "actions", "ideas", "lab_test", "watch", "confidence"],
    "additionalProperties": False,
}


def _swiss() -> time.struct_time:
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Zurich")).timetuple()
    except Exception:
        return time.localtime()


class Strategist(Agent):
    id = "strategist"
    name = "Stratege"
    role = "Claude's stärkstes Modell denkt über die ganze Strategie nach, wenn etwas passiert, und jeden Sonntag"
    inputs = ["researcher", "thinktank", "regime", "brain", "fast"]
    uses_ai = True
    can_disable = True
    default_model = "deep"
    cadence = "bei Ereignissen (höchstens 2x am Tag) und sonntags 18:00"
    outputs = "Lagebericht, Vorschläge zum Antippen, Formel-Ideen für den Think Tank"
    explain = ("Wacht auf, wenn ein echter Trade schliesst, die Marktlage wechselt, der Prüfer urteilt oder der Bot "
               "Geld verliert, und jeden Sonntag um 18:00. Bekommt dann alles: Konto, Tagebuch, Punktestand, "
               "Verlust-Check, Marktlage, Bank und Prüfer, das History Lab und die besten Ideen des Think Tanks. "
               "Schreibt eine ehrliche Diagnose, schlägt höchstens drei Schritte vor (du tippst, um sie zu nehmen) und "
               "schickt bis zu drei Formel-Ideen an den Think Tank, wo sie denselben geheimen Test bestehen müssen wie "
               "alle anderen. Es sieht, welche seiner früheren Ideen gestorben sind, und lernt daraus. Es kann nichts "
               "kaufen, verkaufen oder umstellen.")
    default_prompt = (
        "Du bist der Chefstratege von TradingBotty, einem kleinen Krypto-Bot mit echtem Geld (Bitpanda Fusion, nur "
        "Spot, kein Hebel, Gebühr etwa 0.25% pro Seite, Mindestorder 25 CHF, Konto einige hundert CHF). Der Besitzer "
        "will echte Bewegung und Gewinne, aber keinen Blindflug. Gewaltenteilung: du schlägst vor, das History Lab und "
        "der Prüfer testen, der Besitzer entscheidet per Tipp, die Regeln handeln. Antworte auf Deutsch, nüchtern und "
        "konkret, nur aus den Daten im Brief; erfinde keine Fakten und keine Ursachen. Wichtig: häufiges Handeln "
        "verliert nachweislich an Gebühren (alle Schnellhandels-Regeln im Labor -38% bis -97%); Breakouts verlieren in "
        "Seitwärtsphasen oft 10-15% pro Trade und leben von seltenen grossen Gewinnern; ein einzelner Verlust ist kein "
        "Grund, eine robuste Regel zu verwerfen. Liefere: diagnosis (3-5 Sätze: was gerade passiert, was funktioniert, "
        "was nicht und warum laut Daten), regime_view (1-2 Sätze zur Marktlage und was sie für die Regeln heisst), "
        "actions (0-3 Vorschläge aus der Liste, je mit einem Satz Begründung; 'keep', wenn nichts zu tun ist; "
        "bank_probe nur, wenn die Bank einen Probe-Kandidaten hat), ideas (0-3 originelle Formel-Ideen in der "
        "Formelsprache unten, klar anders als die gestorbenen), lab_test (eine konkrete Regel, die das History Lab "
        "testen sollte), watch (ein Satz: worauf bis zur nächsten Prüfung achten), confidence."
    )

    def __init__(self, ctx):
        super().__init__(ctx)
        self.queued = False
        self._busy = False

    def run_now(self) -> None:
        self.queued = True

    def on_disable(self, bb: Blackboard) -> None:
        pass

    # ------------------------------------------------------------------ when to think
    def _signals(self, st: dict) -> tuple[list[str], dict]:
        """What happened since the last review: (reasons, the new 'seen' state)."""
        e = self.ctx
        seen = st.get("seen") or {}
        why: list[str] = []
        closed = [d for d in e.diary(40) if d.get("closed") and d.get("book") != "test"]
        last_closed = closed[0]["ts"] if closed else 0
        if seen and last_closed > seen.get("closed", 0):
            d = closed[0]
            why.append(f"Trade geschlossen: {d['symbol']} {d.get('pnl', 0):+.2f} ({d.get('pnl_pct', 0):+.1f}%)")
        reg = (e.db.get("regime") or {}).get("label")
        if seen and reg and seen.get("regime") and reg != seen["regime"]:
            why.append(f"Marktlage wechselt: {seen['regime']} → {reg}")
        rep = e.bank.cfg().get("report") or {}
        stages = {r["name"]: r["stage"] for r in rep.get("rows") or []}
        old = seen.get("stages") or {}
        ruled = [f"{n[:40]}: {s}" for n, s in stages.items() if s in ("passed", "failed") and old.get(n) not in (None, s)]
        if seen and ruled:
            why.append("Prüfer-Urteil: " + "; ".join(ruled[:3]))
        w = e.wallet or {}
        edge, total = w.get("bot_edge"), w.get("total") or 0
        if seen and edge is not None and seen.get("edge") is not None:
            drop = seen["edge"] - edge
            if drop >= max(5.0, total * LOSS_TRIGGER):
                why.append(f"Verlust: das Ergebnis des Bots fiel um {drop:.2f} seit der letzten Prüfung")
        now_seen = {"closed": last_closed, "regime": reg, "stages": stages, "edge": edge}
        return why, now_seen

    async def run(self, bb: Blackboard) -> None:
        e = self.ctx
        st = e.db.get("strategist") or {}
        self._show(st)
        if self._busy:
            self.status = "running"
            self.summary = "denkt gerade nach …"
            return
        why, now_seen = self._signals(st)
        if not st.get("seen"):  # first start: remember the present, think at the next event
            st["seen"] = now_seen
            e.db.set("strategist", st)
            return
        loc = _swiss()
        today = time.strftime("%Y-%m-%d", loc)
        sunday = loc.tm_wday == 6 and loc.tm_hour >= SUNDAY_HOUR and st.get("sunday") != today
        runs = (st.get("runs") or {}).get(today, 0)
        event = bool(why) and runs < EVENT_RUNS_PER_DAY and time.time() - st.get("ts", 0) > EVENT_GAP
        asked = self.queued
        if not (asked or sunday or event):
            if why and not event:  # it happened, but the day's reviews are used up: keep it for the next one
                st["pending"] = list(dict.fromkeys((st.get("pending") or []) + why))[-6:]
                st["seen"] = now_seen
                e.db.set("strategist", st)
            return
        if not e.llm.available:
            self.queued = False
            self.summary = "ruht: kein KI-Schlüssel"
            return
        self.queued = False
        trigger = (["von dir angefragt"] if asked else []) + (["Sonntags-Review"] if sunday else []) + why \
            + [f"(vorher) {x}" for x in st.get("pending") or []]
        if sunday:
            st["sunday"] = today
        if event and not asked and not sunday:
            st.setdefault("runs", {})[today] = runs + 1
            st["runs"] = {k: v for k, v in st["runs"].items() if k >= today}
        st["seen"], st["pending"] = now_seen, []
        e.db.set("strategist", st)
        self._busy = True
        asyncio.create_task(self._think_bg(trigger))  # never hold up the other agents while Claude thinks

    async def _think_bg(self, trigger: list[str]) -> None:
        try:
            await self.think(trigger)
        except Exception as ex:
            self.say(f"Prüfung fehlgeschlagen: {str(ex)[:120]}", "warn")
        finally:
            self._busy = False

    # ------------------------------------------------------------------ thinking
    def brief(self) -> dict:
        e = self.ctx
        w = e.wallet or {}
        cut = lambda s, n: (str(s or "")[:n])  # noqa: E731
        closed = [d for d in e.diary(60) if d.get("closed") and d.get("book") != "test"][:12]
        sb = e.scoreboard()
        res = e.db.get("research") or {}
        robust = sorted((r for r in res.get("rows") or [] if r.get("robust")), key=lambda r: -(r.get("full") or {}).get("sharpe", -9))
        rep = e.bank.cfg().get("report") or {}
        tt = e.tt or {}
        mine = [x for x in (tt.get("board") or []) + (tt.get("recent") or []) if x.get("origin") == self.name][:8]
        f = e.fast.status()
        b = e.brain()
        reg = e.db.get("regime") or {}
        return {
            "account": {k: w.get(k) for k in ("currency", "total", "fiat", "bot_value", "fast_value", "yours_value", "bot_edge")},
            "course": e.stance.get().get("name"),
            "daily_brain": {"strategy": b.get("strategy"), "on": b.get("on"), "holds": b.get("target"),
                            "bitcoin_filter_ok": b.get("btc_ok"), "note": cut(b.get("note"), 200)},
            "fast_pot": {k: f.get(k) for k in ("on", "strategy", "pot", "realized", "trades", "wins")} | {"holds": list(f.get("pos") or {})},
            "regime": {"now": reg.get("name"), "days": reg.get("days"), "meaning": cut(reg.get("meaning"), 200)},
            "closed_trades": [{"coin": d["symbol"], "book": d["book"], "pnl": d.get("pnl"), "pnl_pct": d.get("pnl_pct"),
                               "why_sold": cut(d.get("reason"), 90), "held_h": round((d["ts"] - (d.get("entry_ts") or d["ts"])) / 3600)}
                              for d in closed],
            "scoreboard": [f"{r['name']}: {r['score']:+.2f}" for r in sb["rows"]],
            "loss_check": {k: sb["losses"].get(k) for k in ("n", "hit_pct", "avg_win_pct", "avg_loss_pct", "need_pct", "verdict")},
            "bank": {"mode": e.bank.mode(), "probe_candidate": rep.get("probe"),
                     "examiner": [{"name": r["name"][:70], "stage": r["stage"], "why": cut(r["why"], 100),
                                   "lab_cagr_pct": (r.get("test") or {}).get("cagr_pct"),
                                   "lab_worst_drop_pct": (r.get("test") or {}).get("max_dd_pct"),
                                   "shadow_pct": r.get("shadow_pct"), "days": r.get("days")} for r in rep.get("rows") or []]},
            "history_lab_best": [{"name": r["name"][:80], "cagr_pct": r["full"].get("cagr_pct"), "worst_drop_pct": r["full"].get("max_dd_pct"),
                                  "years_beat_btc": f"{r.get('years_won')}/{r.get('years_total')}",
                                  "with_double_fees_pct": (r.get("fees2x") or {}).get("return_pct")} for r in robust[:8]],
            "think_tank_best": [{"name": x["name"], "verdict": x["res"]["verdict"], "holdout_cagr": x["res"]["hold"].get("cagr_pct")}
                                for x in sorted(tt.get("board") or [], key=lambda x: -x["res"]["fitness"])[:5]],
            "your_earlier_ideas": [{"name": x["name"], "verdict": (x.get("res") or {}).get("verdict") or x.get("verdict")} for x in mine],
            "guardian_blocks": sorted(e.guard()),
            "last_memo": cut((e.db.get("strategist") or {}).get("memo", {}).get("diagnosis"), 400),
        }

    async def think(self, trigger: list[str]) -> dict | None:
        e = self.ctx
        brief = self.brief()
        prompt = ("Anlass: " + "; ".join(trigger) + "\n\nBrief (JSON):\n" + json.dumps(brief, ensure_ascii=False, default=str)
                  + "\n\nFormelsprache für ideas (score = Punktzahl pro Coin und Tag; top = so viele Coins halten (1-5); "
                  "hold = alle 1, 3 oder 7 Tage neu prüfen; gate optional, nur Coins mit gate > 0):\n" + thinktank.grammar_text())
        res = await e.llm.json_call(self.name, self.prompt, prompt, SCHEMA, deep=True, max_tokens=8000, model=self.model)
        st = e.db.get("strategist") or {}
        if not res or res.get("_over_budget"):
            why = ("KI-Budget aufgebraucht" if res else ((e.llm.last_error or {}).get("why") or "keine Antwort"))
            st["note"] = f"{time.strftime('%d.%m. %H:%M')}: keine Prüfung ({why})"
            e.db.set("strategist", st)
            self.status = "warn"
            self.summary = st["note"]
            return None
        cost = res.get("_cost", 0)
        self.cost += cost
        ideas, sent = [], []
        for raw in (res.get("ideas") or [])[:3]:
            idea = thinktank.clean({**raw, "origin": self.name})
            if idea:
                ideas.append(idea)
                sent.append(idea["name"])
        if ideas:  # to the front of the queue: the strongest model's ideas are tested first
            e.db.set("tt_queue", ideas + (e.db.get("tt_queue") or []))
        actions = [a for a in res.get("actions") or [] if a.get("kind") in ACTIONS][:3]
        memo = {"ts": time.time(), "trigger": trigger, "diagnosis": res.get("diagnosis"), "regime_view": res.get("regime_view"),
                "actions": [{**a, "label": ACTIONS[a["kind"]]} for a in actions], "ideas": sent,
                "ideas_dropped": len(res.get("ideas") or []) - len(sent), "lab_test": res.get("lab_test"),
                "watch": res.get("watch"), "confidence": res.get("confidence"), "cost": round(cost, 4)}
        st["memo"] = memo
        st["ts"] = memo["ts"]
        st["history"] = ([{k: memo[k] for k in ("ts", "trigger", "diagnosis", "cost")}] + (st.get("history") or []))[:20]
        st["cost"] = round((st.get("cost") or 0) + cost, 4)
        st.pop("note", None)
        e.db.set("strategist", st)
        self.status = "ok"
        self.say(f"Lagebericht ({', '.join(trigger)[:80]}): {memo['diagnosis']}")
        if sent:
            self.say(f"An den Think Tank: {', '.join(sent)}")
        e._notify_later(f"{memo['diagnosis']}\n\nAchten auf: {memo['watch']}"
                        + ("\n\nVorschlag: " + "; ".join(a["label"] for a in memo["actions"] if a["kind"] != "keep")
                           if any(a["kind"] != "keep" for a in memo["actions"]) else ""),
                        title="🧭 Stratege", tags=["compass"])
        self._show(st)
        return memo

    def _show(self, st: dict) -> None:
        m = st.get("memo") or {}
        self.summary = (st.get("note") or m.get("diagnosis") or "denkt beim nächsten Ereignis oder Sonntag 18:00")[:140]
        self.detail = {
            "did": ([f"Letzte Prüfung {time.strftime('%d.%m. %H:%M', time.localtime(m['ts']))}: " + ", ".join(m.get("trigger") or [])]
                    if m else ["noch keine Prüfung"]) + ([f"An den Think Tank: {', '.join(m['ideas'])}"] if m.get("ideas") else []),
            "assessment": m.get("diagnosis"), "watch": m.get("watch"), "idea": m.get("lab_test"),
            "facts": [["Kosten bisher", f"${st.get('cost', 0):.2f}"], ["Wartende Anlässe", ", ".join(st.get("pending") or []) or "–"]],
        }
