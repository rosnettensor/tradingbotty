"""Live prices. Crypto from Kraken's free public API, US stocks from Yahoo's public chart API.

Everything here is real market data. Simulation mode (TB_SIMULATE=1) swaps in random-walk prices,
only for offline tests and demos; the dashboard shows a SIMULATED badge when it's on.
"""
from __future__ import annotations

import asyncio
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

KRAKEN = "https://api.kraken.com/0/public"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart"
UA = {"User-Agent": "Mozilla/5.0 TradingBotty/0.1"}

# Kraken uses its own names for a few coins
KRAKEN_PAIR = {"BTC": "XBTUSD", "DOGE": "XDGUSD"}


try:
    from zoneinfo import ZoneInfo
    NEW_YORK = ZoneInfo("America/New_York")
except Exception:  # no tz database: fall back to US daylight time
    NEW_YORK = timezone(timedelta(hours=-4))


def us_market_open(ts: float | None = None) -> bool:
    """Regular US session, Monday to Friday 9:30-16:00 New York time. Holidays are caught by the stale-price check."""
    t = datetime.fromtimestamp(ts or time.time(), NEW_YORK)
    if t.weekday() >= 5:
        return False
    minutes = t.hour * 60 + t.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


def kraken_pair(symbol: str) -> str:
    return KRAKEN_PAIR.get(symbol, f"{symbol}USD")


def _norm_kraken_key(key: str) -> str:
    for a, b in (("XXBT", "XBT"), ("XETH", "ETH"), ("XXRP", "XRP"), ("XXDG", "XDG"), ("XLTC", "LTC"), ("ZUSD", "USD")):
        key = key.replace(a, b)
    return key


