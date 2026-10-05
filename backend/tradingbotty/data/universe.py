"""The coin universe: every coin with a USD market on Kraken, checked against what Bitpanda Fusion can trade.

One Kraken call returns all tickers at once (price, 24h range, volume, bid and ask), so scanning a few hundred
coins costs two requests, not hundreds. The Market Radar ranks them and only the hottest get watched closely.
"""
from __future__ import annotations

import math
import random
import time

import httpx

from . import prices as P

# Not worth trading: stablecoins, fiat, wrapped or pegged copies of other coins
SKIP = {
    "USDT", "USDC", "DAI", "PYUSD", "TUSD", "USDD", "FDUSD", "USDE", "USDS", "USDG", "RLUSD", "USDQ", "USDR", "UST",
    "EURT", "EURC", "EUROC", "EURR", "EURQ", "EUR", "GBP", "CHF", "CAD", "AUD", "JPY", "AED", "ZUSD", "ZEUR", "ZGBP",
    "WBTC", "TBTC", "CBBTC", "WETH", "STETH", "WSTETH", "METH", "CMETH", "LSETH", "PAXG", "XAUT", "GHO", "LUSD",
}
ALIASES = {"XBT": "BTC", "XDG": "DOGE"}  # Kraken's names -> everyone else's


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


class Universe:
    def __init__(self, simulate: bool = False, log=None):
        self.simulate = simulate
        self.log = log or (lambda *a: None)
        self.client = httpx.AsyncClient(timeout=20, headers=P.UA)
        self.pairs: dict[str, dict] = {}    # symbol -> {"key": Kraken pair key, "alt": altname}
        self.pairs_ts = 0.0
        self.rows: list[dict] = []          # last scan, every usable coin
        self.ts = 0.0
        self.fusion: set[str] | None = None  # coins Fusion trades in our currency (None = unknown, no key)

    async def _load_pairs(self) -> None:
        if self.pairs and time.time() - self.pairs_ts < 6 * 3600:
            return
        r = await self.client.get(f"{P.KRAKEN}/AssetPairs")
        body = r.json()
        if body.get("error"):
            raise RuntimeError(body["error"])
        pairs = {}
        for key, p in body.get("result", {}).items():
            ws = str(p.get("wsname") or "")
            if "/" not in ws or not ws.endswith("/USD") or key.endswith(".d") or p.get("status", "online") != "online":
                continue
            base = ws.split("/")[0].upper()
            sym = ALIASES.get(base, base)
            if sym in SKIP or not sym.isalnum():
                continue
            pairs[sym] = {"key": key, "alt": p.get("altname") or key}
        if not pairs:
            raise RuntimeError("Kraken returned no USD pairs")
        self.pairs, self.pairs_ts = pairs, time.time()
        for sym, p in pairs.items():  # teach the price feed the exact pair names
            P.KRAKEN_PAIR.setdefault(sym, p["alt"])
            P.PAIR_KEYS[p["key"]] = sym
            P.PAIR_KEYS[p["alt"]] = sym

    async def scan(self) -> list[dict]:
        """All coins with their 24h stats. Raises if Kraken can't be reached."""
        if self.simulate:
            self.rows, self.ts = self._sim_rows(), time.time()
            return self.rows
        await self._load_pairs()
        r = await self.client.get(f"{P.KRAKEN}/Ticker")
        body = r.json()
        if body.get("error"):
            raise RuntimeError(body["error"])
        by_key = {p["key"]: s for s, p in self.pairs.items()}
        rows = []
        for key, t in body.get("result", {}).items():
            sym = by_key.get(key)
            if not sym:
                continue
            last, open_ = _num(t.get("c", [0])[0]), _num(t.get("o"))
            ask, bid = _num(t.get("a", [0])[0]), _num(t.get("b", [0])[0])
            hi, lo = _num(t.get("h", [0, 0])[1]), _num(t.get("l", [0, 0])[1])
            vol = _num(t.get("v", [0, 0])[1])
            if last <= 0:
                continue
            mid = (ask + bid) / 2 if ask and bid else last
            rows.append({
                "symbol": sym, "price": last, "change": (last - open_) / open_ * 100 if open_ else 0.0,
                "volume_usd": vol * last, "spread_pct": (ask - bid) / mid * 100 if ask and bid else 9.9,
                "range_pos": (last - lo) / (hi - lo) if hi > lo else 0.5, "high": hi, "low": lo,
            })
        self.rows, self.ts = rows, time.time()
        return rows

    def _sim_rows(self) -> list[dict]:
        rnd = random.Random(int(time.time() // 600))
        names = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "PEPE"] + [
            f"SIM{i:02d}" for i in range(60)]
        out = []
        for i, s in enumerate(names):
            vol = 10 ** rnd.uniform(4.5, 9.5 if i < 10 else 8)
            out.append({"symbol": s, "price": 10 ** rnd.uniform(-4, 4), "change": rnd.gauss(0, 6),
                        "volume_usd": vol, "spread_pct": max(0.01, 40 / math.sqrt(vol)),
                        "range_pos": rnd.random(), "high": 0, "low": 0})
        return out
