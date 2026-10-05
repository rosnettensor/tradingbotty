"""Free alternative data with years of daily history, for the Pattern Hunter and the history test.

Every series is one value per UTC day, cached in the database (kv "alt:<key>") and refreshed once a day.
Sources (all free, no key):
- Crypto Fear & Greed index (alternative.me, since 2018)
- Perpetual futures funding rates per coin (Binance futures, since 2019): how crowded and leveraged longs are
- Wikipedia page views (Wikimedia, since mid 2015): public attention
- Total stablecoin supply (DefiLlama): money parked on the sidelines, ready to buy coins
- Bitcoin hash rate (blockchain.com): miners' confidence and stress
A source that fails just keeps its cached history; nothing here can stop the bot.
"""
from __future__ import annotations

import asyncio
import calendar
import math
import random
import time
from urllib.parse import quote

FNG = "https://api.alternative.me/fng/"
FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate"
WIKI = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user"
STABLES = "https://stablecoins.llama.fi/stablecoincharts/all"
HASHRATE = "https://api.blockchain.info/charts/hash-rate"
UA = {"User-Agent": "TradingBotty/0.5 (hobby research bot; daily refresh)"}

# Wikipedia articles: market-wide attention, plus the coins whose article names are unambiguous
WIKI_PAGES = {"wiki:market": "Cryptocurrency", "wiki:BTC": "Bitcoin", "wiki:ETH": "Ethereum", "wiki:DOGE": "Dogecoin",
              "wiki:LTC": "Litecoin", "wiki:BCH": "Bitcoin_Cash", "wiki:SOL": "Solana_(blockchain_platform)",
              "wiki:ADA": "Cardano_(blockchain_platform)"}
FUNDING_START = 1567296000  # 2019-09-01: Binance perpetual funding history starts about here


