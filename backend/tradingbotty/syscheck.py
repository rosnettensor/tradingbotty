"""The system check: is every link of the real-money chain working right now, and a small real round trip that
proves it (buy a little Bitcoin, sell it again at once). So nobody has to wait days to find out that something is off.

Each row: {id, state (ok | warn | fail | info), title, detail, fix}. "fail" means the bot can't trade real money
at all; "warn" means something it would need is missing or stale; "info" is a fact worth knowing (why it waits)."""
from __future__ import annotations

import asyncio
import time

from . import bank as bank_mod

TEST_COIN = "BTC"
TEST_MIN = 26.0           # Fusion's minimum is 25 (30 for some coins): a little above, so the sell clears it too
LOOPS = {                 # the loops the chain needs, in the order money moves
    "Engine._poll_wallet": "Konto lesen",
    "Engine.brain_tick": "Daily Brain",
    "FastTrader.tick": "Fast Pot (4 h)",
    "FastTrader.watch": "Fast Pot Live-Stopps",
    "Engine.guard_tick": "Guardian",
    "Engine.research_tick": "History Lab",
}


def _row(rid: str, state: str, title: str, detail: str, fix: str | None = None) -> dict:
    return {"id": rid, "state": state, "title": title, "detail": detail, "fix": fix}


def _ago(ts: float | None) -> str:
    if not ts:
        return "nie"
    s = time.time() - ts
    if s < 90:
        return f"vor {s:.0f} s"
    if s < 5400:
        return f"vor {s / 60:.0f} min"
    if s < 48 * 3600:
        return f"vor {s / 3600:.1f} h"
    return f"vor {s / 86400:.0f} Tagen"


