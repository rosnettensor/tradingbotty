"""Paper broker: real prices, fake money, realistic fees. Spot only, so it can never go below zero."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Position:
    symbol: str
    qty: float
    avg_price: float
    opened: float
    peak: float

    def value(self, price: float) -> float:
        return self.qty * price


@dataclass
class Fill:
    symbol: str
    side: str
    qty: float
    price: float
    notional: float
    fee: float
    pnl: float | None = None


@dataclass
class PaperBroker:
    cash: float
    fee_pct: float
    slippage_pct: float
    positions: dict[str, Position] = field(default_factory=dict)
    last_sell: dict[str, float] = field(default_factory=dict)

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(p.value(prices.get(s, p.avg_price)) for s, p in self.positions.items())

    def buy(self, symbol: str, usd: float, price: float, fee_pct: float | None = None, now: float | None = None) -> Fill:
        # Spend at most the cash we have. No borrowing: this is the "never owe money" rule at the broker level.
        if usd <= 0 or usd > self.cash + 1e-9:
            raise ValueError(f"buy {usd:.2f} exceeds cash {self.cash:.2f}")
        exec_price = price * (1 + self.slippage_pct / 100)
        fee = usd * (self.fee_pct if fee_pct is None else fee_pct) / 100
        qty = (usd - fee) / exec_price
        cost_per_unit = usd / qty  # cost basis includes the buy fee, so P&L is honest
        self.cash -= usd
        pos = self.positions.get(symbol)
        if pos:
            total = pos.qty + qty
            pos.avg_price = (pos.avg_price * pos.qty + cost_per_unit * qty) / total
            pos.qty = total
        else:
            self.positions[symbol] = Position(symbol, qty, cost_per_unit, now or time.time(), price)
        return Fill(symbol, "BUY", qty, exec_price, usd, fee)

    def sell(self, symbol: str, qty: float, price: float, fee_pct: float | None = None, now: float | None = None) -> Fill:
        pos = self.positions.get(symbol)
        if not pos or qty <= 0 or qty > pos.qty * (1 + 1e-9):
            raise ValueError(f"can't sell {qty} {symbol}: not held")  # no short selling, ever
        qty = min(qty, pos.qty)
        exec_price = price * (1 - self.slippage_pct / 100)
        gross = qty * exec_price
        fee = gross * (self.fee_pct if fee_pct is None else fee_pct) / 100
        # avg_price already includes the buy fee
        pnl = gross - fee - qty * pos.avg_price
        self.cash += gross - fee
        pos.qty -= qty
        if pos.qty <= 1e-12:
            del self.positions[symbol]
            self.last_sell[symbol] = now or time.time()
        return Fill(symbol, "SELL", qty, exec_price, gross, fee, pnl)

    def to_dict(self, prices: dict[str, float]) -> dict:
        return {
            "cash": self.cash,
            "equity": self.equity(prices),
            "positions": [
                {
                    "symbol": p.symbol, "qty": p.qty, "avg_price": p.avg_price,
                    "price": prices.get(p.symbol, p.avg_price), "value": p.value(prices.get(p.symbol, p.avg_price)),
                    "pnl_pct": (prices.get(p.symbol, p.avg_price) / p.avg_price - 1) * 100, "opened": p.opened,
                }
                for p in self.positions.values()
            ],
        }
