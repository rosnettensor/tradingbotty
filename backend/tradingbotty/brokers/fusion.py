"""Live trading on Bitpanda Fusion: Bitpanda's exchange with an order API and about 0.25% fees (vs ~1.5% in the app).

Same account as the Bitpanda app. Docs: https://docs.fusion.bitpanda.com

Safety rules built in:
- Spot only: Buy spends fiat you hold, Sell sells coins you hold. Nothing here can borrow or short.
- The API key needs Read + Trade only. Never give it withdrawal rights.
- Orders are checked against the pair's minimum size before they are sent.

The adapter is covered by mocked API tests. Account access and real execution must
be checked separately; supported pairs determine their own minimum order amounts.
"""
from __future__ import annotations

import asyncio
import math
from decimal import Decimal, ROUND_FLOOR

import httpx
from ..risk import positive, spot_only

BASE = "https://api.fusion.bitpanda.com"
DONE = {"filled", "cancelled", "canceled", "rejected", "expired", "closed", "filled-and-canceled", "filledandcanceled"}


class FusionError(RuntimeError):
    def __init__(self, message, http_status=None):
        self.http_status = http_status
        super().__init__(message)


class FusionPendingOrder(FusionError):
    """Execution is uncertain: stop trading and reconcile the order in Fusion."""
    def __init__(self, order):
        self.order = order
        super().__init__(f"order {order.get('id')} is unresolved ({order.get('status')}); check Fusion before resuming")


def _floor(x: float, step: float) -> float:
    if not step or step <= 0:
        return x
    return float((Decimal(str(x)) / Decimal(str(step))).to_integral_value(rounding=ROUND_FLOOR) * Decimal(str(step)))


