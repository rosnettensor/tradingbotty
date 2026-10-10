"""Explicitly opted-in spot pilot, using the common execution/ownership ledger.

No backtest certification is fabricated: this is a bounded experimental mandate.
Limits are fixed, persisted submissions count across restarts, profits never scale it.
"""
from __future__ import annotations

import math
import time

from .risk import positive

BUDGET = 50.0
ORDER = 30.0
LOSS_STOP = 10.0
DAILY_ORDERS = 2
CONFIRMATION = "VOLATILITY LIVE"


class VolatilityTrader:
    def __init__(self, engine):
        self.e = engine

    def cfg(self):
        return {"enabled": False, "currency": None, "activated_at": 0,
                **self.e.db.get("volatility_live", {})}

    def stats(self, now=None):
        now = time.time() if now is None else now
        e = self.e
        result = e.db.query("SELECT COALESCE(SUM(pnl),0) realized, "
                            "COALESCE(SUM(CASE WHEN pnl<0 THEN -pnl ELSE 0 END),0) loss, "
                            "COALESCE(SUM(fee),0) fees FROM trades WHERE mode='live' AND variant_id='volatility'")[0]
        result["orders_today"] = e.db.query("SELECT COUNT(*) n FROM orders WHERE book='volatility' "
                                             "AND side='BUY' AND created_at>=?", (now // 86400 * 86400,))[0]["n"]
        return result

    def budget(self):
        # Losses reduce capital; profitable trades never automatically enlarge the mandate.
        return max(0.0, BUDGET + min(0.0, self.stats()["realized"]))

    def configure(self, enabled, confirmation=""):
        e, c = self.e, self.cfg()
        if enabled:
            if confirmation != CONFIRMATION:
                raise ValueError("Zur Freigabe VOLATILITY LIVE eingeben")
            e.execution.permission()
            if e.settings.simulate:
                raise ValueError("Simulationsdaten können den Echtgeld-Pilot nicht aktivieren")
            if not hasattr(e.live, "liquidity") or not hasattr(e.live, "tickers"):
                raise ValueError("Der Pilot benötigt Fusion-Marktdaten und Orderbücher")
            cur = e.settings["live"]["currency"]
            if e.live.currency != cur or (c["currency"] and c["currency"] != cur):
                raise ValueError("Pilot-Kontowährung stimmt nicht überein; keine automatische Umrechnung")
            if self.stats()["loss"] >= LOSS_STOP:
                raise ValueError("Pilot-Verlustgrenze erreicht; erneutes Einschalten setzt sie nicht zurück")
            if not c["enabled"]:
                c.update(enabled=True, currency=cur, activated_at=time.time())
        else:
            c["enabled"] = False  # ownership and exit monitoring survive a purchase pause
        e.db.set("volatility_live", c)
        return self.status()

    def reason(self, now=None):
        e, c = self.e, self.cfg()
        if not c["enabled"]:
            return "Neue Käufe nicht freigegeben"
        if e.settings.simulate:
            return "Simulationsdaten aktiv"
        if c["currency"] != e.settings["live"]["currency"] or not e.live or e.live.currency != c["currency"]:
            return "Handelsverbindung oder Pilot-Kontowährung stimmt nicht überein"
        if e.db.get("volatility_qty", {}):
            return "Ein Pilot-Platz belegt; Ausstieg wird weiter geprüft"
        stats = self.stats(now)
        if stats["loss"] >= LOSS_STOP:
            return "Pilot-Verlustgrenze erreicht; keine weiteren Käufe"
        if stats["orders_today"] >= DAILY_ORDERS:
            return "Zwei Kaufaufträge heute erreicht; nächster Handelstag UTC abwarten"
        scan = e.db.get("speculation", {})
        age = (time.time() if now is None else now) - scan.get("ts", 0)
        if scan.get("error") or not 0 <= age <= 180 or scan.get("currency") != c["currency"]:
            return "Frischer Markt-Scan fehlt"
        if scan.get("ts", 0) <= c["activated_at"]:
            return "Wartet auf neue Scans nach deiner Freigabe"
        return None

    def admit(self, symbol):
        e, c = self.e, self.cfg()
        reason = self.reason()
        if reason:
            raise ValueError(reason)
        scan = e.db.get("speculation", {})
        row = next((r for r in scan.get("rows", []) if r["symbol"] == symbol), {})
        if (not row.get("eligible") or not row.get("breakout") or (row.get("momentum_pct") or 0) < 2
                or (row.get("reference_ts") or 0) < c["activated_at"]):
            raise ValueError("Kein frischer Ausbruch nach Pilot-Freigabe")
        # One attempt per scan, even on rejection/restart; an exit cannot trigger an immediate re-entry.
        if e.db.query("SELECT id FROM orders WHERE book='volatility' AND created_at>=? LIMIT 1", (scan["ts"],)):
            raise ValueError("Dieser Scan wurde bereits gehandelt; nächsten Scan abwarten")
        return row

    async def check_market(self, symbol):
        """Fresh order book and quote inside the account lock, immediately before submission."""
        e = self.e
        liq = await e.live.liquidity(symbol)
        if not all(math.isfinite(liq.get(k, math.nan)) and liq[k] >= 0 for k in ("spread_pct", "depth_quote")):
            raise ValueError("Ungültiges Pilot-Orderbuch")
        if liq["depth_quote"] < 1000 or liq["spread_pct"] > e.settings["live"]["max_spread_pct"]:
            raise ValueError("Pilot-Orderbuch: Tiefe oder Spread nicht ausreichend")
        prices = await e.live.prices()
        px = positive(prices.get(symbol), "Pilot-Preis")
        row = self.admit(symbol)
        if px <= positive(row.get("reference_high"), "Referenzhoch") or px < positive(row.get("reference_price"), "Referenzpreis") * 1.02:
            raise ValueError("Pilot-Ausbruch vor Ausführung nicht mehr gültig")

    async def enter(self):
        e = self.e
        if self.reason():
            return
        revision = e.strategies.revision("volatility")
        e.db.set("volatility_note", "Wartet auf einen gültigen Ausbruch mit frischer Referenz nach Freigabe")
        for row in e.db.get("speculation", {}).get("rows", []):
            if not row.get("eligible") or not row.get("breakout") or (row.get("momentum_pct") or 0) < 2:
                continue
            try:
                self.admit(row["symbol"])
                e.strategies.admit("volatility", row["symbol"], revision)
                await e.execution.buy(row["symbol"], ORDER, "volatility: confirmed breakout + scan momentum",
                                      book="volatility", automatic=True, revision=revision)
                e.db.set("volatility_note", "Bestätigter Kauf gebucht; Ausstieg wird jede Minute geprüft")
                return
            except Exception as ex:
                e.db.set("volatility_note", str(ex)[:240])
                # Any submitted attempt consumes this scan, including uncertain/rejected outcomes.
                if self.reason() or e.db.query("SELECT id FROM orders WHERE book='volatility' AND created_at>=?",
                                              (e.db.get("speculation", {}).get("ts", 0),)):
                    return

    async def watch(self):
        e, c = self.e, self.cfg()
        owned = e.db.get("volatility_qty", {})
        if not owned:
            return
        try:
            e.execution.permission()
            if not c["currency"] or e.live.currency != c["currency"]:
                raise ValueError("Pilot-Ausstieg: Kontowährung stimmt nicht überein")
            prices = await e.live.prices()
            for s, qty in owned.items():
                px = positive(prices.get(s), "Pilot-Ausstiegspreis")
                buys = e._open_buys("volatility", s)
                if not buys:
                    raise ValueError("Pilot-Einstieg fehlt im Ledger; Position abgleichen")
                entry = positive(e.db.get("volatility_cost", {}).get(s), "Pilot-Kosten") / qty
                change = px / entry - 1
                why = "stop −8%" if change <= -.08 else "target +20%" if change >= .20 else "time exit 24h" if time.time() - buys[0]["ts"] >= 86400 else None
                if why:
                    result = await e.execution.sell(s, "volatility: " + why, book="volatility", expected_entry_ts=buys[0]["ts"])
                    if result is None and e.db.get("volatility_qty", {}).get(s):
                        raise ValueError("Pilot-Ausstieg nicht bestätigt; Orderjournal und Verbindung prüfen")
            e.db.set("volatility_exit", {"ts": time.time(), "error": None})
        except Exception as ex:
            e.db.set("volatility_exit", {"ts": time.time(), "error": str(ex)[:240]})

    def status(self):
        e, c = self.e, self.cfg()
        stats = self.stats()
        reasons = []
        try:
            e.execution.permission()
        except ValueError as ex:
            reasons.append(str(ex))
        reason = self.reason()
        if reason:
            reasons.append(reason)
        if not e.stance.buys_allowed():
            reasons.append("Dein Kurs pausiert neue Käufe")
        return {**c, "budget": self.budget(), "max_order": ORDER, "max_positions": 1,
                "daily_orders": DAILY_ORDERS, "loss_stop": LOSS_STOP, **stats,
                "reasons": reasons, "note": e.db.get("volatility_note"),
                "exit_check": e.db.get("volatility_exit", {}),
                "positions": [{"symbol": s, "qty": q, "cost": e.db.get("volatility_cost", {}).get(s, 0)}
                              for s, q in e.db.get("volatility_qty", {}).items()],
                "trades": e.db.query("SELECT ts,symbol,side,qty,notional,fee,pnl FROM trades "
                                     "WHERE mode='live' AND variant_id='volatility' ORDER BY id DESC LIMIT 20")}
