"""Independent cash-only gate. Strategies and AI cannot change these ceilings.

Amounts use the account quote currency (CHF by default). Losses are gross realized
losses since 00:00 UTC plus current unrealized downside; wins never refill the budget.
This stops new purchases, not exits, and cannot guarantee an execution price.
"""
from __future__ import annotations

import math

MAX_TRADE = 100.0
MAX_DAILY_LOSS = 200.0


def positive(value, label: str) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"invalid {label}") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"invalid {label}: must be finite and positive")
    return number


def spot_only(pair: dict) -> None:
    kind = str(pair.get("productType", "SPOT")).upper()
    asset = str(pair.get("baseAssetType", "cryptocoin")).lower()
    if (kind != "SPOT" or asset not in {"cryptocoin", "crypto"}
            or pair.get("borrowed_funds") or pair.get("can_create_margin")
            or float(pair.get("leverage", 1)) != 1):
        raise ValueError("cash-only spot products required: no borrowing, leverage or derivatives")


def approve(amount: float, single_cap: float, daily_loss: float, positions: set[str],
            symbol: str, max_positions: int) -> float:
    amount = positive(amount, "order amount")
    cap = min(MAX_TRADE, positive(single_cap, "single order cap"))
    if not math.isfinite(daily_loss) or daily_loss < 0:
        raise ValueError("daily loss data invalid")
    if daily_loss >= MAX_DAILY_LOSS:
        raise ValueError(f"daily loss limit reached ({daily_loss:.2f} ≥ {MAX_DAILY_LOSS:g}); exits remain allowed")
    if symbol not in positions and len(positions) >= max_positions:
        raise ValueError("maximum open positions reached")
    return min(amount, cap)
