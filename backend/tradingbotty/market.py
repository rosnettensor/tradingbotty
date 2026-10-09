"""The market at a glance, for the Pulse tab: every coin of the test universe with its moves, risk and distance to
the brain's rules, how the coins move together, and the market's breadth day by day.

Plain math on the daily candles (plus today's live price where Fusion has one). Nothing here trades."""
from __future__ import annotations

import math
import time

from . import regime, research

PERIODS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "1y": 365}


def _closes(xs: list, n: int) -> list[float]:
    return [x for x in xs[-n:] if x]


def _rets(xs: list[float | None]) -> list[float | None]:
    return [None] + [math.log(b / a) if a and b else None for a, b in zip(xs[:-1], xs[1:])]


def _corr(a: list, b: list) -> float | None:
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    n = len(pairs)
    if n < 20:
        return None
    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    syy = sum((y - my) ** 2 for _, y in pairs)
    return round(sxy / math.sqrt(sxx * syy), 3) if sxx and syy else None


def _sma(xs: list, i: int, n: int) -> float | None:
    w = xs[max(0, i - n + 1):i + 1]
    return sum(w) / n if len(w) == n and None not in w else None


def coins(cd: research.Candles, live: dict[str, float] | None = None) -> list[dict]:
    """One row per coin: price, moves over several periods, risk, the brain's lines, Bitcoin's pull."""
    live = live or {}
    btc_r = _rets(cd.c.get("BTC", []))[-90:]
    out = []
    for sym in cd.coins:
        c, h, l = cd.c[sym], cd.h[sym], cd.l[sym]
        last = c[-1]
        if not last or sum(1 for x in c[-120:] if x) < 60:
            continue
        price = live.get(sym) or last
        if not last / 2 < price < last * 2:   # a broken quote (or a feed with other units): keep the daily close
            price = last
        is_live = price != last and sym in live
        moves = {}
        for k, n in PERIODS.items():
            ref = c[-1 - n] if len(c) > n else None
            moves[k] = round((price / ref - 1) * 100, 2) if ref else None
        r30 = [x for x in _rets(c)[-30:] if x is not None]
        vol = math.sqrt(sum((x - sum(r30) / len(r30)) ** 2 for x in r30) / (len(r30) - 1)) * math.sqrt(365) * 100 \
            if len(r30) > 5 else None
        hi20 = max((x for x in h[-21:-1] if x), default=None)   # the brain buys a close above the 20 days before today
        lo10 = min((x for x in l[-11:-1] if x), default=None)
        ath = max((x for x in c if x), default=None)
        sma50 = _sma(c, len(c) - 1, 50)
        corr = _corr(_rets(c)[-90:], btc_r) if sym != "BTC" else 1.0
        spark = _closes(c, 90)
        out.append({
            "symbol": sym, "price": price, "live": is_live, "moves": moves,
            "vol": round(vol, 1) if vol is not None else None,
            "to_high": round((price / hi20 - 1) * 100, 2) if hi20 else None,
            "to_low": round((price / lo10 - 1) * 100, 2) if lo10 else None,
            "from_ath": round((price / ath - 1) * 100, 1) if ath else None,
            "above50": bool(sma50 and price > sma50), "vs50": round((price / sma50 - 1) * 100, 2) if sma50 else None,
            "btc_corr": corr,
            "spark": [round(x / spark[0], 4) for x in spark] if spark else [],
        })
    return out


def matrix(cd: research.Candles, days: int = 90) -> dict:
    """How the coins moved together over the last `days` (correlation of daily returns), most alike next to each other."""
    rets = {s: _rets(cd.c[s])[-days:] for s in cd.coins if sum(1 for x in cd.c[s][-days:] if x) >= days * 0.8}
    syms = list(rets)
    m = {(a, b): _corr(rets[a], rets[b]) for a in syms for b in syms if a <= b}
    get = lambda a, b: 1.0 if a == b else m.get((min(a, b), max(a, b)))  # noqa: E731
    # order: start at Bitcoin, then always the coin most like the last one placed (a simple seriation)
    order = ["BTC"] if "BTC" in syms else syms[:1]
    while len(order) < len(syms):
        last = order[-1]
        order.append(max((s for s in syms if s not in order), key=lambda s: get(last, s) or -2))
    return {"days": days, "symbols": order, "values": [[get(a, b) for b in order] for a in order]}


def breadth(cd: research.Candles, days: int = 365) -> list[dict]:
    """The market's health day by day: share of coins above their 50-day average and at a 20-day high, Bitcoin's
    price and the mood the Regime Radar gave that day."""
    reg = regime.classify(cd)
    btc = cd.c.get("BTC", [])
    out = []
    for i in range(max(60, len(cd.days) - days), len(cd.days)):
        above = highs = n = 0
        for s in cd.coins:
            c, h = cd.c[s], cd.h[s]
            if not c[i]:
                continue
            a = _sma(c, i, 50)
            prev = [x for x in h[max(0, i - 20):i] if x]
            if a is None or len(prev) < 15:
                continue
            n += 1
            above += c[i] > a
            highs += c[i] > max(prev)
        if not n:
            continue
        r = reg[i] or {}
        out.append({"day": cd.days[i], "above50": round(above / n * 100, 1), "highs": round(highs / n * 100, 1),
                    "btc": btc[i], "mood": r.get("label"), "wild": r.get("wild")})
    return out


def snapshot(cd: research.Candles, live: dict[str, float] | None = None) -> dict:
    """Everything the Pulse tab draws. The heavy parts are cached per daily candle."""
    cache = cd.__dict__.get("_pulse")
    if not cache or cache[0] != len(cd.days):
        cache = (len(cd.days), matrix(cd), breadth(cd))
        cd.__dict__["_pulse"] = cache
    rows = coins(cd, live)
    up = [r for r in rows if (r["moves"]["1d"] or 0) > 0]
    return {"ts": time.time(), "day": cd.days[-1], "coins": rows, "matrix": cache[1], "breadth": cache[2],
            "summary": {"coins": len(rows), "up_1d": len(up), "above50": sum(r["above50"] for r in rows),
                        "near_high": sum(1 for r in rows if r["to_high"] is not None and r["to_high"] > -3),
                        "avg_corr": round(sum(r["btc_corr"] or 0 for r in rows if r["symbol"] != "BTC")
                                          / max(1, len(rows) - 1), 2)}}
