"""Evidence required for automatic strategy entries; never blocks an exit."""
from __future__ import annotations

import math
import time

MAX_AGE = 48 * 3600


def evidence(result: dict | None, strategy: str, now: float | None = None) -> dict:
    result = result or {}
    now = time.time() if now is None else now
    try:
        age = now - float(result.get("ts") or 0)
        fresh = math.isfinite(age) and 0 <= age <= MAX_AGE and bool(result.get("ts"))
    except (TypeError, ValueError):
        fresh = False
    row = next((r for r in result.get("rows", []) if r.get("name") == strategy), None)
    if not row:
        reason = "Strategie noch nicht im Labor getestet"
    elif result.get("simulated") is not False:
        reason = "Backtest mit echten Marktdaten fehlt"
    elif not fresh:
        reason = "Backtest älter als 48 Stunden oder Zeitstempel ungültig"
    elif row.get("robust") is not True:
        reason = "Strategie besteht die Robustheitsprüfung nicht"
    else:
        reason = "Aktueller Backtest mit Marktdaten bestanden; kein Gewinnversprechen"
    return {"ready": bool(row and result.get("simulated") is False and fresh and row.get("robust") is True),
            "reason": reason, "ts": result.get("ts"), "strategy": strategy}
