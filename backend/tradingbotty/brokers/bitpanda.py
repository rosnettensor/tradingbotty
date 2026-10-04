"""Live trading on Bitpanda through its public API (quote, then accept).

Safety rules built in:
- Only BUY with fiat you hold and only SELL coins you hold: the account can't go negative.
- The API key should have Read + Trade scope only, never withdrawals.
- Live mirrors the champion strategy in proportions (share of equity), so currency doesn't matter.

Status: EXPERIMENTAL. Run `python -m tradingbotty.live_check` first: it lists balances and asset ids
without trading. Do the first live trade together with a 1-2 EUR order.
"""
from __future__ import annotations

import httpx

BASE = "https://api.public.bitpanda.com/v1"


class BitpandaError(RuntimeError):
    pass


class BitpandaBroker:
    name = "Bitpanda app quotes"
    def __init__(self, api_key: str, currency: str = "EUR", log=None):
        self.client = httpx.AsyncClient(base_url=BASE, timeout=20, headers={"x-api-key": api_key})
        self.currency = currency.upper()
        self.log = log or (lambda *a: None)
        self.asset_ids: dict[str, str] = {}
        self.currency_id: str | None = None

    async def _get(self, path: str, **params):
        r = await self.client.get(path, params=params or None)
        if r.status_code >= 400:
            raise BitpandaError(f"GET {path} -> {r.status_code}: {r.text[:200]}")
        return r.json()

    async def _post(self, path: str, body: dict | None = None):
        r = await self.client.post(path, json=body)
        if r.status_code >= 400:
            raise BitpandaError(f"POST {path} -> {r.status_code}: {r.text[:200]}")
        return r.json()

    @staticmethod
    def _items(body) -> list[dict]:
        if isinstance(body, list):
            return body
        data = body.get("data", body)
        return data if isinstance(data, list) else [data]

    @staticmethod
    def _field(item: dict, *names):
        for n in names:
            v = item.get(n)
            if v is None and isinstance(item.get("attributes"), dict):
                v = item["attributes"].get(n)
            if v is not None:
                return v
        return None

    async def connect(self) -> dict:
        """Load asset and currency ids. Returns a short summary."""
        for item in self._items(await self._get("/assets")):
            sym = self._field(item, "symbol", "code", "ticker")
            aid = self._field(item, "id", "asset_id")
            if sym and aid:
                self.asset_ids.setdefault(str(sym).upper(), str(aid))
        for item in self._items(await self._get("/currencies")):
            sym = self._field(item, "symbol", "code")
            if sym and str(sym).upper() == self.currency:
                self.currency_id = str(self._field(item, "id", "currency_id"))
        if not self.currency_id:
            raise BitpandaError(f"currency {self.currency} not found on Bitpanda")
        return {"assets": len(self.asset_ids), "currency_id": self.currency_id}

    async def balances(self) -> dict[str, float]:
        """Available amounts per asset id plus fiat under the key 'FIAT'."""
        out: dict[str, float] = {}
        for item in self._items(await self._get("/portfolio")):
            avail = self._field(item, "available_balance") or {}
            value = float(avail.get("value", 0) if isinstance(avail, dict) else avail or 0)
            aid = self._field(item, "asset_id")
            cur = item.get("currency_balance") or {}
            if isinstance(cur, dict) and cur.get("currency_id") == self.currency_id:
                out["FIAT"] = max(out.get("FIAT", 0.0), float(cur.get("value", 0)))
            if aid:
                out[str(aid)] = out.get(str(aid), 0.0) + value
        return out

    async def _trade(self, symbol: str, side: str, *, notional: float | None = None, quantity: float | None = None) -> dict:
        if side not in ("BUY", "SELL"):
            raise BitpandaError("only spot BUY and SELL exist in this bot")
        aid = self.asset_ids.get(symbol.upper())
        if not aid:
            raise BitpandaError(f"{symbol} is not tradable on Bitpanda")
        body = {"asset_id": aid, "currency_id": self.currency_id, "side": side}
        if notional is not None:
            body["notional"] = f"{notional:.2f}"
        else:
            body["quantity"] = f"{quantity:.10f}".rstrip("0").rstrip(".")
        quote = await self._post("/quotes", body)
        q = quote.get("data", quote)
        qid = q.get("quote_id") or q.get("id")
        if not qid:
            raise BitpandaError(f"no quote id in response: {str(quote)[:200]}")
        res = await self._post(f"/quotes/{qid}/accept")
        return res.get("data", res)

    async def buy(self, symbol: str, fiat_amount: float) -> dict:
        bal = await self.balances()
        if fiat_amount > bal.get("FIAT", 0.0):
            raise BitpandaError("not enough fiat for this buy")  # never borrow
        return await self._trade(symbol, "BUY", notional=fiat_amount)

    async def sell_fraction(self, symbol: str, fraction: float) -> dict | None:
        bal = await self.balances()
        held = bal.get(self.asset_ids.get(symbol.upper(), "?"), 0.0)
        qty = held * max(0.0, min(1.0, fraction))
        if qty <= 0:
            return None  # nothing to sell, and never short
        return await self._trade(symbol, "SELL", quantity=qty)
