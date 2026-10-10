"""Read-only portfolio observatory. Observed account OHLC, never exchange candles.

The legacy account series has no per-book marks or complete flow history. Import
only what exists; start net-flow and per-book comparisons at the new observations.
"""
from __future__ import annotations

import math
import time

BOOKS = ("brain", "fast", "volatility")
RANGES = {"1d": 86400, "1w": 7*86400, "1m": 30*86400, "3m": 90*86400,
          "6m": 180*86400, "1y": 365*86400, "all": None}
INTERVALS = (300, 900, 1800, 3600, 7200, 21600, 43200, 86400, 172800, 604800, 2592000)


def bootstrap(e):
    if e.db.get("account_history_v1"):
        return
    currency = e.settings["live"]["currency"]
    with e.db.transaction():
        for h in e.db.get("wallet_hist", []):
            if len(h) >= 2 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in h[:2]) and h[1] >= 0:
                edge = h[2] if len(h) > 2 and isinstance(h[2], (int, float)) and math.isfinite(h[2]) else None
                e.db.execute("INSERT OR IGNORE INTO account_samples(ts,currency,total,edge,source) VALUES(?,?,?,?,?)",
                             (h[0], currency, h[1], edge, "legacy"))
        e.db.set("account_history_v1", {"ts": time.time(), "legacy_currency": currency})


def revision(e):
    return e.db.query("SELECT COALESCE(MAX(updated_at),0) rev,COUNT(*) n FROM orders")[0]


def record(e, read_at, balances, prices, before):
    """One complete, stable account observation every five minutes; no network calls."""
    now, w = time.time(), e.wallet or {}
    def skip(reason):
        e.db.set("account_chart_status", {"ts": now, "error": reason})
        return False
    if e.settings.simulate:
        return skip("Simulationsdaten werden nicht als Kontohistorie gespeichert")
    if not 0 <= now - read_at <= 120:
        return skip("Kontolesung zu alt; nächste Messung abwarten")
    if before is None or e.execution.lock.locked() or before != revision(e):
        return skip("Auftrag während Kontolesung; nächste konsistente Messung abwarten")
    currency = w.get("currency")
    if currency != e.settings["live"]["currency"] or w.get("error") or w.get("stale"):
        return skip("Kontowährung oder Bewertung nicht bestätigt")
    values = [w.get("total"), w.get("fiat")]
    if not all(isinstance(x, (int, float)) and math.isfinite(x) and x >= 0 for x in values):
        return skip("Ungültige Kontobewertung")
    missing = [s for s, q in balances.items() if s not in ("FIAT", currency) and q > 1e-12
               and not (isinstance(prices.get(s), (int, float)) and math.isfinite(prices[s]) and prices[s] > 0)]
    if missing:
        return skip("Kurse fehlen: " + ", ".join(missing[:8]))
    if e.db.get("account_history_v1", {}).get("legacy_currency", currency) != currency:
        return skip("Kontowährung geändert; historische Kostenbasis muss abgeglichen werden")
    latest = e.db.query("SELECT MAX(ts) ts FROM account_samples WHERE currency=? AND source='observed'", (currency,))[0]["ts"]
    e.db.set("account_chart_status", {"ts": now, "error": None})
    if latest is not None and now - latest < 300:
        return False
    pnl = {}
    for book in BOOKS:
        qty_key, cost_key = e.portfolio.keys(book)
        quantities, costs = e.db.get(qty_key, {}), e.db.get(cost_key, {})
        if any(not prices.get(s) or not math.isfinite(prices[s]) for s, q in quantities.items() if q > 0):
            return skip("Bot-Bewertung unvollständig")
        if any(q > balances.get(s, 0) + max(1e-8, q*1e-6) for s, q in quantities.items()):
            return skip("Bot-Bestand weicht vom Konto ab; Abgleich nötig")
        realized = e.db.query("SELECT COALESCE(SUM(pnl),0) pnl FROM trades WHERE mode='live' AND variant_id=?", (book,))[0]["pnl"]
        pnl[book] = realized + sum(q*prices[s] - costs.get(s, 0) - e.portfolio.fee_basis(book, s, q)
                                   for s, q in quantities.items() if q > 0)
    e.db.execute("INSERT OR IGNORE INTO account_samples(ts,currency,total,cash,edge,flow_total,brain_pnl,fast_pnl,volatility_pnl,source) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?)", (now, currency, w["total"], w["fiat"], w.get("bot_edge"),
                 float(e.db.get("chart_flow_total", 0)), *(pnl[b] for b in BOOKS), "observed"))
    return True


