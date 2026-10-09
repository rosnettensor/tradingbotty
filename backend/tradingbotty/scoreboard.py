"""Bauplan 2, phase 1: what each agent is worth in your currency, why each real trade happened, and ghost trades.

- Why: every real order carries the facts that made it (the signal's numbers, the Bitcoin filter, the market mood,
  your course, the Risk Officer's checks). A sell also carries the facts of its buy, so a closed trade reads as one
  story. The diary's AI line is told to stick to these facts.
- Ghosts: whenever an agent (or you) stops a trade the rules wanted (a blocked buy) or ends one early (a forced
  sell), the bot follows that coin as if the trade had happened: same amount, fees included, held like the rule
  would (the fast pot's take/stop, or a week for the daily brain). When the ghost ends, the agent that stopped it is
  credited what it saved, or debited what it cost. Ghosts never trade.
- Scoreboard: the traders get their real result after fees (and how it compares with simply holding Bitcoin over
  the same days); the guards get their ghosts; AI agents pay for their own API calls. One number per agent.
- Loss check: average win against average loss, the hit rate the rules need, and where the losses came from.
"""
from __future__ import annotations

import time

from . import research

FEE = 0.005           # a round trip on Fusion: about 0.25% per side
GHOST_MAX = 300
BRAIN_DAYS = 7        # a blocked daily-brain trade is followed for a week (a typical breakout holds days to weeks)

BLOCKERS = {
    "guardian": ("🛡", "Guardian", "Gericht", "Sperrt Coins bei Hack- oder Delisting-News; zahlt den News Hunter."),
    "professor": ("🎓", "Professor", "Gericht", "Opus-Bericht, darf Käufe 24 h sperren; zahlt seine KI-Kosten."),
    "risk": ("⚖️", "Risk Officer", "Gericht", "Harte Grenzen vor jeder Order (Spread, Minimum, Cash)."),
    "course": ("🧭", "Dein Kurs", "Du", "Pause und Bunkern: was blockierte Käufe und gesicherte Gewinne brachten."),
    "you": ("🙋", "Du (Chat)", "Du", "Deine Chat-Käufe, und was deine Chat-Verkäufe gegenüber Halten brachten."),
}


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


