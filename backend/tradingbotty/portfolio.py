"""One portfolio policy; existing strategy books are attribution, not separate accounts.

Legacy SQLite keys remain canonical so deployment preserves ownership, cost basis and
operator limits. No destructive migration and no second copy of position quantities.
"""
from __future__ import annotations

import math

BOOKS = {"brain": ("live_qty", "live_cost"), "fast": ("fast_qty", "fast_cost"),
         "test": ("test_qty", "test_cost")}


class Portfolio:
    def __init__(self, engine):
        self.e = engine

    @staticmethod
    def keys(book):
        if book not in BOOKS:
            raise ValueError(f"unknown portfolio book: {book}")
        return BOOKS[book]

    def invested(self, book):
        return sum(self.e.db.get(self.keys(book)[1], {}).values())

    def holdings(self):
        qty, costs = {}, {}
        for qkey, ckey in BOOKS.values():
            for s, q in self.e.db.get(qkey, {}).items():
                qty[s] = qty.get(s, 0) + q
            for s, c in self.e.db.get(ckey, {}).items():
                costs[s] = costs.get(s, 0) + c
        return qty, costs

    def other_owner(self, book, symbol):
        return next((b for b in ("brain", "fast") if b != book and
                     self.e.db.get(self.keys(b)[0], {}).get(symbol, 0) > 0), None)

    def fast_budget(self, config=None):
        c = config or self.e.fast.cfg()
        total = float((self.e.wallet or {}).get("total") or 0)
        value = total * c["pct"] / 100 if c["mode"] == "pct" else c["chf"] + c["realized"]
        if not math.isfinite(value):
            raise ValueError("invalid fast allocation")
        return round(max(0, value), 2)

    def fast_cash_reserve(self):
        return round(max(0, self.fast_budget() - self.invested("fast")), 2) if self.e.fast.on() else 0.0

    def allocation(self):
        e = self.e
        total = max(0.0, float((e.wallet or {}).get("total") or 0))
        # Disabled strategies can still own positions: their capital is not free a second time.
        held = self.invested("fast")
        requested = self.fast_budget() if e.fast.on() else 0.0
        reserved = max(requested, held)
        daily = max(0.0, min(e.settings["live"]["max_invest"], (total - reserved) * .98))
        return {"total": total, "daily": round(daily, 2), "fast": requested,
                "fast_committed": round(held, 2), "fast_cash_reserve": self.fast_cash_reserve(),
                "unallocated": round(max(0.0, total - daily - reserved), 2),
                "overallocated": reserved > total, "policy": "partitioned", "currency": e.settings["live"]["currency"]}

    def fee_basis(self, book, symbol, sold_qty):
        """Remaining acquisition fees, apportioned on partial fills using the durable trade ledger.

        Previous code charged all buy fees on the first partial sale. Replay avoids a
        fragile fee-state migration; quantity/cost ownership stays in the legacy keys.
        """
        qty = fees = cost = 0.0
        for t in self.e.db.query("SELECT side,qty,fee,notional,pnl FROM trades WHERE mode='live' AND variant_id=? "
                                "AND symbol=? ORDER BY id", (book, symbol)):
            if t["side"] == "BUY":
                qty += t["qty"]
                fees += t["fee"]
                cost += t["notional"]
            elif qty > 0:
                part = min(1.0, t["qty"] / qty)
                # Respect fees already charged by the old version on a partial sale.
                charged = (max(0.0, t["notional"] - t["fee"] - t["pnl"] - cost * part)
                           if t["pnl"] is not None else fees * part)
                fees = max(0.0, fees - charged)
                cost *= 1 - part
                qty = max(0.0, qty - t["qty"])
                if qty <= 1e-12:
                    fees = cost = qty = 0.0
        return fees * min(1.0, sold_qty / qty) if qty > 0 else 0.0