def snapshot(e, period="1d", now=None):
    if period not in RANGES:
        raise ValueError("Unbekannter Zeitraum")
    now = time.time() if now is None else now
    currency = e.settings["live"]["currency"]
    bounds = e.db.query("SELECT MIN(ts) first,MAX(ts) last,COUNT(*) n FROM account_samples WHERE currency=? AND ts<=?", (currency, now))[0]
    since = max(0, now - RANGES[period]) if RANGES[period] else (bounds["first"] or now)
    span = now - max(since, bounds["first"] or since)
    interval = next((x for x in INTERVALS if x >= span/400), max(2592000, math.ceil(span/400/86400)*86400))
    # Aggregate in SQLite: response size stays bounded even after years of observations.
    rows = e.db.query("""WITH buckets AS (
        SELECT CAST(ts/? AS INTEGER)*? bucket,MIN(ts) first_ts,MAX(ts) last_ts,COUNT(*) n,
               MIN(total) low,MAX(total) high,
               MIN(CASE WHEN flow_total IS NOT NULL THEN total-flow_total END) net_low,
               MAX(CASE WHEN flow_total IS NOT NULL THEN total-flow_total END) net_high
        FROM account_samples WHERE currency=? AND ts>=? AND ts<=? GROUP BY bucket
    ) SELECT b.*,a.total open,z.total close,a.total-a.flow_total net_open,z.total-z.flow_total net_close,
             z.cash,z.edge,z.brain_pnl,z.fast_pnl,z.volatility_pnl,z.source
      FROM buckets b JOIN account_samples a ON a.currency=? AND a.ts=b.first_ts
      JOIN account_samples z ON z.currency=? AND z.ts=b.last_ts ORDER BY b.bucket""",
        (interval, interval, currency, since, now, currency, currency))
    first = e.db.query("SELECT * FROM account_samples WHERE currency=? AND ts>=? AND ts<=? ORDER BY ts LIMIT 1", (currency, since, now))
    last = e.db.query("SELECT * FROM account_samples WHERE currency=? AND ts>=? AND ts<=? ORDER BY ts DESC LIMIT 1", (currency, since, now))
    bars = []
    for r in rows:
        net = ([r["net_open"], r["net_high"], r["net_low"], r["net_close"]]
               if r["net_open"] is not None and r["net_close"] is not None else None)
        bars.append({"ts": r["last_ts"], "start": r["first_ts"], "bucket": r["bucket"], "n": r["n"],
                     "account": [r["open"], r["high"], r["low"], r["close"]], "net": net,
                     "cash": r["cash"], "hold": r["close"] - r["edge"] if r["edge"] is not None else None,
                     "bots": {b: r[b+"_pnl"] for b in BOOKS}})
    baseline = e.db.query("SELECT * FROM account_samples WHERE currency=? AND ts>=? AND ts<=? AND flow_total IS NOT NULL ORDER BY ts LIMIT 1",
                          (currency, since, now))
    base = baseline[0] if baseline else None
    events = e.db.query("SELECT CAST(ts/? AS INTEGER)*? bucket,SUM(CASE WHEN side='BUY' THEN 1 ELSE 0 END) buys,"
                        "SUM(CASE WHEN side='SELL' THEN 1 ELSE 0 END) sells,SUM(notional) amount "
                        "FROM trades WHERE mode='live' AND variant_id IN ('brain','fast','volatility') AND ts>=? AND ts<=? GROUP BY bucket",
                        (interval, interval, since, now))
    w = e.wallet or {}
    allocation = [{"id": k, "value": max(0.0, float(w.get(v) or 0))} for k, v in
                  (("cash", "fiat"), ("brain", "bot_value"), ("fast", "fast_value"), ("volatility", "volatility_value"), ("own", "yours_value"))] if w.get("ts") and not w.get("error") else []
    latest = last[0] if last else None
    initial = first[0] if first else None
    change = latest["total"] - initial["total"] if initial and latest else None
    net_change = (latest["total"] - latest["flow_total"] - (base["total"] - base["flow_total"])) if base and latest and latest["flow_total"] is not None else None
    flows = latest["flow_total"]-base["flow_total"] if base and latest and latest["flow_total"] is not None else None
    return {"ts": now, "range": period, "currency": currency, "interval": interval, "since": since,
            "first": initial["ts"] if initial else None, "last": latest["ts"] if latest else None,
            "available_since": bounds["first"], "samples": sum(b["n"] for b in bars), "bars": bars,
            "stale": not bounds["last"] or now-bounds["last"] > 660,
            "status": e.db.get("account_chart_status", {}),
            "baseline": {"ts": base["ts"], "net": base["total"]-base["flow_total"],
                         "bots": {b: base[b+"_pnl"] for b in BOOKS}} if base else None,
            "summary": {"total": latest["total"] if latest else None, "change": change,
                        "change_pct": change/initial["total"]*100 if initial and initial["total"] else None,
                        "net_change": net_change, "flows": flows,
                        "high": max((b["account"][1] for b in bars), default=None),
                        "low": min((b["account"][2] for b in bars), default=None)},
            "events": events, "allocation": allocation,
            "allocation_ts": w.get("ts"), "allocation_stale": bool(w.get("error") or w.get("stale") or now-w.get("ts",0)>120),
            "notes": {"candles": "OHLC aus Kontomessungen, keine Börsenkerzen; unbeobachtete Schwankungen fehlen.",
                      "comparison": "Änderung in Kontowährung nach erfassten Zu-/Abflüssen. Bot-P/L inklusive Gebühren und offener Positionen, ab erster gemeinsamer Messung.",
                      "legacy": "Ältere Kontowerte wurden aus dem bisherigen Verlauf in dessen konfigurierter Kontowährung übernommen. Bot- und bereinigte Vergleiche beginnen mit diesem Update."}}