def checks(e) -> dict:
    now = time.time()
    rows: list[dict] = []
    w = e.wallet or {}
    cur = w.get("currency") or "CHF"
    live = e.mode == "live"
    rows.append(_row("mode", "ok" if live else "fail", "Echtgeld-Modus", "LIVE: echte Orders gehen raus" if live
                     else "STANDBY: der Bot handelt gerade nichts", None if live else "Oben auf STANDBY tippen und REAL MONEY eingeben"))
    rows.append(_row("kill", "fail" if e.kill_switch else "ok", "Not-Aus", "AN: alle Orders gesperrt" if e.kill_switch
                     else "aus", "Not-Aus oben wieder lösen" if e.kill_switch else None))

    # the account
    if e.settings.simulate:
        rows.append(_row("fusion", "info", "Bitpanda Fusion", "Simulation: kein echtes Konto"))
    elif w.get("error"):
        rows.append(_row("fusion", "fail", "Bitpanda Fusion", f"nicht erreichbar: {str(w['error'])[:120]}",
                         "BITPANDA_FUSION_API_KEY in Render prüfen"))
    elif not w.get("ts"):
        rows.append(_row("fusion", "fail", "Bitpanda Fusion", "Konto noch nie gelesen", "BITPANDA_FUSION_API_KEY in Render setzen"))
    else:
        age = now - w["ts"]
        st = "ok" if age < 180 and not w.get("stale") else "warn"
        rows.append(_row("fusion", st, "Bitpanda Fusion", f"Konto {w.get('total', 0):.2f} {cur}, gelesen {_ago(w['ts'])}"
                         + (f" (letzter Versuch: {w['stale'][:60]})" if w.get("stale") else "")))
    trade_key = e.live is not None
    if live and not trade_key and not e.settings.simulate:
        rows.append(_row("trade_key", "fail", "Handels-Schlüssel", "der Bot ist live, aber ohne Verbindung zum Handeln",
                         "Seite neu laden; hilft das nicht, Render neu starten"))

    # money it can use
    fiat = float(w.get("fiat") or 0)
    reserve = e.portfolio.cash_reserve()
    free = max(0.0, fiat - reserve)
    st = "ok" if free >= TEST_MIN else "warn"
    rows.append(_row("cash", st, "Bargeld für Käufe", f"{fiat:.2f} {cur} Cash, davon {reserve:.2f} für Fast und Volatility reserviert"
                     + ("" if st == "ok" else f": unter dem Fusion-Minimum von 25 {cur}")
                     + (" · der Daily Brain darf deine Coins verkaufen, wenn Cash fehlt" if e.settings["live"]["use_my_coins"] else ""),
                     None if st == "ok" else "Geld einzahlen oder in Controls erlauben, dass der Bot deine Coins nutzt"))
    if e.settings["live"]["max_order"] < 25:
        rows.append(_row("max_order", "warn", "Grösste Order", f"{e.settings['live']['max_order']:.0f} {cur}: unter dem Fusion-Minimum",
                         "In Controls die grösste Order auf mindestens 30 setzen"))

    # the loops
    beats = e.__dict__.get("beats") or {}
    dead = []
    for fn, name in LOOPS.items():
        b = beats.get(fn)
        if not b:
            dead.append(f"{name} (noch nie)")
        elif now - b[0] > max(3 * b[1], 180) + 60:
            dead.append(f"{name} ({_ago(b[0])})")
    up = now - e.started
    if dead and up > 600:
        rows.append(_row("loops", "fail", "Herzschlag", "stehen: " + ", ".join(dead), "Render neu starten (Manual Deploy)"))
    else:
        rows.append(_row("loops", "ok" if not dead else "info", "Herzschlag",
                         "alle Schleifen laufen" if not dead else f"Bot startet gerade ({up / 60:.0f} min)"))

    # the daily brain
    b = e.brain()
    if not b.get("on") or not b.get("strategy"):
        rows.append(_row("brain", "warn", "Daily Brain", "aus: kauft nichts", "Research: eine Strategie live schalten"))
    else:
        rows.append(_row("brain", "ok", "Daily Brain", f"{b['strategy'][:60]} · letzte Entscheidung {_ago(b.get('ts'))}"
                         + f" · hält {', '.join(b.get('target') or {}) or 'nichts'}"))
        if b.get("btc_ok") is False:
            rows.append(_row("btc", "info", "Bitcoin-Filter", "Bitcoin liegt unter seinem Schnitt: der Brain wartet absichtlich "
                             "in Cash (so verlor er 2022 viel weniger). Probe und Fast Pot dürfen trotzdem handeln."))
        elif b.get("btc_ok"):
            rows.append(_row("btc", "ok", "Bitcoin-Filter", "Käufe erlaubt"))
    st = e.stance.get()
    if st["key"] == "pause":
        rows.append(_row("course", "warn", "Dein Kurs", "⏸ Pause: keine neuen Käufe", "Im Cockpit den Kurs beenden"))
    elif st["key"] != "normal":
        rows.append(_row("course", "info", "Dein Kurs", st.get('name', st['key'])))

    # the lab and the bank
    res = e.db.get("research") or {}
    names = {r["name"] for r in res.get("rows") or []}
    missing = [n for n in bank_mod.NEW if n not in names]
    if not res.get("ts"):
        rows.append(_row("lab", "warn", "History Lab", "noch kein Lauf", "Läuft von selbst; braucht die Tagesdaten"))
    else:
        rows.append(_row("lab", "ok" if not missing and now - res["ts"] < 36 * 3600 else "warn", "History Lab",
                         f"Lauf {_ago(res['ts'])}, {sum(1 for r in res.get('rows') or [] if r.get('robust'))} robust"
                         + (f" · {len(missing)} neue Strategien noch nicht getestet (läuft gleich)" if missing else "")))
    bk = e.bank.cfg()
    rep = bk.get("report") or {}
    if not rep:
        rows.append(_row("bank", "info", "Bank & Prüfer", "rechnet nach dem nächsten Labor-Lauf"))
    else:
        mode = bk["mode"]
        probe = rep.get("probe")
        txt = {"shadow": "Schatten: zeigt nur", "probe": "PROBE: kleiner echter Anteil", "live": "LIVE"}[mode]
        if mode == "probe":
            st = "ok" if probe else "warn"
            txt += f" für {probe}" if probe else ": gerade kein Kandidat hat das Labor bestanden"
        else:
            st = "info"
            txt += f" · Probe-Kandidat: {probe}" if probe else " · kein Kandidat besteht das Labor (Probe hätte nichts zu testen)"
        rows.append(_row("bank", st, "Bank & Prüfer", txt))

    # the fast pot
    f = e.fast.status()
    if not f["on"]:
        rows.append(_row("fast", "warn" if not f.get("stopped") else "info", "Fast Pot",
                         "aus" + (f" (gestoppt: {f['stopped']})" if f.get("stopped") else ""), "Im Cockpit: Fast Pot einschalten"))
    else:
        c = e.fast.cfg()
        bar = c.get("bar")
        late = bar and now - bar > 9 * 3600
        rows.append(_row("fast", "warn" if late else "ok", "Fast Pot",
                         f"{f['pot']:.0f} {cur} Topf · {len(f['pos'])} Coin(s) · letzte 4-h-Prüfung {_ago((bar or 0) + 4 * 3600)}"
                         + (" · Ziel und Stopp jede Minute" if c.get("live_exits", True) else " · Live-Stopps aus")))

    # guards and helpers
    g = e.guard()
    if g:
        rows.append(_row("guard", "info", "Guardian", f"{len(g)} Coin(s) gesperrt: " + ", ".join(sorted(g))[:120]))
    if not e.llm.available:
        rows.append(_row("ai", "info", "KI", "kein Schlüssel: alles läuft mit reiner Mathematik weiter"))
    else:
        bs = e.budget.snapshot()
        left = bs["cap_total"] - bs["spent_total"]
        rows.append(_row("ai", "ok" if left > 0.5 else "warn", "KI-Budget", f"{left:.2f} USD übrig von {bs['cap_total']:.2f}"))
    ch = e.phone_channels()
    rows.append(_row("phone", "ok" if ch else "info", "Handy-Push", ", ".join(ch) if ch else "kein Kanal eingerichtet"))

    daily_proofs = e.strategies.checks("brain")
    entry_checks = {
        "daily": next((p for p in daily_proofs if not p["ready"]), daily_proofs[0] if daily_proofs else
                      {"ready": False, "reason": "Die Bank hält Cash", "strategy": "Bank", "ts": None}),
        "fast": e.strategies.checks("fast")[0],
    }
    for key, label, enabled in (("daily", "Daily Brain", b.get("on")), ("fast", "Fast Pot", f["on"])):
        proof = entry_checks[key]
        rows.append(_row(key + "_evidence", "ok" if proof["ready"] else "warn" if enabled else "info",
                         label + " · Kaufprüfung", proof["reason"],
                         None if proof["ready"] else "Research: Labor mit Marktdaten ausführen und robuste Strategie wählen"))
    pilot = e.volatility.status()
    rows.append(_row("volatility", "warn" if pilot["enabled"] and pilot["reasons"] else "info",
                     "Volatility · begrenzter Echtgeld-Pilot",
                     "; ".join(pilot["reasons"]) or "Freigegeben; jede Order braucht Signal- und Risikoprüfung"))
    if e.db.get("unresolved_order"):
        rows.append(_row("unresolved", "fail", "Ungeklärte Order", "Eine Order hat noch keinen sicher verbuchten Abschluss",
                         "Order und Kontobestand in Fusion abgleichen; keinen weiteren Kauf starten"))

    # proof: the last real order, the last test
    last = e.db.query("SELECT MAX(ts) t FROM trades WHERE mode='live'")[0]["t"]
    rows.append(_row("last_trade", "info", "Letzte echte Order", _ago(last)))
    t = e.db.get("testtrade") or {}
    if t:
        rows.append(_row("test", "ok" if t.get("ok") else "fail", "Testtrade",
                         f"{_ago(t['ts'])}: {t.get('summary', '')}", None if t.get("ok") else "Unten nochmals testen"))
    blocking = [r for r in rows if r["state"] == "fail"]
    return {"ts": now, "rows": rows, "overall": "fail" if blocking else "warn" if any(r["state"] == "warn" for r in rows) else "ok",
            "entry_checks": entry_checks,
            "trade_ready": not blocking and not e.settings.simulate and free >= test_amount(e) * 1.005
                           and e.settings["live"]["max_order"] >= test_amount(e), "test": t, "test_amount": test_amount(e), "currency": cur}


