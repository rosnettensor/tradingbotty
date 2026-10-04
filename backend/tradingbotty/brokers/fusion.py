"""Live trading on Bitpanda Fusion: Bitpanda's exchange with an order API and about 0.25% fees (vs ~1.5% in the app).

Same account as the Bitpanda app. Docs: https://docs.fusion.bitpanda.com

Safety rules built in:
- Spot only: Buy spends fiat you hold, Sell sells coins you hold. Nothing here can borrow or short.
- The API key needs Read + Trade only. Never give it withdrawal rights.
- Orders are checked against the pair's minimum size before they are sent.

Status: EXPERIMENTAL, tested against a mock of the documented API. Run `python run.py --check-live` first,
then do the first live trade together with a 1-2 EUR order.
"""
from __future__ import annotations

import asyncio
import math

import httpx

BASE = "https://api.fusion.bitpanda.com"
DONE = {"filled", "cancelled", "canceled", "rejected", "expired", "closed"}


class FusionError(RuntimeError):
    pass


def _floor(x: float, step: float) -> float:
    if not step or step <= 0:
        return x
    return math.floor(x / step + 1e-9) * step


def _fmt(x: float, step: float) -> str:
    decimals = max(0, -int(math.floor(math.log10(step)))) if step and step < 1 else 0
    return f"{x:.{decimals}f}"


class FusionBroker:
    name = "Bitpanda Fusion"

    def __init__(self, api_key: str, currency: str = "EUR", log=None):
        self.client = httpx.AsyncClient(base_url=BASE, timeout=20, headers={"x-api-key": api_key})
        self.currency = currency.upper()
        self.log = log or (lambda *a: None)
        self.pairs: dict[str, dict] = {}  # base symbol -> pair info for our quote currency

    async def _req(self, method: str, path: str, **kw):
        r = await self.client.request(method, path, **kw)
        if r.status_code >= 400:
            raise FusionError(f"{method} {path} -> {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else {}

    async def connect(self) -> dict:
        body = await self._req("GET", "/v1/pairs")
        items = body if isinstance(body, list) else body.get("data", [])
        for p in items:
            if str(p.get("quoteAsset", "")).upper() == self.currency:
                self.pairs[str(p.get("baseAsset", "")).upper()] = p
        if not self.pairs:
            raise FusionError(f"no {self.currency} pairs on Fusion: try currency EUR in config.toml")
        return {"assets": len(self.pairs)}

    async def balances(self) -> dict[str, float]:
        """Available amount per symbol, plus fiat under 'FIAT'."""
        body = await self._req("GET", "/v1/account/balances")
        items = body if isinstance(body, list) else body.get("data", [])
        out = {str(b["symbol"]).upper(): float(b.get("available", 0) or 0) for b in items if b.get("symbol")}
        out["FIAT"] = out.get(self.currency, 0.0)
        return out

    def _pair(self, symbol: str) -> dict:
        p = self.pairs.get(symbol.upper())
        if not p:
            raise FusionError(f"{symbol}-{self.currency} is not tradable on Fusion")
        return p

    async def _order(self, body: dict) -> dict:
        order = await self._req("POST", "/v1/account/orders", json=body)
        oid = order.get("id")
        for _ in range(20):  # market orders fill fast; wait up to ~10 seconds for the final state
            if str(order.get("status", "")).lower() in DONE or not oid:
                break
            await asyncio.sleep(0.5)
            order = await self._req("GET", f"/v1/account/orders/{oid}")
        status = str(order.get("status", "")).lower()
        if status in ("rejected", "cancelled", "canceled", "expired") and not float(order.get("filledQuantity") or 0):
            raise FusionError(f"order {status}: {str(order)[:200]}")
        qty = float(order.get("filledQuantity") or 0)
        price = float(order.get("filledAveragePrice") or 0)
        fee = order.get("fee") or {}
        return {"order_id": oid, "status": status,
                "execution": {"quantity": qty, "price": price, "notional": round(qty * price, 2),
                              "fee": float(fee.get("amount", 0) or 0) if isinstance(fee, dict) else 0.0}}

    async def buy(self, symbol: str, fiat_amount: float) -> dict:
        p = self._pair(symbol)
        bal = await self.balances()
        if fiat_amount > bal["FIAT"] + 1e-9:
            raise FusionError("not enough fiat for this buy")  # never borrow
        amount = _floor(fiat_amount, float(p.get("amountIncrement") or 0.01))
        if amount < float(p.get("minOrderAmount") or 0):
            raise FusionError(f"{amount} {self.currency} is below Fusion's minimum for {symbol}")
        step = float(p.get("amountIncrement") or 0.01)
        return await self._order({"pair": p["pair"], "side": "Buy", "type": "Market", "amount": _fmt(amount, step)})

    async def sell_fraction(self, symbol: str, fraction: float, owned: float | None = None) -> dict | None:
        """Sell a fraction of what the bot owns. `owned` = the quantity the bot bought itself, so coins you held
        before (or bought by hand) are never touched. None = fraction of the whole balance (tests only)."""
        p = self._pair(symbol)
        held = (await self.balances()).get(symbol.upper(), 0.0)
        base = held if owned is None else min(held, max(0.0, owned))
        step = float(p.get("sizeIncrement") or 1e-8)
        qty = _floor(base * max(0.0, min(1.0, fraction)), step)
        if qty <= 0:
            return None  # nothing to sell, and never short
        return await self._order({"pair": p["pair"], "side": "Sell", "type": "Market", "quantity": _fmt(qty, step)})