def _day(ts: float) -> int:
    return int(ts // 86400 * 86400)


async def fetch_fear_greed(client) -> list[tuple[int, float]]:
    r = await client.get(FNG, params={"limit": 0, "format": "json"})
    r.raise_for_status()
    data = r.json().get("data") or []
    return sorted({_day(float(d["timestamp"])): float(d["value"]) for d in data}.items())


async def fetch_funding(client, sym: str, since: float) -> list[tuple[int, float]]:
    """Daily average of the 8-hourly funding rates (0.0001 = 0.01% per 8 hours, the neutral level)."""
    per_day: dict[int, list[float]] = {}
    start = int(since * 1000)
    for _ in range(40):  # 1000 rates = about 333 days per page
        r = await client.get(FUNDING, params={"symbol": f"{sym}USDT", "startTime": start, "limit": 1000})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise ValueError(str(data)[:120])
        for x in data:
            per_day.setdefault(_day(int(x["fundingTime"]) / 1000), []).append(float(x["fundingRate"]))
        if len(data) < 1000:
            break
        start = int(data[-1]["fundingTime"]) + 1
        await asyncio.sleep(0.3)
    return sorted((d, sum(v) / len(v)) for d, v in per_day.items())


async def fetch_wiki(client, page: str) -> list[tuple[int, float]]:
    end = time.strftime("%Y%m%d", time.gmtime())
    r = await client.get(f"{WIKI}/{quote(page, safe='')}/daily/20150701/{end}", headers=UA)
    r.raise_for_status()
    items = r.json().get("items") or []
    out = []
    for it in items:
        t = time.strptime(str(it["timestamp"])[:8], "%Y%m%d")
        out.append((_day(calendar.timegm(t)), float(it["views"])))
    return sorted(out)


async def fetch_stablecoins(client) -> list[tuple[int, float]]:
    r = await client.get(STABLES)
    r.raise_for_status()
    data = r.json()
    out = []
    for d in data if isinstance(data, list) else []:
        tot = d.get("totalCirculatingUSD") or d.get("totalCirculating") or {}
        v = tot.get("peggedUSD") if isinstance(tot, dict) else None
        if v:
            out.append((_day(float(d["date"])), float(v)))
    return sorted(out)


async def fetch_hashrate(client) -> list[tuple[int, float]]:
    r = await client.get(HASHRATE, params={"timespan": "all", "format": "json", "sampled": "false"})
    r.raise_for_status()
    data = r.json()
    return sorted({_day(float(v["x"])): float(v["y"]) for v in data.get("values") or [] if v.get("y")}.items())


def _store(db, key: str, rows: list[tuple[int, float]], merge: bool = False) -> int:
    old = {int(d): v for d, v in (db.get(f"alt:{key}") or [])} if merge else {}
    old.update({int(d): float(v) for d, v in rows if v is not None and math.isfinite(v)})
    today = _day(time.time())
    rows = sorted((d, v) for d, v in old.items() if d < today)  # today isn't over yet
    if rows:
        db.set(f"alt:{key}", [list(r) for r in rows])
    return len(rows)


async def update(client, db, coins: list[str], log=lambda *a: None) -> dict[str, int]:
    """Refresh every series once a day. Returns days of history per series."""
    status = db.get("alt_status") or {}
    if status.get("day") == _day(time.time()):
        return status.get("days", {})
    days: dict[str, int] = {}
    errors: list[str] = []

    async def one(key: str, coro, merge: bool = False):
        try:
            rows = await coro
            if not rows and not merge:
                raise ValueError("empty answer")
            days[key] = _store(db, key, rows, merge)
        except Exception as ex:
            errors.append(f"{key} ({str(ex)[:50] or type(ex).__name__})")
            days[key] = len(db.get(f"alt:{key}") or [])

    await one("fear_greed", fetch_fear_greed(client))
    await one("stablecoins", fetch_stablecoins(client))
    await one("hashrate", fetch_hashrate(client))
    for key, page in WIKI_PAGES.items():
        await one(key, fetch_wiki(client, page))
        await asyncio.sleep(0.2)
    for sym in coins:
        have = db.get(f"alt:funding:{sym}") or []
        since = have[-1][0] + 86400 if have else FUNDING_START
        if since < _day(time.time()):
            await one(f"funding:{sym}", fetch_funding(client, sym, since), merge=True)
        else:
            days[f"funding:{sym}"] = len(have)
    if errors:
        log("Pattern Hunter", "warn", "Some free data sources failed today, their cached history stays: "
            + ", ".join(errors[:6]))
    db.set("alt_status", {"day": _day(time.time()), "days": days, "errors": errors, "ts": time.time()})
    return days


def load(db, coins: list[str]) -> dict[str, dict[int, float]]:
    """Every cached series as {key: {day: value}}."""
    keys = ["fear_greed", "stablecoins", "hashrate", *WIKI_PAGES, *(f"funding:{c}" for c in coins)]
    out = {}
    for k in keys:
        rows = db.get(f"alt:{k}")
        if rows:
            out[k] = {int(d): float(v) for d, v in rows}
    return out


def synthetic(days: list[float], coins: list[str], seed: int = 11) -> dict[str, dict[int, float]]:
    """Random stand-ins for --simulate and tests (never shown as real findings)."""
    rng = random.Random(seed)
    out: dict[str, dict[int, float]] = {}
    fg, st, hr = 50.0, 1e10, 1e8
    out["fear_greed"], out["stablecoins"], out["hashrate"] = {}, {}, {}
    for d in days:
        fg = min(100, max(0, fg + rng.gauss(0, 6)))
        st *= math.exp(rng.gauss(0.001, 0.005))
        hr *= math.exp(rng.gauss(0.001, 0.02))
        out["fear_greed"][int(d)], out["stablecoins"][int(d)], out["hashrate"][int(d)] = fg, st, hr
    for k in WIKI_PAGES:
        out[k] = {int(d): max(1.0, rng.lognormvariate(9, 0.5)) for d in days}
    for c in coins:
        out[f"funding:{c}"] = {int(d): rng.gauss(0.0001, 0.0002) for d in days}
    return out