def test_amount(e) -> float:
    return round(max(TEST_MIN, e._min_order(TEST_COIN) * 1.05), 2)


async def roundtrip(e) -> dict:
    """Buy a little Bitcoin and sell exactly that again, through the same Risk Officer and Live Desk as every real
    order. Costs the two fees and the spread (about 0.15 to 0.30 on 26). Booked as its own "test" book: it never
    counts for the Daily Brain, the fast pot or the scoreboard."""
    if e.mode != "live" or not e.live:
        raise ValueError("Der Bot ist nicht LIVE: zuerst Echtgeld einschalten")
    if e.kill_switch:
        raise ValueError("Der Not-Aus ist an")
    if e.__dict__.get("_brain_busy") or e.__dict__.get("_fast_trading"):
        raise ValueError("Gerade läuft eine andere Order: in einer Minute nochmals")
    cur = e.live.currency
    amount = test_amount(e)
    t0 = time.time()
    steps: list[str] = []
    e._fast_trading = True  # one order at a time; the account's deposit check waits too
    try:
        left = e.db.get("test_qty", {})
        if left.get(TEST_COIN):  # a test whose sell failed last time: sell that first
            await e._live_sell(TEST_COIN, "Systemcheck: Rest vom letzten Test verkauft", book="test")
            steps.append("Rest vom letzten Test verkauft")
        e.why("test", TEST_COIN, "BUY", [["info", f"Systemcheck: {amount:.2f} {cur} {TEST_COIN} kaufen und sofort wieder verkaufen, "
                                                  "um die ganze Kette mit echtem Geld zu prüfen"]])
        try:
            spent, got = await e._live_buy(TEST_COIN, amount, "Systemcheck: Testkauf, wird gleich wieder verkauft", book="test")
        except Exception as ex:
            out = {"ts": time.time(), "ok": False, "summary": f"Kauf abgelehnt: {str(ex)[:160]}", "steps": steps}
            e.db.set("testtrade", out)
            e._log("Live Desk", "warn", f"System check: the test buy failed ({str(ex)[:120]}).")
            return out
        steps.append(f"gekauft: {spent:.2f} {cur} → {got:.8f} {TEST_COIN}")
        await asyncio.sleep(2)
        e.why("test", TEST_COIN, "SELL", [["info", "Systemcheck: der Testkauf wird sofort wieder verkauft"]])
        ex = await e._live_sell(TEST_COIN, "Systemcheck: Testverkauf", book="test")
        if not ex:
            out = {"ts": time.time(), "ok": False, "spent": spent, "steps": steps,
                   "summary": f"gekauft für {spent:.2f} {cur}, aber der Verkauf ging nicht durch: der Bot versucht es beim nächsten Test"}
            e.db.set("testtrade", out)
            return out
        back = float(ex.get("notional", 0) or 0) - float(ex.get("fee", 0) or 0)
        entry = next((d for d in e.diary(5) if d.get("book") == "test" and d.get("side") == "SELL"), {})
        cost = -float(entry.get("pnl", back - spent))
        secs = time.time() - t0
        steps.append(f"verkauft: {back:.2f} {cur} zurück")
        out = {"ts": time.time(), "ok": True, "spent": round(spent, 2), "back": round(back, 2), "cost": round(cost, 2),
               "seconds": round(secs, 1), "steps": steps,
               "summary": f"gekauft {spent:.2f}, verkauft {back:.2f} {cur} in {secs:.0f} s: die Kette funktioniert "
                          f"(Kosten {cost:.2f} {cur} für Gebühren und Spread)"}
        e.db.set("testtrade", out)
        e._log("Live Desk", "live", f"System check passed: test round trip {spent:.2f} → {back:.2f} {cur} in {secs:.0f}s.")
        return out
    finally:
        e._fast_trading = False
