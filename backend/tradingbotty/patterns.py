"""The Pattern Hunter: does any signal really tell us something about the next week's price?

Every candidate (alternative data, price patterns, and two random "control" series) is measured the same way:
- Only data known by day t is used; the outcome is the return from day t+1's open to day t+7's close.
- Weeks don't overlap, so one big move isn't counted seven times.
- Per-coin signals: each week, do coins with a higher value do better than the others (rank correlation)?
- Market-wide signals: in weeks with a higher value, does Bitcoin do better (rank correlation)?
- A finding counts only if it is strong after correcting for how many signals were tried (Bonferroni), points
  the same way in both halves of history, and holds in most calendar years. The random controls show what
  pure chance looks like: they should always fail.
"""
from __future__ import annotations

import math
import random
import time
from statistics import NormalDist

from .research import Candles

HORIZON = 7


def _rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda k: xs[k])
    r = [0.0] * len(xs)
    k = 0
    while k < len(order):
        j = k
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[k]]:
            j += 1
        for m in range(k, j + 1):
            r[order[m]] = (k + j) / 2
        k = j + 1
    return r


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 4:
        return None
    ra, rb = _rank(a), _rank(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return cov / (va * vb) if va and vb else None


def fwd_return(cd: Candles, sym: str, i: int, h: int = HORIZON) -> float | None:
    if i + h >= len(cd.days):
        return None
    o, c = cd.o[sym][i + 1], cd.c[sym][i + h]
    return c / o - 1 if o and c else None


# ------------------------------------------------------------------ candidate signals (value on day i)
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _col_mean(cd, key, i, n):
    return _mean([cd.feat(key, k) for k in range(max(0, i - n + 1), i + 1)])


def _close_ratio(cd, sym, i, n):
    xs = cd.closes(sym, i, n + 1)
    return xs[-1] / xs[0] - 1 if xs else None


def _attention(cd, key, i):
    short, long_ = _col_mean(cd, key, i, 7), _col_mean(cd, key, i, 90)
    return math.log(short / long_) if short and long_ else None


def _dist_high(cd, sym, i):
    hs = [x for x in cd.h[sym][max(0, i - 20):i] if x is not None]
    c = cd.c[sym][i]
    return c / max(hs) - 1 if hs and c else None


def _vol(cd, sym, i):
    xs = cd.closes(sym, i, 31)
    if not xs:
        return None
    r = [math.log(b / a) for a, b in zip(xs[:-1], xs[1:])]
    m = sum(r) / len(r)
    return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1))


def _ma_ratio(cd, key, i, a, b):
    x, y = _col_mean(cd, key, i, a), _col_mean(cd, key, i, b)
    return x / y - 1 if x and y else None


