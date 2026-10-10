"""Read-only operational truth: execution permission is distinct from strategy readiness."""
from __future__ import annotations

import time

from .readiness import evidence

WINDOW = 12 * 3600


def snapshot(e, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    w, b, f = e.wallet or {}, e.brain(), e.fast.cfg()
    cfg = e.settings["live"]
    cur = cfg["currency"]
    bank = e.bank.cfg()
    mode = e.bank.mode()
    report = bank.get("report") or {}
    split = report.get("probe_split" if mode == "probe" else "split") or {}
    global_reasons = []
    if e.settings.simulate: global_reasons.append("Simulationsdaten aktiv")
    if e.mode != "live": global_reasons.append("Globaler Modus ist Standby")
    if e.kill_switch: global_reasons.append("Not-Aus ist eingeschaltet")
    if not e.live: global_reasons.append("Handelsverbindung fehlt")
    if e.db.get("unresolved_order"): global_reasons.append("Ungeklärte Fusion-Order")
    if e.db.get("moved"): global_reasons.append("Handel ist auf eine andere Instanz verlegt")
    trading_allowed = not global_reasons
    wallet_fresh = bool(w.get("ts") and 0 <= now - w["ts"] <= 120 and not w.get("error") and not w.get("stale"))
    bank_used = mode != "shadow" and bool(report.get("split"))
    names = ([n for n, share in split.items() if n != "Cash" and share > 0] if bank_used else [b.get("strategy", "")])
    daily_proofs = [evidence(e.db.get("research"), n, now) for n in names]
    fast_proof = evidence(e.db.get("fastlab"), f["strategy"], now)
    pot = e.fast.pot_size(f) if f["on"] else 0.0
    total = float(w.get("total") or 0)
    daily_budget = max(0.0, min(cfg["max_invest"], (total - pot) * .98))
    reserve = e.fast.cash_reserve()
    conflicts = []
    overlap = sorted(set(e.db.get("live_qty", {})) & set(e.db.get("fast_qty", {})))
    if overlap: conflicts.append("Ältere überlappende Positionen: " + ", ".join(overlap) + ". Neue Überschneidungen sind gesperrt.")
    if f["on"] and pot > total: conflicts.append("Fast-Budget übersteigt den Kontowert; Betrag oder Anteil in Research prüfen.")
    if f["on"] and pot < float(f.get("floor") or 0): conflicts.append("Fast-Budget liegt unter seiner Abschaltgrenze.")
    if b.get("on") and f["on"] and daily_budget < 25:
        conflicts.append("Die Fast-Reservierung lässt dem Daily Brain weniger als 25 für neue Positionen.")
    if f["on"] and not fast_proof["ready"] and reserve:
        conflicts.append(f"{reserve:.2f} {cur} bleiben für Fast reserviert, obwohl dessen Kaufprüfung noch nicht bestanden ist.")
    if cfg["max_order"] < 25: conflicts.append("Das Orderlimit liegt unter vielen Fusion-Mindestorders.")
    if not wallet_fresh: conflicts.append("Kontostand fehlt oder ist älter als zwei Minuten; aktuelle Verbindung prüfen.")

    def lane(key, title, on, ready, reasons, budget, note, next_at):
        reasons = list(reasons)
        if not e.stance.get()["buys"]: reasons.append("Dein Kurs pausiert neue Käufe")
        if budget < 25: reasons.append("Budget unter 25; Fusion-Mindestorder prüfen")
        if cfg["max_order"] < 25: reasons.append("Orderlimit unter 25")
        if not on: status, label = "off", "Ausgeschaltet"
        elif not trading_allowed: status, label = "standby", "Keine echten Orders"
        elif not ready or reasons: status, label = "blocked", "Echtgeld · Käufe warten"
        else: status, label = "armed", "Echtgeld · signalbereit"
        return {"id": key, "name": title, "enabled": bool(on), "status": status, "label": label,
                "budget": round(budget, 2), "reasons": reasons, "note": note, "next_at": next_at}

    daily_reasons = [f"{p['strategy']}: {p['reason']}" for p in daily_proofs if not p["ready"]]
    if bank_used and not names: daily_reasons.append("Die Bank hält ihre Zuteilung vollständig in Cash")
    last_day = b.get("day")
    daily_next = (now // 86400 + 1) * 86400 if last_day and last_day >= now // 86400 * 86400 - 86400 else None
    lanes = [lane("daily", "Daily Brain", b.get("on"), bool(daily_proofs) and all(p["ready"] for p in daily_proofs),
                  daily_reasons, daily_budget, b.get("note") or "Entscheidet auf geschlossenen Tageskerzen", daily_next),
             lane("fast", "Fast-Topf", f["on"], fast_proof["ready"],
                  [] if fast_proof["ready"] else [fast_proof["reason"]], pot,
                  f.get("note") or "Entscheidet auf geschlossenen 4-Stunden-Kerzen; Ausstiege jede Minute",
                  (now // 14400 + 1) * 14400 + 120)]
    started = float(e.db.get("observation_started_v1") or e.started)
    since = max(started, now - WINDOW)
    stats = e.db.query("SELECT COUNT(*) orders, COALESCE(SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END),0) sells, "
                       "COALESCE(SUM(pnl),0) realized, COALESCE(SUM(fee),0) fees, MAX(ts) last "
                       "FROM trades WHERE mode='live' AND variant_id!='test' AND ts>=?", (since,))[0]
    latest = e.db.query("SELECT ts,symbol,side,notional,fee,pnl,variant_id FROM trades "
                        "WHERE mode='live' AND variant_id!='test' AND ts>=? ORDER BY id DESC LIMIT 5", (since,))
    ai = e.db.query("SELECT COALESCE(SUM(cost_usd),0) cost FROM llm_calls WHERE ts>=?", (since,))[0]["cost"]
    return {"ts": now, "currency": cur, "execution_allowed": trading_allowed, "global_reasons": global_reasons,
            "wallet_fresh": wallet_fresh, "wallet_ts": w.get("ts"), "lanes": lanes, "conflicts": conflicts,
            "bank": {"mode": mode, "used": bank_used, "probe": report.get("probe"),
                     "label": {"shadow": "Nur Modell · Daily Brain handelt seine eigene Strategie", "probe": "Echtgeld-Probe · begrenzte Beimischung",
                               "live": "Echtgeld-Mix · Bank verteilt das Daily-Budget"}[mode],
                     "effective": trading_allowed and bool(b.get("on")) and bank_used},
            "paper": {"label": "Volatility, Schatten und Geister-Trades sind virtuell"},
            "window": {"since": since, "started": started, "target_end": started + WINDOW,
                       "complete": now >= started + WINDOW, "hours": round((now - since) / 3600, 2),
                       "orders": stats["orders"], "sells": stats["sells"], "realized": round(stats["realized"], 2),
                       "fees": round(stats["fees"], 4), "ai_usd": round(ai, 4), "last": stats["last"], "latest": latest}}
