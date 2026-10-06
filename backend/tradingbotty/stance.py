"""The course ("Kurs") you set for a while: Mutig, Bunkern, Pause or Normal.

It never changes WHAT the bot buys or sells (the tested signals stay), only how big new buys are and how early
gains are locked in:
- Mutig: new buys 1.5x as big, never above your limits (the cap in Controls, the fast pot's size);
- Bunkern: new buys half as big, and gains are locked: once a coin is +10% over what the bot paid, it is sold if it
  falls back to +2% (the "lock" tested in the History Lab's "Trend + profit taking" group);
- Pause: no new buys from the daily brain or the fast pot; coins they hold still follow their rules;
- Normal: as tested.
A course lasts 24 hours (or until midnight, or the hours you say) and then goes back to Normal by itself.
"""
from __future__ import annotations

import re
import time

STANCES = {
    "normal": {"name": "Normal", "size": 1.0, "buys": True, "lock": False,
               "what": "Alles wie getestet: normale Grössen, die Regeln entscheiden."},
    "bold": {"name": "🔥 Mutig", "size": 1.5, "buys": True, "lock": False,
             "what": "Neue Käufe 1.5x so gross, nie über deine Limits (Cap in Controls, Grösse des Fast Pots)."},
    "bunker": {"name": "🏦 Bunkern", "size": 0.5, "buys": True, "lock": True,
               "what": "Neue Käufe halb so gross, und Gewinne werden gesichert: liegt ein Coin +10% im Plus, "
                       "verkauft der Bot ihn, wenn er auf +2% zurückfällt."},
    "pause": {"name": "⏸ Pause", "size": 0.0, "buys": False, "lock": False,
              "what": "Keine neuen Käufe von Daily Brain und Fast Pot. Was sie halten, läuft nach ihren Regeln weiter."},
}
LOCK_AT, LOCK_FLOOR = 1.10, 1.02