def candidates(seed: int = 3) -> list[dict]:
    """name, kind ('coin' = compared across coins, 'market' = one value for the whole market), how to compute it,
    and what the idea behind it is."""
    rnd_m, rnd_c = random.Random(seed), random.Random(seed + 1)
    noise_m: dict[int, float] = {}
    noise_c: dict[tuple, float] = {}
    return [
        {"name": "Funding rate, last 7 days", "kind": "coin", "source": "Binance futures",
         "idea": "High funding = many leveraged buyers. Crowded trades often reverse.",
         "f": lambda cd, s, i: _col_mean(cd, f"funding:{s}", i, 7)},
        {"name": "Funding rate change vs last month", "kind": "coin", "source": "Binance futures",
         "idea": "Leverage building up fast is a warning sign.",
         "f": lambda cd, s, i: (lambda a, b: a - b if a is not None and b is not None else None)(
             _col_mean(cd, f"funding:{s}", i, 7), _col_mean(cd, f"funding:{s}", i, 30))},
        {"name": "Wikipedia attention spike (coin)", "kind": "coin", "source": "Wikipedia",
         "idea": "Sudden public attention: early momentum or the top of a hype?",
         "f": lambda cd, s, i: _attention(cd, f"wiki:{s}", i)},
        {"name": "30-day strength", "kind": "coin", "source": "prices",
         "idea": "Known momentum effect: strong coins tend to stay strong for a while.",
         "f": lambda cd, s, i: _close_ratio(cd, s, i, 30)},
        {"name": "7-day strength", "kind": "coin", "source": "prices",
         "idea": "Short-term moves often partly reverse.",
         "f": lambda cd, s, i: _close_ratio(cd, s, i, 7)},
        {"name": "Distance to the 20-day high", "kind": "coin", "source": "prices",
         "idea": "Coins at a new high (the breakout rule) vs coins far below it.",
         "f": _dist_high},
        {"name": "Volatility, last 30 days", "kind": "coin", "source": "prices",
         "idea": "Do wild coins pay for their risk, or just lose more?",
         "f": _vol},
        {"name": "Random number (control)", "kind": "coin", "source": "chance",
         "idea": "Pure chance, as a yardstick. Must always fail.",
         "f": lambda cd, s, i: noise_c.setdefault((s, i), rnd_c.gauss(0, 1))},
        {"name": "Fear & Greed level", "kind": "market", "source": "alternative.me",
         "idea": "Extreme fear as a buying chance, extreme greed as a warning.",
         "f": lambda cd, s, i: cd.feat("fear_greed", i)},
        {"name": "Fear & Greed, change over a week", "kind": "market", "source": "alternative.me",
         "idea": "Mood turning fast.",
         "f": lambda cd, s, i: (lambda a, b: a - b if a is not None and b is not None else None)(
             cd.feat("fear_greed", i), cd.feat("fear_greed", i - 7) if i >= 7 else None)},
        {"name": "Stablecoin supply growth, 30 days", "kind": "market", "source": "DefiLlama",
         "idea": "New dollars parked in crypto, ready to buy coins.",
         "f": lambda cd, s, i: (lambda a, b: b / a - 1 if a and b else None)(
             cd.feat("stablecoins", i - 30) if i >= 30 else None, cd.feat("stablecoins", i))},
        {"name": "Hash ribbon (hash rate 30 vs 60 days)", "kind": "market", "source": "blockchain.com",
         "idea": "Miners under stress switch off machines; their recovery has been called a buy signal.",
         "f": lambda cd, s, i: _ma_ratio(cd, "hashrate", i, 30, 60)},
        {"name": "Bitcoin attention spike (Wikipedia)", "kind": "market", "source": "Wikipedia",
         "idea": "The public suddenly reading about Bitcoin.",
         "f": lambda cd, s, i: _attention(cd, "wiki:BTC", i)},
        {"name": "Crypto attention spike (Wikipedia)", "kind": "market", "source": "Wikipedia",
         "idea": "Interest in crypto as a whole.",
         "f": lambda cd, s, i: _attention(cd, "wiki:market", i)},
        {"name": "Random number (control)", "kind": "market", "source": "chance",
         "idea": "Pure chance, as a yardstick. Must always fail.",
         "f": lambda cd, s, i: noise_m.setdefault(i, rnd_m.gauss(0, 1))},
    ]


def _stats(points: list[tuple[int, float]]) -> tuple[float, float, int]:
    """points = (day index, statistic per week). Returns mean, t-value, count."""
    xs = [x for _, x in points]
    n = len(xs)
    if n < 8:
        return 0.0, 0.0, n
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) or 1e-12
    return m, m / sd * math.sqrt(n), n