def _fmt(x: float, step: float) -> str:
    decimals = max(0, -Decimal(str(step)).normalize().as_tuple().exponent) if step else 0
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
            raise FusionError(f"{method} {path} -> {r.status_code}: {r.text[:200]}", r.status_code)
        return r.json() if r.content else {}

    async def connect(self) -> dict:
        body = await self._req("GET", "/v1/pairs")
        items = body if isinstance(body, list) else body.get("data", [])
        self.pairs = {}
        for p in items:
            if str(p.get("quoteAsset", "")).upper() == self.currency:
                self.pairs[str(p.get("baseAsset", "")).upper()] = p
        if not self.pairs:
            quotes = sorted({str(p.get("quoteAsset", "")).upper() for p in items} - {""})
            raise FusionError(f"no {self.currency} pairs on Fusion (it has {', '.join(quotes[:8])}): "
                              f"set currency in config.toml [live]")
        return {"assets": len(self.pairs)}

    async def spread_pct(self, symbol: str) -> float:
        """Gap between best ask and best bid, in % of the middle. Wide gaps make market orders expensive."""
        book = await self._req("GET", f"/v1/orderbook/{self._pair(symbol)['pair']}", params={"depth": 5})
        bids, asks = book.get("bids") or [], book.get("asks") or []
        if not bids or not asks:
            return 99.0
        bid, ask = float(bids[0]["price"]), float(asks[0]["price"])
        return (ask - bid) / ((ask + bid) / 2) * 100 if ask > 0 and bid > 0 else 99.0

    async def prices(self) -> dict[str, float]:
        """Current price of every coin in our currency (one request)."""
        items = await self.tickers()
        out = {}
        for t in items:
            base, _, quote = str(t.get("pair", "")).upper().partition("-")
            if quote == self.currency and t.get("price"):
                out[base] = float(t["price"])
        return out

    async def tickers(self) -> list[dict]:
        body = await self._req("GET", "/v1/tickers")
        return body if isinstance(body, list) else body.get("data", [])

    async def liquidity(self, symbol: str) -> dict:
        """Displayed depth in quote currency, within 1% of the best bid/ask."""
        book = await self._req("GET", f"/v1/orderbook/{self._pair(symbol)['pair']}", params={"depth": 10})
        bids, asks = book.get("bids") or [], book.get("asks") or []
        if not bids or not asks:
            raise FusionError("empty order book")
        bid, ask = positive(bids[0]["price"], "bid"), positive(asks[0]["price"], "ask")
        if ask < bid:
            raise FusionError("crossed order book")
        def depth(rows, best):
            return sum(positive(r["price"], "price") * positive(r["quantity"], "quantity")
                       for r in rows if abs(float(r["price"]) / best - 1) <= 0.01)
        return {"spread_pct": (ask - bid) / ((ask + bid) / 2) * 100,
                "depth_quote": min(depth(bids, bid), depth(asks, ask))}

    async def balances(self) -> dict[str, float]:
        """Available amount per symbol, plus fiat under 'FIAT'."""
        body = await self._req("GET", "/v1/account/balances")
        items = body if isinstance(body, list) else body.get("data", [])
        out = {str(b["symbol"]).upper(): float(b.get("available", 0) or 0) for b in items if b.get("symbol")}
        out["FIAT"] = out.get(self.currency, 0.0)
        return out

    async def total_balance(self, symbol: str) -> float:
        """Available plus locked: a user limit order is not proof that coins were sold."""
        body = await self._req("GET", "/v1/account/balances")
        items = body if isinstance(body, list) else body.get("data", [])
        for b in items:
            if str(b.get("symbol", "")).upper() == symbol.upper():
                total = float(b.get("available") or 0) + float(b.get("locked") or 0)
                if not math.isfinite(total) or total < 0:
                    raise FusionError("invalid total balance")
                return total
        return 0.0

    def _pair(self, symbol: str) -> dict:
        p = self.pairs.get(symbol.upper())
        if not p:
            raise FusionError(f"{symbol}-{self.currency} is not tradable on Fusion")
        spot_only(p)
        return p

    async def _order(self, body: dict) -> dict:
        try:
            order = await self._req("POST", "/v1/account/orders", json=body)
        except (httpx.TransportError, ValueError) as ex:
            # A timed-out POST may already have reached the exchange. Never retry automatically.
            raise FusionPendingOrder({"id": None, "status": "unknown", "request": body}) from ex
        except FusionError as ex:
            if ex.http_status and ex.http_status >= 500:
                raise FusionPendingOrder({"id": None, "status": "unknown", "request": body}) from ex
            raise
        oid = order.get("id")
        for _ in range(20):  # market orders fill fast; wait up to ~10 seconds for the final state
            if str(order.get("status", "")).lower() in DONE or not oid:
                break
            await asyncio.sleep(0.5)
            try:
                order = await self._req("GET", f"/v1/account/orders/{oid}")
            except Exception as ex:
                raise FusionPendingOrder(order) from ex
        status = str(order.get("status", "")).lower()
        if status not in DONE or not oid:
            raise FusionPendingOrder(order)
        if status in ("rejected", "cancelled", "canceled", "expired", "filled-and-canceled", "filledandcanceled") and not float(order.get("filledQuantity") or 0):
            raise FusionError(f"order {status}: {str(order)[:200]}")
        qty = float(order.get("filledQuantity") or 0)
        price = float(order.get("filledAveragePrice") or 0)
        try:
            positive(qty, "filled quantity")
            positive(price, "fill price")
        except ValueError as ex:
            raise FusionPendingOrder(order) from ex
        fee = order.get("fee") or {}
        try:
            notional = positive(order["filledAmount"], "filled amount") if "filledAmount" in order else qty * price
            fee_amount = float(fee.get("amount", 0) or 0) if isinstance(fee, dict) else 0.0
            if not math.isfinite(fee_amount) or fee_amount < 0:
                raise ValueError("invalid fee")
        except (ValueError, TypeError) as ex:
            raise FusionPendingOrder(order) from ex
        return {"order_id": oid, "status": status,
                "execution": {"quantity": qty, "price": price, "notional": notional,
                              "fee": fee_amount}}

    async def buy(self, symbol: str, fiat_amount: float) -> dict:
        positive(fiat_amount, "order amount")
        p = self._pair(symbol)
        bal = await self.balances()
        if fiat_amount > bal["FIAT"] + 1e-9:
            raise FusionError("not enough fiat for this buy")  # never borrow
        amount = _floor(fiat_amount, float(p.get("amountIncrement") or 0.01))
        if amount < float(p.get("minOrderAmount") or 0):
            raise FusionError(f"{amount} {self.currency} is below Fusion's minimum for {symbol}")
        step = float(p.get("amountIncrement") or 0.01)
        if amount > float(p.get("maxOrderAmount") or float("inf")):
            raise FusionError("buy exceeds Fusion's maximum order amount")
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