_WHICH = [
    ("pause", re.compile(r"\bpause\b|keine (neuen )?käufe|nichts (mehr )?kaufen|stopp?e? (die |alle )?käufe|"
                         r"\bno (new )?buys\b")),
    ("bunker", re.compile(r"bunker|gewinne? (\w+ )?(sichern|mitnehmen|retten)|sicher spielen|vorsichtig|defensiv|"
                          r"lock (in )?(the )?(gains|profits)|play (it )?safe|gewinn\w* .*\bcash\b")),
    ("bold", re.compile(r"mutig|mehr risiko|risiko (rauf|hoch|erhöhen)|grösser\w* trades|größer\w* trades|"
                        r"aggressiv|vollgas|\byolo\b|more risk|bigger trades|\bbold\b")),
    ("normal", re.compile(r"\bnormal\b|\bstandard\b|kurs (beenden|aus|zurück)|back to normal")),
]
def swiss(ts: float) -> str:
    """dd.mm. HH:MM in Swiss time (the server runs on UTC)."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(ts, ZoneInfo("Europe/Zurich")).strftime("%d.%m. %H:%M")
    except Exception:
        return time.strftime("%d.%m. %H:%M UTC", time.gmtime(ts))


_ASK = re.compile(r"^\s*(soll|sollte|würdest|wäre|lohnt|warum|wieso|was|wie|welche\w*|should|would|why|what|how|"
                  r"which)\b")
_HOURS = re.compile(r"(\d{1,3})\s*(h\b|std|stunden|hours?)")
_TODAY = re.compile(r"\b(heute|today|heut)\b")


def parse(text: str) -> dict | None:
    """The course a chat message asks for, or None. Questions are questions."""
    t = " ".join(text.lower().split())
    if not t or t.endswith("?") or _ASK.search(t):
        return None
    key = next((k for k, rx in _WHICH if rx.search(t)), None)
    if not key:
        return None
    m = _HOURS.search(t)
    hours = min(168, max(1, int(m.group(1)))) if m else None
    return {"stance": key, "hours": hours, "today": bool(_TODAY.search(t)) and not m}


def until_for(p: dict, now: float | None = None) -> float | None:
    """When the course ends: the hours you said, tonight at midnight (Swiss time) for "heute", else 24 hours."""
    now = now or time.time()
    if p["stance"] == "normal":
        return None
    if p.get("hours"):
        return now + p["hours"] * 3600
    if p.get("today"):
        try:
            from datetime import datetime, timedelta
            from zoneinfo import ZoneInfo
            z = ZoneInfo("Europe/Zurich")
            d = datetime.fromtimestamp(now, z)
            return (d.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
        except Exception:
            pass
    return now + 24 * 3600


class Stance:
    """Kept in kv "stance": {"key", "since", "until", "peaks": {book:symbol: best value/cost seen}}."""

    def __init__(self, engine):
        self.e = engine

    def get(self) -> dict:
        s = self.e.db.get("stance") or {}
        key = s.get("key", "normal")
        if key != "normal" and s.get("until") and s["until"] < time.time():
            key = "normal"  # ran out; the tick says so and stores it
        info = STANCES.get(key, STANCES["normal"])
        return {"key": key, **info, "since": s.get("since"), "until": s.get("until") if key != "normal" else None}

    def size(self) -> float:
        return self.get()["size"]

    def buys_allowed(self) -> bool:
        return self.get()["buys"]

    def locked(self) -> set[str]:
        """Coins Bunkern sold to lock a gain: not bought again while the course lasts."""
        s = self.e.db.get("stance") or {}
        return set(s.get("locked") or []) if self.get()["key"] == s.get("key") else set()

    def set(self, key: str, until: float | None = None, by: str = "you") -> dict:
        if key not in STANCES:
            raise ValueError(f"unknown course {key}")
        now = time.time()
        until = None if key == "normal" else (until or now + 24 * 3600)
        self.e.db.set("stance", {"key": key, "since": now, "until": until, "peaks": {}})
        info = STANCES[key]
        end = f" until {swiss(until)}" if until else ""
        self.e._log("Risk Officer", "info", f"Course set by {by}: {info['name']}{end}. {info['what']}")
        if key != "normal":
            self.e._notify_later(f"{info['what']}\n\nGilt bis {swiss(until)}, "
                                 "dann wieder Normal.", title=f"Kurs: {info['name']}", tags=["compass"])
        return self.get()

    async def tick(self) -> None:
        """Every minute: end a course that ran out, and in Bunkern lock in gains of both traders' coins."""
        e = self.e
        s = e.db.get("stance") or {}
        if s.get("key", "normal") != "normal" and s.get("until") and s["until"] < time.time():
            e.db.set("stance", {"key": "normal", "since": time.time(), "until": None, "peaks": {}})
            e._log("Risk Officer", "info", f"Course {STANCES.get(s['key'], {}).get('name', s['key'])} ran out: back to Normal.")
            e._notify_later("Der Kurs ist abgelaufen: der Bot handelt wieder normal.", title="Kurs: Normal",
                            tags=["compass"])
            return
        if not STANCES.get(s.get("key"), {}).get("lock"):
            return
        if e.mode != "live" or not e.live or e.kill_switch or e.__dict__.get("_fast_trading"):
            return
        w = e.wallet or {}
        peaks = dict(s.get("peaks") or {})
        sells = []
        for book, rows in (("brain", w.get("coins") or []), ("fast", w.get("fast_coins") or [])):
            for c in rows:
                if not c.get("price") or not c.get("cost") or not c.get("value"):
                    continue
                k = f"{book}:{c['symbol']}"
                r = c["value"] / c["cost"]
                peaks[k] = max(peaks.get(k, 0.0), r)
                if peaks[k] >= LOCK_AT and r <= LOCK_FLOOR:
                    sells.append((book, c["symbol"], peaks[k], r))
        s["peaks"] = peaks
        e.db.set("stance", s)
        if not sells:
            return
        e._fast_trading = True  # the fast pot's own decision waits
        try:
            for book, sym, peak, r in sells:
                who = "fast pot" if book == "fast" else "daily brain"
                ex = await e._live_sell(sym, f"{who}: Bunkern locked the gain (was +{(peak - 1) * 100:.0f}%, "
                                             f"now +{(r - 1) * 100:.1f}%)", book=book)
                if ex and book == "fast":
                    e.fast.booked_sell(sym, ex)
                s = e.db.get("stance") or {}
                (s.get("peaks") or {}).pop(f"{book}:{sym}", None)
                if ex:
                    s["locked"] = sorted(set(s.get("locked") or []) | {sym})
                e.db.set("stance", s)
        finally:
            e._fast_trading = False