def analyse(cd: Candles, start: int = 100) -> dict:
    nd = NormalDist()
    cands = candidates()
    days = list(range(start, len(cd.days) - HORIZON - 1, HORIZON))
    rows = []
    for c in cands:
        weekly: list[tuple[int, float]] = []      # one statistic per week
        spread: list[float] = []                  # top fifth minus bottom fifth, return per week
        if c["kind"] == "coin":
            for i in days:
                pairs = []
                for s in cd.coins:
                    v, r = c["f"](cd, s, i), fwd_return(cd, s, i)
                    if v is not None and r is not None:
                        pairs.append((v, r))
                if len(pairs) < 6:
                    continue
                ic = spearman([p[0] for p in pairs], [p[1] for p in pairs])
                if ic is None:
                    continue
                weekly.append((i, ic))
                pairs.sort()
                q = max(1, len(pairs) // 5)
                spread.append(sum(r for _, r in pairs[-q:]) / q - sum(r for _, r in pairs[:q]) / q)
            mean, t, n = _stats(weekly)
        else:
            vals = []
            for i in days:
                v, r = c["f"](cd, "BTC", i), fwd_return(cd, "BTC", i)
                if v is not None and r is not None:
                    vals.append((i, v, r))
            n = len(vals)
            rho = spearman([v for _, v, _ in vals], [r for _, _, r in vals]) if n >= 8 else None
            mean = rho or 0.0
            t = mean * math.sqrt(max(n - 2, 1) / max(1e-9, 1 - mean * mean)) if rho is not None else 0.0
            weekly = [(i, (v, r)) for i, v, r in vals]
            if n >= 10:
                srt = sorted(vals, key=lambda x: x[1])
                q = max(1, n // 5)
                spread = [sum(x[2] for x in srt[-q:]) / q - sum(x[2] for x in srt[:q]) / q]
        if n < 8:
            rows.append({"name": c["name"], "kind": c["kind"], "source": c["source"], "idea": c["idea"],
                         "weeks": n, "verdict": "no data"})
            continue
        p = 2 * (1 - nd.cdf(abs(t)))

        def sub(lo: int, hi: int) -> float | None:
            part = [w for w in weekly if lo <= w[0] < hi]
            if c["kind"] == "coin":
                return _stats(part)[0] if len(part) >= 8 else None
            return spearman([x[1][0] for x in part], [x[1][1] for x in part]) if len(part) >= 8 else None

        first, last = weekly[0][0], weekly[-1][0] + 1
        mid = (first + last) // 2
        h1, h2 = sub(first, mid), sub(mid, last)
        years: dict[int, list] = {}
        for w in weekly:
            years.setdefault(time.gmtime(cd.days[w[0]]).tm_year, []).append(w)
        signs = []
        for y, part in years.items():
            if len(part) >= 10:
                v = (_stats(part)[0] if c["kind"] == "coin"
                     else spearman([x[1][0] for x in part], [x[1][1] for x in part]))
                if v is not None:
                    signs.append(v)
        same = sum(1 for v in signs if v * mean > 0)
        rows.append({"name": c["name"], "kind": c["kind"], "source": c["source"], "idea": c["idea"],
                     "weeks": n, "corr": round(mean, 3), "t": round(t, 2), "p": round(p, 4),
                     "first_half": None if h1 is None else round(h1, 3),
                     "second_half": None if h2 is None else round(h2, 3),
                     "years_same": same, "years_total": len(signs),
                     "spread_pct": round(sum(spread) / len(spread) * 100, 2) if spread else None,
                     "since": time.strftime("%Y-%m-%d", time.gmtime(cd.days[weekly[0][0]]))})
    tested = [r for r in rows if r.get("verdict") != "no data"]
    for r in tested:
        r["p_corrected"] = round(min(1.0, r["p"] * len(tested)), 4)
        halves = r["first_half"] is not None and r["second_half"] is not None and r["first_half"] * r["second_half"] > 0
        steady = r["years_total"] and r["years_same"] >= math.ceil(r["years_total"] * 0.6)
        if r["p_corrected"] < 0.05 and halves and steady:
            r["verdict"] = "pattern"
        elif r["p"] < 0.05 and halves:
            r["verdict"] = "hint"
        else:
            r["verdict"] = "chance"
        r["direction"] = "higher value, better week" if r["corr"] > 0 else "higher value, worse week"
    order = {"pattern": 0, "hint": 1, "chance": 2, "no data": 3}
    rows.sort(key=lambda r: (order[r["verdict"]], -abs(r.get("t", 0))))
    return {"ts": time.time(), "horizon_days": HORIZON, "tested": len(tested),
            "from": time.strftime("%Y-%m-%d", time.gmtime(cd.days[start])) if len(cd.days) > start else None,
            "to": time.strftime("%Y-%m-%d", time.gmtime(cd.days[-1])), "rows": rows}
