"""The market's mood, day by day: bull, bear, sideways or wild.

Plain math on the daily candles, using only what was known at each day's close:
- Bitcoin against its 50- and 200-day averages (the trend everything else follows),
- breadth: the share of coins above their own 50-day average (is the move broad or carried by a few?),
- Bitcoin's 30-day volatility, ranked against every earlier day since 2017 (how unusual is the turbulence?).
Wild (volatility in the top 15% of history) wins over the others: in such phases sizes and stops matter more
than direction. The Think Tank can use the same two numbers as building blocks (mood and wild).
"""
from __future__ import annotations

import bisect
import math

from . import research

LABELS = {"bull": "🐂 Bull", "bear": "🐻 Bear", "sideways": "〰️ Sideways", "wild": "🌪️ Wild"}
MEANING = {
    "bull": "Bitcoin above its 50- and 200-day averages and most coins rising with it: breakouts tend to follow through.",
    "bear": "Bitcoin below its 200-day average and most coins below their 50-day average: the daily brain mostly "
            "waits in cash.",
    "sideways": "No clear direction: breakouts often fail, the brain trades little.",
    "wild": "Turbulence in the top 15% of history: big swings both ways, gains and losses come fast.",
}


def _sma(xs: list, i: int, n: int) -> float | None:
    if i - n + 1 < 0:
        return None
    w = xs[i - n + 1:i + 1]
    return None if any(x is None for x in w) else sum(w) / n


def classify(cd: research.Candles) -> list[dict | None]:
    """One entry per day: label, mood (+1 bull, -1 bear, 0 otherwise), wild (volatility rank 0..1), breadth."""
    cache = cd.__dict__.get("_regime")
    if cache and cache[0] == len(cd.days):
        return cache[1]
    btc = cd.c.get("BTC")
    out: list[dict | None] = [None] * len(cd.days)
    if not btc:
        return out
    rets = [None] + [math.log(b / a) if a and b else None for a, b in zip(btc[:-1], btc[1:])]
    past_vols: list[float] = []
    for i in range(len(cd.days)):
        w = rets[max(0, i - 29):i + 1]
        vol = None
        if i >= 30 and None not in w:
            m = sum(w) / len(w)
            vol = math.sqrt(sum((x - m) ** 2 for x in w) / (len(w) - 1))
        s50, s200 = _sma(btc, i, 50), _sma(btc, i, 200)
        if vol is None or s50 is None or s200 is None or not btc[i]:
            if vol is not None:
                bisect.insort(past_vols, vol)
            continue
        wild = bisect.bisect_left(past_vols, vol) / len(past_vols) if len(past_vols) >= 200 else 0.5
        bisect.insort(past_vols, vol)
        above = [cd.c[s][i] > a for s in cd.coins
                 if cd.c[s][i] and (a := _sma(cd.c[s], i, 50)) is not None]
        breadth = sum(above) / len(above) if above else 0.5
        if wild >= 0.85:
            label = "wild"
        elif btc[i] > s50 and btc[i] > s200 and breadth >= 0.55:
            label = "bull"
        elif btc[i] < s200 and breadth < 0.45:
            label = "bear"
        else:
            label = "sideways"
        out[i] = {"label": label, "mood": {"bull": 1.0, "bear": -1.0}.get(label, 0.0), "wild": round(wild, 3),
                  "breadth": round(breadth, 3), "btc_vs_50": round(btc[i] / s50 - 1, 4),
                  "btc_vs_200": round(btc[i] / s200 - 1, 4)}
    cd.__dict__["_regime"] = (len(cd.days), out)
    return out


def summary(cd: research.Candles) -> dict:
    """Today's mood, how long it has lasted, the last 90 days, and what each mood meant for Bitcoin's next 30 days."""
    reg = classify(cd)
    idx = [i for i, r in enumerate(reg) if r]
    if not idx:
        return {}
    last = idx[-1]
    now = reg[last]
    since = last
    while since - 1 >= 0 and reg[since - 1] and reg[since - 1]["label"] == now["label"]:
        since -= 1
    btc = cd.c["BTC"]
    stats = {}
    for lab in LABELS:
        days = [i for i in idx if reg[i]["label"] == lab]
        fwd = [btc[i + 30] / btc[i] - 1 for i in days if i + 30 < len(btc) and btc[i] and btc[i + 30]]
        stats[lab] = {"share_pct": round(len(days) / len(idx) * 100, 1),
                      "btc_next30_pct": round(sum(fwd) / len(fwd) * 100, 1) if fwd else None,
                      "btc_next30_up_pct": round(sum(f > 0 for f in fwd) / len(fwd) * 100) if fwd else None}
    return {"label": now["label"], "name": LABELS[now["label"]], "meaning": MEANING[now["label"]],
            "since": cd.days[since], "days": last - since + 1, **{k: now[k] for k in ("wild", "breadth", "btc_vs_50", "btc_vs_200")},
            "recent": [reg[i]["label"] if reg[i] else None for i in range(max(0, last - 89), last + 1)],
            "stats": stats, "day": cd.days[last]}
