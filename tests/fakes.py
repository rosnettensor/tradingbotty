"""A fake Bitpanda Fusion account and a simulated engine for tests (no real money, no network)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty.config import load_settings  # noqa: E402


class FakeFusion:
    name = "Bitpanda Fusion"
    currency = "CHF"

    def __init__(self, spread=0.1):
        self.pairs = {"BTC": {}, "SOL": {}}
        self.bal = {"FIAT": 100.0}
        self.spread = spread
        self.buys, self.sells = [], []

    async def balances(self):
        return dict(self.bal)

    async def total_balance(self, symbol):
        return self.bal.get(symbol, 0)

    async def spread_pct(self, symbol):
        return self.spread

    async def buy(self, symbol, amount):
        if amount > self.bal["FIAT"] * 0.9975 + 1e-9:       # like Fusion: the fee must fit in your cash too
            raise RuntimeError("422: The order size/value is too big.")
        self.buys.append((symbol, amount))
        self.bal["FIAT"] -= amount
        self.bal[symbol] = self.bal.get(symbol, 0) + amount / 10
        return {"execution": {"quantity": amount / 10, "price": 10, "notional": amount, "fee": 0}}

    async def sell_fraction(self, symbol, fraction, owned=None):
        held = self.bal.get(symbol, 0)
        qty = (held if owned is None else min(held, owned)) * fraction
        self.sells.append((symbol, round(qty, 6)))
        self.bal[symbol] -= qty
        self.bal["FIAT"] += qty * 10
        return {"execution": {"quantity": qty, "price": 10, "notional": qty * 10, "fee": 0}}

    async def prices(self):
        return {s: 10.0 for s in self.pairs}


def _engine(tmp_path, monkeypatch, live):
    monkeypatch.setenv("TB_SIMULATE", "1")
    monkeypatch.setenv("TB_DB", str(tmp_path / "l.db"))
    from tradingbotty.engine import Engine
    e = Engine(load_settings())
    e.live = live
    e.db.set("mode", "live")
    return e