@dataclass
class Quote:
    symbol: str
    kind: str                     # crypto | stock
    price: float = 0.0
    change_24h_pct: float = 0.0
    volume_24h: float = 0.0
    updated: float = 0.0
    # 1-minute closes, newest last
    candles: deque = field(default_factory=lambda: deque(maxlen=1440))
    always_open: bool = False     # simulation only: stocks trade around the clock

    def push(self, ts: float, price: float) -> None:
        minute = int(ts // 60) * 60
        if self.candles and self.candles[-1][0] == minute:
            self.candles[-1] = (minute, price)
        else:
            self.candles.append((minute, price))
        self.price = price
        self.updated = ts

    def closes(self) -> list[float]:
        return [c[1] for c in self.candles]

    def merge(self, rows: list[tuple[float, float]]) -> None:
        """Mix older history (database, exchange backfill) into the candles, keeping one close per minute."""
        merged = {int(ts // 60 * 60): c for ts, c in rows if c}
        merged.update({ts: c for ts, c in self.candles})
        keep = sorted(merged.items())[-self.candles.maxlen:]
        self.candles = deque(keep, maxlen=self.candles.maxlen)
        if keep and not self.price:
            self.price = keep[-1][1]

    @property
    def tradable(self) -> bool:
        """Crypto trades 24/7. Stocks only in the US session, and only with a fresh price."""
        if self.kind == "crypto":
            return self.price > 0
        return self.always_open or (us_market_open() and time.time() - self.updated < 600)


class PriceFeed:
    def __init__(self, crypto: list[str], stocks: list[str], simulate: bool = False, log=None):
        self.quotes: dict[str, Quote] = {s: Quote(s, "crypto") for s in crypto}
        self.quotes.update({s: Quote(s, "stock", always_open=simulate) for s in stocks})
        self.simulate = simulate
        self.log = log or (lambda *a: None)
        self.healthy = {"crypto": False, "stock": False}
        self.client = httpx.AsyncClient(timeout=10, headers=UA)
        self._sim_state: dict[str, float] = {}

    def crypto(self) -> list[Quote]:
        return [q for q in self.quotes.values() if q.kind == "crypto"]

    def stocks(self) -> list[Quote]:
        return [q for q in self.quotes.values() if q.kind == "stock"]

    def price(self, symbol: str) -> float:
        q = self.quotes.get(symbol)
        return q.price if q else 0.0

    # ---------- startup history so indicators work immediately ----------
    async def backfill(self) -> None:
        if self.simulate:
            self._sim_backfill()
            return
        for q in self.crypto():
            try:
                await self._backfill_crypto(q)
            except Exception as e:  # keep going, one coin missing is fine
                self.log("Data", "warn", f"No history for {q.symbol}: {e}")
            await asyncio.sleep(1.1)  # Kraken public rate limit
        await self.poll_stocks("5d")

    async def _backfill_crypto(self, q: Quote) -> None:
        r = await self.client.get(f"{KRAKEN}/OHLC", params={"pair": kraken_pair(q.symbol), "interval": 1})
        result = r.json().get("result", {})
        rows = next((v for k, v in result.items() if k != "last"), [])
        q.merge([(float(row[0]), float(row[4])) for row in rows])

    # ---------- live polling ----------
    async def poll_crypto(self) -> None:
        if self.simulate:
            self._sim_tick(self.crypto())
            self.healthy["crypto"] = True
            return
        pairs = {kraken_pair(q.symbol): q for q in self.crypto()}
        try:
            r = await self.client.get(f"{KRAKEN}/Ticker", params={"pair": ",".join(pairs)})
            body = r.json()
            if body.get("error"):
                raise RuntimeError(body["error"])
            now = time.time()
            for key, t in body["result"].items():
                q = pairs.get(key) or pairs.get(_norm_kraken_key(key))
                if not q:
                    continue
                last, open_ = float(t["c"][0]), float(t["o"])
                q.change_24h_pct = (last - open_) / open_ * 100 if open_ else 0.0
                q.volume_24h = float(t["v"][1]) * last
                q.push(now, last)
            self.healthy["crypto"] = True
        except Exception as e:
            self.healthy["crypto"] = False
            self.log("Data", "warn", f"Kraken price poll failed: {e}")

    async def poll_stocks(self, history: str = "1d") -> None:
        if self.simulate:
            self._sim_tick(self.stocks(), vol=0.0006)
            self.healthy["stock"] = True
            return
        ok = False
        for q in self.stocks():
            try:
                await self._poll_stock(q, history)
                ok = True
            except Exception as e:
                self.log("Data", "warn", f"Stock price for {q.symbol} failed: {e}")
        self.healthy["stock"] = ok

    async def _poll_stock(self, q: Quote, history: str = "1d") -> None:
        r = await self.client.get(f"{YAHOO}/{q.symbol}", params={"interval": "1m", "range": history})
        res = r.json()["chart"]["result"][0]
        meta = res["meta"]
        ts = res.get("timestamp") or []
        closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        q.merge([(float(t), float(c)) for t, c in zip(ts, closes) if c is not None])
        price = float(meta.get("regularMarketPrice") or q.price)
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
        q.change_24h_pct = (price - prev) / prev * 100 if prev else 0.0
        # stamp the price with the exchange's own trade time, so a closed market doesn't look fresh
        q.push(float(meta.get("regularMarketTime") or time.time()), price)

    # ---------- watchlist changes from the dashboard ----------
    async def validate(self, symbol: str, kind: str) -> str | None:
        """None if the symbol has live prices, else a reason."""
        if self.simulate:
            return None
        try:
            if kind == "crypto":
                r = await self.client.get(f"{KRAKEN}/Ticker", params={"pair": kraken_pair(symbol)})
                body = r.json()
                return f"Kraken doesn't list {symbol}/USD" if body.get("error") or not body.get("result") else None
            r = await self.client.get(f"{YAHOO}/{symbol}", params={"interval": "1d", "range": "5d"})
            res = (r.json().get("chart") or {}).get("result")
            return None if res and res[0]["meta"].get("regularMarketPrice") else f"Yahoo has no price for {symbol}"
        except Exception as e:
            return f"couldn't check {symbol}: {e}"

    async def add_symbol(self, symbol: str, kind: str, history: list[tuple[float, float]] | None = None) -> None:
        q = Quote(symbol, kind, always_open=self.simulate and kind == "stock")
        if history:
            q.merge(history)
        self.quotes[symbol] = q
        if self.simulate:
            self._sim_backfill([q])
            return
        try:
            if kind == "crypto":
                await self._backfill_crypto(q)
            else:
                await self._poll_stock(q, "5d")
        except Exception as e:
            self.log("Data", "warn", f"No history for {symbol}: {e}")

    def remove_symbol(self, symbol: str) -> None:
        self.quotes.pop(symbol, None)

    # ---------- simulation (offline only) ----------
    def _sim_backfill(self, quotes: list[Quote] | None = None) -> None:
        now = time.time()
        for q in quotes or list(self.quotes.values()):
            base = {"BTC": 62000, "ETH": 2500, "SOL": 140, "SPY": 570, "NVDA": 120}.get(q.symbol, random.uniform(0.5, 300))
            p = base
            for i in range(1440, 0, -1):
                p *= math.exp(random.gauss(0, 0.002 if q.kind == "crypto" else 0.0006))
                q.push(now - i * 60, p)
            self._sim_state[q.symbol] = p

    def _sim_tick(self, quotes: list[Quote], vol: float = 0.0025) -> None:
        now = time.time()
        for q in quotes:
            p = q.price or 100.0
            shock = random.gauss(0, vol)
            if random.random() < 0.01:
                shock += random.choice([-1, 1]) * vol * 8  # occasional pump or dump
            p *= math.exp(shock)
            first = q.candles[0][1] if q.candles else p
            q.change_24h_pct = (p - first) / first * 100
            q.volume_24h = abs(shock) * 1e9
            q.push(now, p)