# ------------------------------------------------------------------ why: the facts behind an order
def _safe(fn):
    """The facts are a nice-to-have: a gap in the data must never stop a real order or a decision."""
    def wrapped(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as ex:
            return [["info", f"Fakten nicht lesbar ({type(ex).__name__})"]]
    wrapped.__name__, wrapped.__doc__ = fn.__name__, fn.__doc__
    return wrapped


def _donchian(strat) -> research.Donchian | None:
    if isinstance(strat, research.Donchian):
        return strat
    for part in (getattr(strat, "a", None), getattr(strat, "b", None)):
        if isinstance(part, research.Donchian):
            return part
    return None


def btc_filter_line(cd, i: int, days: int | None) -> list[str] | None:
    xs = cd.closes("BTC", i, days) if days else None
    if not xs:
        return None
    gap = xs[-1] / research.sma(xs) - 1
    return ["ok" if gap > 0 else "no", f"BTC-Filter: Bitcoin {_pct(gap)} zum {days}-Tage-Schnitt"
            + ("" if gap > 0 else " (darunter: keine neuen Käufe, alles in Cash)")]


@_safe
def brain_why(cd, strat, sym: str, side: str) -> list[list[str]]:
    """The daily rule's own numbers for this coin on the day it decided."""
    i = len(cd.days) - 1
    d = _donchian(strat)
    c = (cd.c.get(sym) or [None])[i] if sym in cd.c else None
    lines: list[list[str]] = [["info", f"Strategie: {strat.name}"]]
    if not d or c is None:
        return lines
    highs = [x for x in cd.h[sym][i - d.entry:i] if x is not None]
    lows = [x for x in cd.l[sym][i - d.exit:i] if x is not None]
    a = research.atr(cd, sym, i)
    if side == "BUY" and highs:
        lines.append(["ok", f"Signal: Schluss {c:.6g} über dem {d.entry}-Tage-Hoch {max(highs):.6g} "
                            f"({_pct(c / max(highs) - 1)})"])
        if lows:
            lines.append(["info", f"Ausstieg, wenn er unter das {d.exit}-Tage-Tief fällt: heute {min(lows):.6g} "
                                  f"({_pct(min(lows) / c - 1)} von hier)"])
    if side == "SELL":
        btc_off = d.regime and not research.btc_uptrend(cd, i, d.regime)
        if lows and c < min(lows):
            lines.append(["no", f"Ausstieg: Schluss {c:.6g} unter dem {d.exit}-Tage-Tief {min(lows):.6g}"])
        elif not btc_off:
            lines.append(["no", f"Ausstieg: Nachlauf-Stopp, {d.atr_mult:g}× die Tagesschwankung"
                                + (f" ({a:.4g})" if a else "") + " unter dem Höchststand seit dem Kauf"])
        if btc_off:
            lines.append(["no", "Ausstieg: Bitcoin fiel unter seinen Schnitt, die Regel geht ganz in Cash"])
    line = btc_filter_line(cd, i, d.regime)
    if line:
        lines.append(line)
    return lines


@_safe
def fast_why(cd, strat, sym: str, i: int, side: str, entry: float | None = None, since: float | None = None) -> list[list[str]]:
    """The 4-hour rule's numbers: for the dip buyer how far it fell and whether its trend still holds."""
    lines: list[list[str]] = [["info", f"Regel: {strat.name}"]]
    xs = cd.closes(sym, i, getattr(strat, "trend", 120)) if sym in cd.c else None
    if not xs:
        return lines
    c = xs[-1]
    if side == "BUY":
        if len(xs) >= 7 and xs[-7]:
            lines.append(["ok", f"Signal: {_pct(c / xs[-7] - 1)} in einem Tag"])
        avg = sum(xs) / len(xs)
        lines.append(["ok" if c > avg else "warn", f"Trend: {_pct(c / avg - 1)} zum {len(xs) // 6}-Tage-Schnitt"])
        tp, stop = getattr(strat, "tp", None), getattr(strat, "stop", None)
        if tp and stop:
            lines.append(["info", f"Verkauf bei {c * (1 + tp):.6g} (+{tp * 100:.0f}%) oder {c * (1 - stop):.6g} "
                                  f"(−{stop * 100:.0f}%), geprüft alle 4 Stunden"])
    elif entry:
        lines.append(["no" if c < entry else "ok", f"Kurs {c:.6g} gegen Einstieg {entry:.6g}: {_pct(c / entry - 1)}"
                      + (f" nach {(time.time() - since) / 3600:.0f} h" if since else "")])
        stop = getattr(strat, "stop", None)
        if stop and c < entry * (1 - stop):
            lines.append(["warn", f"Unter dem Stopp (−{stop * 100:.0f}%): die Regel prüft nur am Ende jeder "
                                  f"4-Stunden-Kerze, darum kann der Verlust grösser sein"])
    regime = getattr(strat, "regime", None)
    if regime:
        line = btc_filter_line(cd, i, regime)
        if line:
            line[1] = line[1].replace(f"{regime}-Tage", f"{regime // 6}-Tage")
            lines.append(line)
    return lines


def context_why(e) -> list[list[str]]:
    """What every order shares: the market mood and your course."""
    out = []
    reg = e.db.get("regime") or {}
    if reg.get("name"):
        out.append(["info", f"Marktphase: {reg['name']}"
                    + (f" · BTC {_pct(reg['btc_vs_50'])} zum 50-Tage-Schnitt" if reg.get("btc_vs_50") is not None else "")])
    course = e.stance.get()
    if course["key"] != "normal":
        out.append(["warn", f"Dein Kurs: {course['name']} (Grösse {course['size']:g}×)"])
    return out


def checks_line(checks: list) -> list[str] | None:
    """The Risk Officer's checks on a buy as one line."""
    if not checks:
        return None
    bad = [c for c in checks if c[1] not in ("ok", "kept")]
    spread = next((c[2] for c in checks if c[0] == "spread"), None)
    text = f"Risk Officer: {len(checks) - len(bad)} von {len(checks)} Prüfungen ok"
    if spread:
        text += f" · Spread {spread.split(' ')[0]}"
    if bad:
        text += " · " + "; ".join(f"{c[0]}: {c[1]}" for c in bad)
    return ["warn" if bad else "ok", text]


# ------------------------------------------------------------------ ghost trades
class Ghosts:
    """kv "ghosts", newest first: {id, ts, book, symbol, blocker, kind (buy|keep), why, amount, price, until, tp,
    stop, last, closed, exit, pnl, credit}. credit is what the blocker earned: minus what the trade would have made."""

    def __init__(self, engine):
        self.e = engine

    def all(self) -> list[dict]:
        return self.e.db.get("ghosts") or []

    def add(self, book: str, sym: str, blocker: str, why: str, amount: float, kind: str = "buy",
            price: float | None = None, hours: float | None = None, tp: float | None = None,
            stop: float | None = None) -> dict | None:
        try:
            return self._add(book, sym, blocker, why, amount, kind, price, hours, tp, stop)
        except Exception as ex:  # following a ghost must never get in the way of a real decision
            self.e._log("Scoreboard", "warn", f"Ghost trade for {sym} not started: {str(ex)[:80]}")
            return None

    def _add(self, book, sym, blocker, why, amount, kind, price, hours, tp, stop) -> dict | None:
        price = price or (self.e.__dict__.get("fusion_prices") or {}).get(sym)
        if not price or amount <= 0 or blocker not in BLOCKERS:
            return None
        gs = self.all()
        if any(not g.get("closed") and g["symbol"] == sym and g["book"] == book and g["blocker"] == blocker
               for g in gs):
            return None  # already followed: the same veto every 5 minutes is one ghost
        now = time.time()
        if book == "fast" and hours is None:
            hours = 72
        g = {"id": f"{int(now * 1000)}-{book}-{sym}-{blocker}", "ts": now, "book": book, "symbol": sym,
             "blocker": blocker, "kind": kind, "why": why[:200], "amount": round(amount, 2), "price": price,
             "until": now + (hours or BRAIN_DAYS * 24) * 3600, "tp": tp, "stop": stop, "last": price,
             "closed": False}
        self.e.db.set("ghosts", [g, *gs][:GHOST_MAX])
        return g

    @staticmethod
    def result(g: dict, px: float) -> float:
        """What the trade would have made at price px: a blocked buy pays the round-trip fee, a kept coin doesn't
        (its sell fee is due either way)."""
        gross = g["amount"] * (px / g["price"] - 1)
        return gross - (g["amount"] * FEE if g["kind"] == "buy" else 0.0)

    async def tick(self) -> None:
        """Every 5 minutes: move each open ghost to the live price and end it where the rule would have."""
        gs = self.all()
        if not any(not g.get("closed") for g in gs):
            return
        prices = self.e.__dict__.get("fusion_prices") or {}
        if not prices and self.e.live and hasattr(self.e.live, "prices"):
            try:
                prices = await self.e.live.prices()
            except Exception:
                return
        now = time.time()
        for g in gs:
            if g.get("closed"):
                continue
            px = prices.get(g["symbol"])
            if not px:
                if now > g["until"] + 86400:  # coin gone from Fusion: close at the last known price
                    px = g["last"]
                else:
                    continue
            g["last"] = px
            r = px / g["price"]
            ended = now >= g["until"] or (g.get("tp") and r >= 1 + g["tp"]) or (g.get("stop") and r <= 1 - g["stop"])
            pnl = self.result(g, px)
            g["pnl"], g["credit"] = round(pnl, 2), round(-pnl, 2)
            if ended:
                g.update(closed=True, exit=px, ended=now)
                icon, name = BLOCKERS[g["blocker"]][:2]
                verb = "sparte" if pnl < 0 else "kostete"
                self.e._log("Scoreboard", "info", f"👻 Geister-Trade {g['symbol']} beendet: {name} {verb} "
                                                  f"{abs(pnl):.2f} ({_pct(r - 1)} seit {g['why'][:80]}).")
        self.e.db.set("ghosts", gs)


# ------------------------------------------------------------------ scoreboard and loss check
def _by_chat(d: dict) -> bool:
    return "by you (chat)" in (d.get("entry_reason") or "")


def exit_kind(reason: str) -> str:
    r = (reason or "").lower()
    for key, word in (("chat", "by you (chat)"), ("guardian", "guardian"), ("bunker", "bunkern"),
                      ("take", "take profit"), ("stop", "stop"), ("time", "time or exit"), ("rule", "no longer holds")):
        if word in r:
            return key
    return "other"


EXIT_NAMES = {"chat": "von dir per Chat verkauft", "guardian": "vom Guardian verkauft", "bunker": "Bunkern-Sicherung",
              "take": "Gewinnziel erreicht", "stop": "Stopp gegriffen", "time": "Zeit abgelaufen",
              "rule": "Regel-Ausstieg (Tief oder BTC-Filter)", "other": "anderes"}


def losses(closed: list[dict]) -> dict:
    """Average win against average loss, and the hit rate those two need to break even."""
    n = len(closed)
    if not n:
        return {"n": 0, "verdict": "Noch keine abgeschlossenen Trades."}
    wins = [d["pnl_pct"] for d in closed if d["pnl"] > 0]
    lost = [d["pnl_pct"] for d in closed if d["pnl"] <= 0]
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(lost) / len(lost) if lost else 0.0
    hit = len(wins) / n
    need = abs(al) / (aw + abs(al)) if wins and lost and aw + abs(al) > 0 else None
    kinds: dict[str, dict] = {}
    for d in closed:
        k = kinds.setdefault(exit_kind(d.get("reason", "")), {"n": 0, "pnl": 0.0})
        k["n"] += 1
        k["pnl"] = round(k["pnl"] + d["pnl"], 2)
    held = [(d["ts"] - d["entry_ts"]) / 3600 for d in closed if d.get("entry_ts")]
    out = {"n": n, "won": len(wins), "lost": len(lost), "hit_pct": round(hit * 100), "avg_win_pct": round(aw, 2),
           "avg_loss_pct": round(al, 2), "need_pct": round(need * 100) if need is not None else None,
           "expect_pct": round(hit * aw + (1 - hit) * al, 2), "total": round(sum(d["pnl"] for d in closed), 2),
           "avg_hold_h": round(sum(held) / len(held)) if held else None,
           "by_exit": [{"kind": k, "name": EXIT_NAMES[k], **v} for k, v in sorted(kinds.items(), key=lambda kv: kv[1]["pnl"])]}
    bits = []
    if wins and lost:
        bits.append(f"Ø Gewinn {aw:+.1f}%, Ø Verlust {al:+.1f}%: mit diesem Verhältnis braucht es über {out['need_pct']}% "
                    f"Treffer, aktuell {out['hit_pct']}%.")
    elif lost:
        bits.append(f"Alle {n} Trades im Minus, Ø {al:+.1f}%.")
    else:
        bits.append(f"Alle {n} Trades im Plus, Ø {aw:+.1f}%.")
    if n < 20:
        bits.append(f"Erst {n} Trades: für ein Urteil braucht es etwa 20 oder mehr.")
    out["verdict"] = " ".join(bits)
    return out


def usd_to(e) -> float:
    """How many of your currency one US dollar buys, read from Bitcoin's price in both (about 0.80 for CHF)."""
    chf = (e.__dict__.get("fusion_prices") or {}).get("BTC")
    usd = e.prices.price("BTC") if getattr(e, "prices", None) else 0.0
    if chf and usd and 0.3 < chf / usd < 3:
        return chf / usd
    return 0.80


def ai_costs(e) -> dict[str, float]:
    rows = e.db.query("SELECT agent, COALESCE(SUM(cost_usd),0) s FROM llm_calls GROUP BY agent")
    return {r["agent"]: float(r["s"] or 0) for r in rows}


def board(e) -> dict:
    """One row per agent with its score in your currency, newest facts first."""
    w = e.wallet or {}
    cur = w.get("currency") or (e.live.currency if e.live else "CHF")
    closed = [d for d in (e.db.get("diary") or []) if d.get("closed")]
    ghosts = Ghosts(e).all()
    fx = usd_to(e)
    costs = ai_costs(e)
    rows = []

    def ai_of(*words) -> tuple[float, list[str]]:
        names = [a for a in costs if any(wd in a.lower() for wd in words)]
        return sum(costs[a] for a in names) * fx, names

    for book, icon, name, holds in (("brain", "🧠", "Daily Brain", w.get("coins") or []),
                                    ("fast", "⚡", "Fast Pot", w.get("fast_coins") or [])):
        cl = [d for d in closed if d["book"] == book and not _by_chat(d)]
        realized = sum(d["pnl"] for d in cl)
        open_pnl = sum(c["pnl"] for c in holds if c.get("pnl") is not None)
        bench = [d for d in cl if d.get("btc_in") and d.get("btc_out")]
        vs_btc = (sum(d["pnl"] for d in bench) - sum(d["cost"] * (d["btc_out"] / d["btc_in"] - 1) for d in bench)
                  if bench else None)
        detail = f"{len(cl)} abgeschlossen · {sum(d['pnl'] > 0 for d in cl)} gewonnen"
        by_you = [d for d in cl if exit_kind(d.get("reason", "")) == "chat"]
        if by_you:
            detail += f" · davon {len(by_you)} per Chat verkauft ({sum(d['pnl'] for d in by_you):+.2f})"
        if holds:
            detail += f" · offen {open_pnl:+.2f}"
        rows.append({"id": book, "icon": icon, "name": name, "role": "Regierung",
                     "what": "Echter Gewinn nach Gebühren, offene Coins zum heutigen Kurs.",
                     "score": round(realized + open_pnl, 2), "realized": round(realized, 2), "open": round(open_pnl, 2),
                     "detail": detail, "vs_btc": round(vs_btc, 2) if vs_btc is not None else None,
                     "vs_btc_n": len(bench), "trades": len(cl)})

    for key, (icon, name, role, what) in BLOCKERS.items():
        mine = [g for g in ghosts if g["blocker"] == key]
        done = sum(g.get("credit", 0.0) for g in mine if g.get("closed"))
        running = sum(g.get("credit", 0.0) for g in mine if not g.get("closed"))
        extra, cost, names = 0.0, 0.0, []
        if key == "you":
            chat = [d for d in closed if _by_chat(d)]
            extra = sum(d["pnl"] for d in chat)
        if key == "guardian":
            cost, names = ai_of("news")
        if key == "professor":
            cost, names = ai_of("professor")
        if not mine and not extra and not cost and key in ("risk", "course"):
            detail = "noch nichts blockiert"
        else:
            parts = [f"{len(mine)} Geister-Trade{'s' if len(mine) != 1 else ''}"]
            if running:
                parts.append(f"laufend {running:+.2f}")
            if extra:
                parts.append(f"Chat-Käufe {extra:+.2f}")
            if cost:
                parts.append(f"KI {-cost:.2f} ({', '.join(names)})")
            detail = " · ".join(parts)
        rows.append({"id": key, "icon": icon, "name": name, "role": role, "what": what,
                     "score": round(done + running + extra - cost, 2), "realized": round(done + extra - cost, 2),
                     "open": round(running, 2), "detail": detail, "ghosts": len(mine)})

    taken = {a for a in costs if any(wd in a.lower() for wd in ("news", "professor"))}
    for agent, usd in sorted(costs.items(), key=lambda kv: -kv[1]):
        if agent in taken or usd <= 0:
            continue
        thinker = "think" in agent.lower() or "research" in agent.lower()
        rows.append({"id": f"ai:{agent}", "icon": "💡" if thinker else "🤖", "name": agent,
                     "role": "Parlament" if thinker else "Dienste",
                     "what": ("Erfindet Ideen; Punkte gibt es erst, wenn eine Idee echtes Geld verdient (Phase 2)."
                              if thinker else "Erklärt und beantwortet; zahlt nur seine KI-Kosten."),
                     "score": round(-usd * fx, 2), "realized": round(-usd * fx, 2), "open": 0.0,
                     "detail": f"KI-Kosten {usd:.2f} USD"})

    rows.sort(key=lambda r: -r["score"])
    return {"currency": cur, "rows": rows, "losses": losses(closed),
            "ghosts": [{**g, "blocker_name": BLOCKERS[g["blocker"]][1]} for g in ghosts[:40]], "fx": round(fx, 3), "ts": time.time(),
            "note": "Händler: echter Gewinn nach Gebühren. Wächter und du: was gestoppte Trades gebracht hätten "
                    "(Geister-Trades, mit Gebühren). KI-Agenten zahlen ihre eigenen API-Kosten."}
