"""The Think Tank: original strategy ideas, tested without mercy.

Ideas come from three places: Claude (asked for original concepts from physics, biology, information theory and
anything else, never the standard indicators), evolution (mutating and crossing the best ideas so far, endlessly),
and the owner (Ralph's price echo). Every idea is a formula in a small, safe language: a score per coin and day,
built only from what is known at that day's close. The coins with the highest scores are held.

How an idea is judged, on the daily history since 2017 with Fusion's fees and spread:
- discovery (the first 60% of the days): evolution and Claude only ever see these results;
- holdout (the last 40%): never used to pick or improve anything, it decides the verdict;
- the signal itself (does a higher score really mean a better next week, across coins?) in both parts;
- a candidate must beat holding Bitcoin in both parts, survive twice the fees, pass the multiple-testing correction
  over every idea ever tried here, and keep working when a third of the coins is left out.
Nothing here trades. A candidate can be added to the history lab, where it faces the full robustness bar like any
other strategy before it could ever get real money.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import random
import re
import time
from statistics import median

from . import research

WINDOWS = (3, 5, 10, 20, 50, 100)
SPLIT = 0.6

# name -> (needs a window, what it means). Every value at day i uses only data up to day i's close.
FEATURES = {
    "ret": (True, "return over the last n days"),
    "vol": (True, "volatility: standard deviation of daily returns over n days"),
    "z": (True, "how many standard deviations the price is above its n-day average"),
    "dhigh": (True, "distance below the n-day high (0 = at the high, -0.2 = 20% below)"),
    "dlow": (True, "distance above the n-day low"),
    "range": (True, "average daily high-low range over n days, as a share of the price"),
    "body": (True, "average candle body (close vs open) over n days: who wins the day, buyers or sellers"),
    "wick": (True, "upper wicks minus lower wicks over n days: selling pressure above vs buying below"),
    "vratio": (True, "variance ratio of 5-day vs 1-day moves over n days: above 1 trending, below 1 mean-reverting"),
    "autoc": (True, "autocorrelation of daily returns over n days: does today repeat yesterday?"),
    "skew": (True, "skew of daily returns over n days: rare big jumps up (positive) or down (negative)"),
    "entropy": (True, "Shannon entropy of the up/down pattern over n days: 1 = pure noise, low = a rhythm"),
    "rel": (True, "return over n days minus Bitcoin's"),
    "btcret": (True, "Bitcoin's return over n days (the same for every coin)"),
    "breadth": (True, "share of all coins up over n days (market mood, same for every coin)"),
    "disp": (True, "dispersion: how differently the coins moved over n days (same for every coin)"),
    "echo": (True, "Ralph's price echo: the average n-day move after every earlier visit to today's price (+-3%)"),
    "gap": (False, "today's open vs yesterday's close"),
    "streak": (False, "days in a row up (positive) or down (negative)"),
    "dow": (False, "day of the week, 0 = Monday"),
    "moon": (False, "moon phase, 1 = full moon, -1 = new moon (a control: it should fail)"),
    "cycle": (False, "position in Bitcoin's 4-year halving cycle, 0 to 1"),
    "mood": (False, "the Regime Radar's market mood: 1 bull, -1 bear, 0 sideways or wild (same for every coin)"),
    "wild": (False, "how turbulent Bitcoin is vs all history, 0 calm to 1 the wildest (same for every coin)"),
}
UNARY = {"rank": "rank among all coins that day, 0 to 1", "demean": "minus the average over all coins that day",
         "abs": "absolute value", "sign": "-1, 0 or 1", "log1p": "log(1 + x)", "sq": "x squared", "neg": "minus x"}
SERIES = {"lag": "the value k days earlier", "smooth": "average of the last k values", "delta": "change over k days"}
BINARY = {"max": "larger of two", "min": "smaller of two", "gt": "1 if a > b else 0", "lt": "1 if a < b else 0"}
HALVINGS = [1468022400, 1589155200, 1713571200, 1713571200 + 1460 * 86400]
NEW_MOON = 947182440  # 2000-01-06 18:14 UTC
LUNAR = 29.530588 * 86400


# ------------------------------------------------------------------ the formula language
class FormulaError(ValueError):
    pass


def _win(n) -> int:
    n = int(n)
    return min(WINDOWS, key=lambda w: abs(w - n))


def parse(text: str):
    """A formula string to a tree. Only known names, numbers and + - * / ( ) are allowed: nothing is executed."""
    toks = re.findall(r"\d+\.?\d*(?:e-?\d+)?|[a-z_][a-z0-9_]*|[()+\-*/,]", text.lower())
    if "".join(toks) != re.sub(r"\s+", "", text.lower()):
        raise FormulaError(f"unknown characters in {text[:60]!r}")
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take(want=None):
        nonlocal pos
        t = peek()
        if t is None or (want and t != want):
            raise FormulaError(f"expected {want or 'more'} in {text[:60]!r}")
        pos += 1
        return t

    def expr():
        node = term()
        while peek() in ("+", "-"):
            node = ("op", "add" if take() == "+" else "sub", [node, term()], None)
        return node

    def term():
        node = factor()
        while peek() in ("*", "/"):
            node = ("op", "mul" if take() == "*" else "div", [node, factor()], None)
        return node

    def factor():
        t = peek()
        if t == "-":
            take()
            return ("op", "neg", [factor()], None)
        if t == "(":
            take("(")
            node = expr()
            take(")")
            return node
        if t and re.match(r"\d", t):
            return ("num", float(take()))
        name = take()
        take("(")
        if name in FEATURES:
            if FEATURES[name][0]:
                n = take()
                take(")")
                return ("feat", name, _win(float(n)))
            take(")")
            return ("feat", name, 0)
        if name in UNARY:
            a = expr()
            take(")")
            return ("op", name, [a], None)
        if name in SERIES:
            a = expr()
            take(",")
            k = max(1, min(30, int(float(take()))))
            take(")")
            return ("op", name, [a], k)
        if name in BINARY:
            a = expr()
            take(",")
            b = expr()
            take(")")
            return ("op", name, [a, b], None)
        if name == "if":
            c = expr()
            take(",")
            a = expr()
            take(",")
            b = expr()
            take(")")
            return ("op", "if", [c, a, b], None)
        raise FormulaError(f"unknown name {name!r}")

    node = expr()
    if pos != len(toks):
        raise FormulaError(f"unexpected {toks[pos]!r} in {text[:60]!r}")
    if size(node) > 30 or depth(node) > 7:
        raise FormulaError("formula too big")
    return node


def show(node) -> str:
    kind = node[0]
    if kind == "num":
        return f"{node[1]:g}"
    if kind == "feat":
        return f"{node[1]}({node[2]})" if node[2] else f"{node[1]}()"
    _, name, args, k = node
    sym = {"add": "+", "sub": "-", "mul": "*", "div": "/"}
    if name in sym:
        return f"({show(args[0])} {sym[name]} {show(args[1])})"
    if name == "neg":
        return f"-{show(args[0])}"
    inner = ", ".join(show(a) for a in args)
    return f"{name}({inner}, {k})" if k else f"{name}({inner})"


def size(node) -> int:
    return 1 + (sum(size(a) for a in node[2]) if node[0] == "op" else 0)


def depth(node) -> int:
    return 1 + (max(depth(a) for a in node[2]) if node[0] == "op" else 0)


# ------------------------------------------------------------------ features, computed once per candle set
def _rolling(xs: list, n: int, fn):
    out = [None] * len(xs)
    for i in range(n - 1, len(xs)):
        w = xs[i - n + 1:i + 1]
        if None not in w:
            out[i] = fn(w)
    return out


def _mean(w):
    return sum(w) / len(w)


def _std(w):
    m = _mean(w)
    return math.sqrt(sum((x - m) ** 2 for x in w) / max(1, len(w) - 1))


def _rolling_mean(xs, n):
    """O(days) rolling mean; None when any value in the window is missing."""
    out, s, bad = [None] * len(xs), 0.0, 0
    for i, x in enumerate(xs):
        if x is None:
            bad += 1
        else:
            s += x
        if i >= n:
            y = xs[i - n]
            if y is None:
                bad -= 1
            else:
                s -= y
        if i >= n - 1 and bad == 0:
            out[i] = s / n
    return out


def _rolling_std(xs, n):
    m1 = _rolling_mean(xs, n)
    m2 = _rolling_mean([None if x is None else x * x for x in xs], n)
    return [None if a is None else math.sqrt(max(0.0, b - a * a) * n / max(1, n - 1)) for a, b in zip(m1, m2)]


def _lr(c):
    return [None] + [math.log(b / a) if a and b else None for a, b in zip(c[:-1], c[1:])]


def _echo(c: list, n: int, band: float = 0.03, min_visits: int = 5) -> list:
    """Ralph's price echo. For day i: every earlier day j at about today's price (+-3%), at least 2n days back so
    its next n days are fully known, and the average move over those n days. A Fenwick tree over price ranks keeps
    it fast: each day adds one past visit and asks for the sum and count in a price band."""
    vals = sorted({x for x in c if x})
    m = len(vals)
    cnt, tot = [0] * (m + 1), [0.0] * (m + 1)

    def add(k, v):
        k += 1
        while k <= m:
            cnt[k] += 1
            tot[k] += v
            k += k & -k

    def pref(k):  # sums over ranks [0, k)
        a, b = 0, 0.0
        while k > 0:
            a += cnt[k]
            b += tot[k]
            k -= k & -k
        return a, b

    out = [None] * len(c)
    for i, p in enumerate(c):
        j = i - 2 * n
        if j >= 0 and c[j] and c[j + n]:
            add(bisect.bisect_left(vals, c[j]), c[j + n] / c[j] - 1)
        if not p:
            continue
        lo, hi = bisect.bisect_left(vals, p * (1 - band)), bisect.bisect_right(vals, p * (1 + band))
        a1, b1 = pref(hi)
        a0, b0 = pref(lo)
        if a1 - a0 >= min_visits:
            out[i] = (b1 - b0) / (a1 - a0)
    return out


FEATURE_CACHE = 24  # columns kept in memory (each about 2 MB on all coins and days): the server has 512 MB


def feature(cd: research.Candles, name: str, n: int) -> dict[str, list]:
    cache = cd.__dict__.setdefault("_tt", {})
    key = (name, n)
    if key in cache:
        cache[key] = cache.pop(key)  # most recently used goes last
        return cache[key]
    while len(cache) >= FEATURE_CACHE:
        cache.pop(next(iter(cache)))
    coins, D = cd.coins, len(cd.days)
    out: dict[str, list] = {}
    if name in ("btcret", "breadth", "disp"):
        if name == "btcret":
            col = feature(cd, "ret", n).get("BTC", [None] * D)
        else:
            rets = feature(cd, "ret", n)
            col = []
            for i in range(D):
                xs = [rets[s][i] for s in coins if rets[s][i] is not None]
                if len(xs) < 4:
                    col.append(None)
                elif name == "breadth":
                    col.append(sum(x > 0 for x in xs) / len(xs))
                else:
                    col.append(_std(xs))
        out = {s: col for s in coins}
    elif name in ("mood", "wild"):
        from . import regime
        col = [None if r is None else r[name] for r in regime.classify(cd)]
        out = {s: col for s in coins}
    elif name in ("dow", "moon", "cycle"):
        col = []
        for d in cd.days:
            if name == "dow":
                col.append(float(time.gmtime(d).tm_wday))
            elif name == "moon":
                col.append(-math.cos(2 * math.pi * ((d - NEW_MOON) % LUNAR) / LUNAR))
            else:
                last = max((h for h in HALVINGS if h <= d), default=None)
                col.append(None if last is None else min(1.0, (d - last) / (1460 * 86400)))
        out = {s: col for s in coins}
    else:
        for s in coins:
            c, o, h, l = cd.c[s], cd.o[s], cd.h[s], cd.l[s]
            if name == "ret":
                v = [c[i] / c[i - n] - 1 if i >= n and c[i] and c[i - n] else None for i in range(D)]
            elif name == "vol":
                v = _rolling_std(_lr(c), n)
            elif name == "z":
                m, sd = _rolling_mean(c, n), _rolling_std(c, n)
                v = [(x - a) / b if x and a and b else None for x, a, b in zip(c, m, sd)]
            elif name in ("dhigh", "dlow"):
                src = h if name == "dhigh" else l
                ext = _rolling(src, n, max if name == "dhigh" else min)
                v = [x / e - 1 if x and e else None for x, e in zip(c, ext)]
            elif name == "range":
                v = _rolling_mean([(a - b) / x if a and b and x else None for a, b, x in zip(h, l, c)], n)
            elif name == "body":
                v = _rolling_mean([(x - y) / y if x and y else None for x, y in zip(c, o)], n)
            elif name == "wick":
                v = _rolling_mean([((a - max(y, x)) - (min(y, x) - b)) / x if a and b and x and y else None
                                   for a, b, x, y in zip(h, l, c, o)], n)
            elif name == "vratio":
                r1 = _lr(c)
                r5 = [math.log(c[i] / c[i - 5]) if i >= 5 and c[i] and c[i - 5] else None for i in range(D)]
                s1, s5 = _rolling_std(r1, max(n, 10)), _rolling_std(r5, max(n, 10))
                v = [b * b / (5 * a * a) if a and b else None for a, b in zip(s1, s5)]
            elif name == "autoc":
                r = _lr(c)
                v = _rolling([(a, b) if a is not None and b is not None else None
                              for a, b in zip([None] + r[:-1], r)], max(n, 10), _corr_pairs)
            elif name == "skew":
                v = _rolling(_lr(c), max(n, 10), _skew)
            elif name == "entropy":
                r = _lr(c)
                pairs = [None if a is None or b is None else (a > 0) * 2 + (b > 0) for a, b in zip([None] + r[:-1], r)]
                v = _rolling(pairs, max(n, 10), _entropy)
            elif name == "rel":
                mine, btc = feature(cd, "ret", n)[s], feature(cd, "ret", n).get("BTC", [None] * D)
                v = [a - b if a is not None and b is not None else None for a, b in zip(mine, btc)]
            elif name == "echo":
                v = _echo(c, n)
            elif name == "gap":
                v = [o[i] / c[i - 1] - 1 if i and o[i] and c[i - 1] else None for i in range(D)]
            elif name == "streak":
                v, run = [], 0
                for i in range(D):
                    if i and c[i] and c[i - 1]:
                        up = c[i] > c[i - 1]
                        run = (run + 1 if run > 0 else 1) if up else (run - 1 if run < 0 else -1)
                        v.append(float(run))
                    else:
                        run = 0
                        v.append(None)
            else:
                raise FormulaError(f"unknown feature {name}")
            out[s] = v
    cache[key] = out
    return out


def _corr_pairs(w):
    xs, ys = [a for a, _ in w], [b for _, b in w]
    mx, my = _mean(xs), _mean(ys)
    sxy = sum((a - mx) * (b - my) for a, b in w)
    sx, sy = math.sqrt(sum((a - mx) ** 2 for a in xs)), math.sqrt(sum((b - my) ** 2 for b in ys))
    return sxy / (sx * sy) if sx and sy else 0.0


def _skew(w):
    m, sd = _mean(w), _std(w)
    return sum((x - m) ** 3 for x in w) / len(w) / sd ** 3 if sd else 0.0


def _entropy(w):
    counts = [w.count(k) for k in range(4)]
    return -sum(c / len(w) * math.log(c / len(w), 4) for c in counts if c)


# ------------------------------------------------------------------ evaluating a formula on all coins and days
def _ew(f, *cols):
    return [None if any(x is None for x in xs) else f(*xs) for xs in zip(*cols)]


def _safe_div(a, b):
    return a / b if abs(b) > 1e-12 else None


def evaluate(cd: research.Candles, node) -> dict[str, list]:
    cache = cd.__dict__.setdefault("_tt_expr", {})
    key = show(node)
    if key in cache:
        return cache[key]
    coins, D = cd.coins, len(cd.days)
    kind = node[0]
    if kind == "num":
        out = {s: [node[1]] * D for s in coins}
    elif kind == "feat":
        out = feature(cd, node[1], node[2])
    else:
        _, name, args, k = node
        vals = [evaluate(cd, a) for a in args]
        out = {}
        if name in ("rank", "demean"):
            col = vals[0]
            out = {s: [None] * D for s in coins}
            for i in range(D):
                xs = [(col[s][i], s) for s in coins if col[s][i] is not None]
                if len(xs) < 4:
                    continue
                if name == "rank":
                    xs.sort()
                    for r, (_, s) in enumerate(xs):
                        out[s][i] = r / (len(xs) - 1)
                else:
                    m = sum(x for x, _ in xs) / len(xs)
                    for x, s in xs:
                        out[s][i] = x - m
        else:
            for s in coins:
                a = vals[0][s]
                if name == "add":
                    v = _ew(lambda x, y: x + y, a, vals[1][s])
                elif name == "sub":
                    v = _ew(lambda x, y: x - y, a, vals[1][s])
                elif name == "mul":
                    v = _ew(lambda x, y: x * y, a, vals[1][s])
                elif name == "div":
                    v = _ew(_safe_div, a, vals[1][s])
                elif name == "max":
                    v = _ew(max, a, vals[1][s])
                elif name == "min":
                    v = _ew(min, a, vals[1][s])
                elif name == "gt":
                    v = _ew(lambda x, y: float(x > y), a, vals[1][s])
                elif name == "lt":
                    v = _ew(lambda x, y: float(x < y), a, vals[1][s])
                elif name == "if":
                    v = _ew(lambda c, x, y: x if c > 0 else y, a, vals[1][s], vals[2][s])
                elif name == "neg":
                    v = _ew(lambda x: -x, a)
                elif name == "abs":
                    v = _ew(abs, a)
                elif name == "sign":
                    v = _ew(lambda x: float((x > 0) - (x < 0)), a)
                elif name == "log1p":
                    v = _ew(lambda x: math.log1p(x) if x > -1 else None, a)
                elif name == "sq":
                    v = _ew(lambda x: x * x, a)
                elif name == "lag":
                    v = [None] * min(k, D) + a[:D - k]
                elif name == "smooth":
                    v = _rolling_mean(a, k)
                elif name == "delta":
                    v = [None if i < k or a[i] is None or a[i - k] is None else a[i] - a[i - k] for i in range(D)]
                else:
                    raise FormulaError(f"unknown operation {name}")
                out[s] = [x if x is None or math.isfinite(x) else None for x in v]
    cache[key] = out
    return out


class FormulaStrategy(research.Strategy):
    """Holds the coins with the highest score, re-checked every `hold` days."""
    warmup = 230

    def __init__(self, idea: dict):
        self.idea = idea
        self.node = parse(idea["score"])
        self.gate = parse(idea["gate"]) if idea.get("gate") else None
        self.top, self.hold = int(idea.get("top", 3)), int(idea.get("hold", 1))
        self.min_score, self.btc_filter = idea.get("min_score"), bool(idea.get("btc_filter"))
        self.name = "Think tank: " + idea["name"]
        self.group = "Think tank"
        self.explain = (idea.get("theory") or "") + f" Score: {show(self.node)}. Holds the top {self.top}, checked every " \
                       f"{self.hold} day{'s' if self.hold > 1 else ''}" + (", only while Bitcoin is above its 50-day "
                                                                          "average" if self.btc_filter else "") + "."
        self._cd = None

    def _prep(self, cd):
        if self._cd is not cd:
            cd.__dict__["_tt_expr"] = {}  # intermediate results live only while one idea is evaluated
            self._cd, self._score = cd, evaluate(cd, self.node)
            self._gate = evaluate(cd, self.gate) if self.gate else None
            cd.__dict__["_tt_expr"] = {}

    def target(self, cd, i, held):
        self._prep(cd)
        if i % self.hold and held:
            return None
        if self.btc_filter and not research.btc_uptrend(cd, i, 50):
            return {} if held else None
        cands = []
        for s in cd.coins:
            x = self._score[s][i]
            if x is None or cd.c[s][i] is None:
                continue
            if self.min_score is not None and x <= self.min_score:
                continue
            if self._gate is not None and not ((self._gate[s][i] or 0) > 0):
                continue
            cands.append((x, s))
        keep = {s: 1 / self.top for _, s in sorted(cands, reverse=True)[:self.top]}
        return keep if set(keep) != set(held) else None


# ------------------------------------------------------------------ judging an idea
def _ic(cd, score, a: int, b: int, ahead: int = 7) -> tuple[float | None, float]:
    """Does a higher score really mean a better next week? Every 7th day (no overlap). For a score that differs
    between coins: rank correlation across coins, averaged, with its t-value. For a market-wide score (the same
    for every coin, like the moon or Bitcoin's trend): rank correlation over time with the next week of the
    average coin. Returns (correlation, t-value); t of 2 or more is unlikely to be chance."""
    cross, ts = [], []
    for i in range(a, min(b, len(cd.days) - ahead), ahead):
        pts = [(score[s][i], cd.c[s][i + ahead] / cd.c[s][i] - 1) for s in cd.coins
               if score[s][i] is not None and cd.c[s][i] and cd.c[s][i + ahead]]
        if len(pts) < 6:
            continue
        xs = [p[0] for p in pts]
        if max(xs) - min(xs) < 1e-12:
            ts.append((xs[0], sum(p[1] for p in pts) / len(pts)))
        else:
            cross.append(_corr_pairs(list(zip(_ranks(xs), _ranks([p[1] for p in pts])))))
    if len(ts) > len(cross):
        if len(ts) < 10:
            return None, 0.0
        r = _corr_pairs(list(zip(_ranks([x for x, _ in ts]), _ranks([y for _, y in ts]))))
        return round(r, 4), round(r * math.sqrt(len(ts) - 2) / math.sqrt(max(1e-9, 1 - r * r)), 2)
    if len(cross) < 10:
        return None, 0.0
    m = sum(cross) / len(cross)
    sd = _std(cross) or 1e-9
    return round(m, 4), round(m / (sd / math.sqrt(len(cross))), 2)


def _ranks(xs):
    """Ranks with ties sharing their average rank, so a constant score carries no information."""
    order = sorted(range(len(xs)), key=lambda k: xs[k])
    r = [0.0] * len(xs)
    pos = 0
    while pos < len(order):
        end = pos
        while end + 1 < len(order) and xs[order[end + 1]] == xs[order[pos]]:
            end += 1
        for k in order[pos:end + 1]:
            r[k] = (pos + end) / 2
        pos = end + 1
    return r


def _sr(eq):
    rets = research._daily_rets(eq)
    if len(rets) < 30:
        return 0.0
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1)) or 1e-12
    return m / sd


class Judge:
    """Holds the yardsticks (Bitcoin and the live strategy, split the same way) for one candle set."""

    def __init__(self, cd: research.Candles, brain: str | None = None):
        self.cd = cd
        self.start = min(len(cd.days) - 60, FormulaStrategy.warmup)
        self.cut = self.start + int((len(cd.days) - 1 - self.start) * SPLIT)
        self.k = self.cut - self.start
        btc = research.simulate(cd, research.HoldBTC(), self.start)
        self.btc = {"disc": btc.stats(0, self.k + 1), "hold": btc.stats(self.k)}
        self.brain = None
        strat = research.by_name(brain) if brain else None
        if strat:
            r = research.simulate(cd, strat, self.start)
            self.brain = {"name": strat.name, "disc": r.stats(0, self.k + 1), "hold": r.stats(self.k)}
        day = lambda i: time.strftime("%Y-%m-%d", time.gmtime(cd.days[i]))  # noqa: E731
        self.periods = {"from": day(self.start), "cut": day(self.cut), "to": day(len(cd.days) - 1)}

    def judge(self, idea: dict, trials: list[float], deep: bool = True) -> dict:
        cd = self.cd
        st = FormulaStrategy(idea)
        res = research.simulate(cd, st, self.start)
        disc, hold = res.stats(0, self.k + 1), res.stats(self.k)
        ic_d, t_d = _ic(cd, st._score, self.start, self.cut)
        ic_h, t_h = _ic(cd, st._score, self.cut, len(cd.days))
        rs = res.round_stats()
        out = {"disc": disc, "hold": hold, "ic_disc": ic_d, "ic_hold": ic_h, "t_disc": t_d, "t_hold": t_h, "fees_pct": res.fees,
               "invested_pct": res.invested, "per_week": rs["per_week"], "win_pct": rs["win_pct"],
               "avg_pct": rs["avg_pct"], "sr_bar": _sr(res.equity[:self.k + 1])}
        beats = lambda part: (out[part].get("sharpe", -9) > self.btc[part].get("sharpe", 0))  # noqa: E731
        # what evolution and Claude may see: discovery only
        out["fitness"] = round(disc.get("sharpe", -9) - self.btc["disc"].get("sharpe", 0) + 4 * (ic_d or 0), 3)
        if not (beats("disc") and (ic_d or 0) > 0 and disc.get("return_pct", -100) > 0):
            out["verdict"] = "dead"
            out["why"] = "fails already on the discovery years"
            return out
        if not (beats("hold") and (ic_h or 0) > 0 and hold.get("return_pct", -100) > 0):
            out["verdict"] = "holdout fail"
            out["why"] = "looked good on the discovery years, but not on the unseen recent years"
            return out
        out["skill_prob"] = research.deflated_sharpe(research._daily_rets(res.equity), trials) if len(trials) > 2 else 0.0
        stress = research.simulate(cd, FormulaStrategy(idea), self.start, cost=2 * (research.FEE + research.SPREAD))
        out["hold_2x"] = stress.stats(self.k)
        if not deep:
            out["verdict"] = "promising"
            return out
        rng = random.Random(len(cd.coins) * 31 + len(idea["score"]))
        luck = []
        for _ in range(8):
            keep = sorted(set(["BTC"] + rng.sample(cd.coins, max(6, len(cd.coins) * 2 // 3))))
            sub = cd.subset(keep)
            r = research.simulate(sub, FormulaStrategy(idea), self.start)
            luck.append(r.stats(self.k).get("cagr_pct") or -100.0)
        out["luck_median"], out["luck_worst"] = round(median(luck), 1), round(min(luck), 1)
        ok = (out["skill_prob"] >= 0.9 and out["hold_2x"].get("sharpe", -9) > self.btc["hold"].get("sharpe", 0)
              and out["luck_worst"] > 0 and t_d >= 2 and t_h >= 1.5)
        out["verdict"] = "candidate" if ok else "promising"
        if not ok:
            out["why"] = ("survives the unseen years, but " + ", ".join(
                x for x, bad in (("could be luck among all ideas tried", out["skill_prob"] < 0.9),
                                 ("not at twice the fees", out["hold_2x"].get("sharpe", -9) <= self.btc["hold"].get("sharpe", 0)),
                                 ("depends on a few lucky coins", out["luck_worst"] <= 0),
                                 ("the score itself doesn't reliably sort good from bad weeks", t_d < 2 or t_h < 1.5)) if bad))
        else:
            out["why"] = "passes every check here: add it to the history lab for the full robustness bar"
        if self.brain:
            out["beats_brain"] = hold.get("sharpe", -9) > self.brain["hold"].get("sharpe", 0)
        return out


# ------------------------------------------------------------------ where ideas come from
SEEDS = [
    {"name": "Ralph's echo, 10 days", "origin": "Ralph", "score": "echo(10)", "top": 3, "hold": 3, "min_score": 0,
     "theory": "Ralph's idea: draw a line at today's price back through history, find every time the coin was at "
               "this price before, and average what happened over the next 10 days. Buy the coins whose past echo "
               "says up."},
    {"name": "Ralph's echo, 20 days", "origin": "Ralph", "score": "echo(20)", "top": 3, "hold": 7, "min_score": 0,
     "theory": "The same price echo, looking 20 days ahead, re-checked weekly."},
    {"name": "Ralph's echo vs the market", "origin": "Ralph", "score": "demean(echo(10)) + rank(ret(20)) / 10",
     "top": 3, "hold": 3, "btc_filter": True,
     "theory": "The echo compared across coins (which coin's past is most bullish right now), with a small tilt "
               "towards coins already moving, only while Bitcoin is in an uptrend."},
    {"name": "Moon phase (control)", "origin": "control", "score": "moon()", "top": 3, "hold": 1, "min_score": 0.5,
     "theory": "Buys around full moon. There is no mechanism: this must fail, and if it ever passes, the test is "
               "too soft."},
    {"name": "Entropy collapse", "origin": "starter", "score": "rank(-entropy(20)) + rank(ret(10))", "top": 3, "hold": 3,
     "theory": "Information theory: when a coin's up/down pattern stops looking like coin flips (low entropy) while "
               "it rises, someone is buying with a plan."},
    {"name": "Halving tide", "origin": "starter", "score": "rel(50) * (1 - cycle())", "top": 3, "hold": 7,
     "theory": "Coins that lead Bitcoin early in the 4-year halving cycle, fading as the cycle ages."},
]

AI_SCHEMA = {
    "type": "object",
    "properties": {"ideas": {"type": "array", "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "inspiration": {"type": "string"}, "theory": {"type": "string"},
                       "score": {"type": "string"}, "gate": {"type": "string"}, "top": {"type": "integer"},
                       "hold": {"type": "integer"}, "btc_filter": {"type": "boolean"}},
        "required": ["name", "inspiration", "theory", "score", "top", "hold", "btc_filter"],
        "additionalProperties": False}}},
    "required": ["ideas"], "additionalProperties": False,
}


def grammar_text() -> str:
    feats = "\n".join(f"- {k}({'n' if v[0] else ''}): {v[1]}" for k, v in FEATURES.items())
    ops = "\n".join(f"- {k}(x): {v}" for k, v in UNARY.items()) + "\n" + \
          "\n".join(f"- {k}(x, k): {v}" for k, v in SERIES.items()) + "\n" + \
          "\n".join(f"- {k}(a, b): {v}" for k, v in BINARY.items()) + "\n- if(c, a, b): a where c > 0, else b"
    return (f"Windows n are rounded to one of {list(WINDOWS)} days. Numbers and + - * / ( ) are allowed.\n"
            f"Features (per coin, per day, known at that day's close):\n{feats}\nOperations:\n{ops}")


def ai_prompt(board: list[dict], dead: list[str], tested: int) -> str:
    best = "\n".join(f"- {x['name']}: {x['score']} (top {x['top']}, every {x['hold']}d) -> discovery sharpe "
                     f"{x['res']['disc'].get('sharpe')}, signal {x['res'].get('ic_disc')}" for x in board[:8]) or "- none yet"
    return (f"You run a think tank inventing ORIGINAL crypto strategy ideas. {tested} ideas have been tested so far.\n"
            "Rules: no standard technical analysis (no RSI, MACD, moving-average crossovers, Bollinger bands, plain "
            "momentum or breakouts; those are already tested). Borrow from physics, biology, ecology, information "
            "theory, network science, game theory, thermodynamics, music, epidemiology or anything else, and turn "
            "the concept into a formula. Each idea scores every coin every day; the strategy holds the `top` coins "
            "with the highest score (1-5), re-checked every `hold` days (1, 3 or 7), optionally only while Bitcoin "
            "is in an uptrend (btc_filter), optionally only coins where `gate` > 0. Fees are 0.4% per trade, so "
            "ideas that churn daily need a strong edge.\n\n" + grammar_text() +
            "\n\nBest ideas so far (discovery years only; the recent years are kept secret to judge you fairly):\n"
            + best + "\nThemes that died: " + (", ".join(dead[:15]) or "none yet") +
            "\n\nInvent 6 new ideas, each clearly different. 'inspiration' names the field and concept, 'theory' "
            "explains in two plain sentences why it could work in crypto. Use only the formula language above.")


def random_formula(rng: random.Random, d: int = 0):
    if d >= 2 or (d and rng.random() < 0.35):
        name = rng.choice(list(FEATURES))
        return ("feat", name, rng.choice(WINDOWS) if FEATURES[name][0] else 0)
    r = rng.random()
    if r < 0.35:
        return ("op", rng.choice(["add", "sub", "mul"]), [random_formula(rng, d + 1), random_formula(rng, d + 1)], None)
    if r < 0.6:
        return ("op", rng.choice(["rank", "demean", "neg", "abs", "sign"]), [random_formula(rng, d + 1)], None)
    if r < 0.75:
        return ("op", rng.choice(list(SERIES)), [random_formula(rng, d + 1)], rng.choice([3, 5, 10]))
    if r < 0.85:
        return ("op", "if", [random_formula(rng, d + 1), random_formula(rng, d + 1), random_formula(rng, d + 1)], None)
    return random_formula(rng, 2)


def _subtrees(node, path=()):
    yield path, node
    if node[0] == "op":
        for k, a in enumerate(node[2]):
            yield from _subtrees(a, path + (k,))


def _replace(node, path, new):
    if not path:
        return new
    args = list(node[2])
    args[path[0]] = _replace(args[path[0]], path[1:], new)
    return ("op", node[1], args, node[3])


def mutate(rng: random.Random, idea: dict, other: dict | None, n: int) -> dict:
    tree = parse(idea["score"])
    subs = list(_subtrees(tree))
    path, node = rng.choice(subs)
    how = rng.random()
    if other and how < 0.3:
        _, donor = rng.choice(list(_subtrees(parse(other["score"]))))
        tree, what = _replace(tree, path, donor), f"crossed with {other['name']}"
    elif how < 0.5 and node[0] == "feat" and node[2]:
        tree, what = _replace(tree, path, ("feat", node[1], rng.choice(WINDOWS))), "new time window"
    elif how < 0.65:
        tree, what = _replace(tree, path, random_formula(rng, 1)), "new part"
    elif how < 0.8:
        op = rng.choice(["rank", "demean", "neg", "smooth", "lag"])
        tree = _replace(tree, path, ("op", op, [node], 3 if op in SERIES else None))
        what = f"wrapped in {op}"
    else:
        tree, what = tree, "new trading rhythm"
    child = {**{k: idea[k] for k in ("top", "hold", "btc_filter", "min_score", "gate") if k in idea},
             "score": show(tree), "origin": "evolution", "parent": idea["name"]}
    if what == "new trading rhythm" or rng.random() < 0.2:
        child.update(top=rng.choice([1, 2, 3, 4, 5]), hold=rng.choice([1, 3, 7]), btc_filter=rng.random() < 0.5)
    if size(parse(child["score"])) > 30 or depth(parse(child["score"])) > 7:
        child["score"] = idea["score"]
    root = re.sub(r"^(Evo \d+: )+", "", idea["name"])
    child["name"] = f"Evo {n}: {root[:40]}"
    child["theory"] = f"Evolved from \"{idea['name']}\" ({what})."
    return child


def clean(idea: dict) -> dict | None:
    """A valid idea, or None. Normalises the formula so the same idea is never tested twice."""
    try:
        score = show(parse(idea["score"]))
        gate = show(parse(idea["gate"])) if idea.get("gate") else None
    except (FormulaError, KeyError, ValueError, IndexError):
        return None
    out = {"name": str(idea.get("name") or "unnamed")[:70], "score": score, "gate": gate,
           "top": max(1, min(5, int(idea.get("top") or 3))), "hold": min((1, 3, 7), key=lambda h: abs(h - int(idea.get("hold") or 1))),
           "btc_filter": bool(idea.get("btc_filter")), "min_score": idea.get("min_score"),
           "origin": idea.get("origin") or "Claude", "theory": str(idea.get("theory") or "")[:400],
           "inspiration": str(idea.get("inspiration") or "")[:120]}
    if idea.get("parent"):
        out["parent"] = idea["parent"]
    return out


def key_of(idea: dict) -> str:
    raw = json.dumps([idea["score"], idea.get("gate"), idea["top"], idea["hold"], idea["btc_filter"], idea.get("min_score")])
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ------------------------------------------------------------------ the endless search, one batch at a time
def run_batch(cd: research.Candles, state: dict, queue: list[dict], brain: str | None, seconds: float = 40,
              seed: int | None = None) -> dict:
    """Tests ideas for about `seconds`: queued ones first (Claude's and the seeds), then evolution and random ones.
    Returns the updated state (pure data, stored by the engine)."""
    rng = random.Random(seed if seed is not None else time.time())
    judge = state.get("_judge")
    if not judge or judge.cd is not cd:
        judge = Judge(cd, brain)
    seen = set(state.get("seen", []))
    trials = state.get("trials", [])
    board = state.get("board", [])
    recent = state.get("recent", [])
    counts = state.get("counts", {"tested": 0, "dead": 0, "holdout fail": 0, "promising": 0, "candidate": 0})
    t0 = time.time()
    n = counts.get("tested", 0)
    while time.time() - t0 < seconds:
        if queue:
            idea = clean(queue.pop(0))
        elif board and rng.random() < 0.75:
            parents = sorted(board, key=lambda x: -x["res"]["fitness"])[:12]
            a = rng.choice(parents)
            b = rng.choice(parents) if rng.random() < 0.4 else None
            idea = clean(mutate(rng, a, b if b is not a else None, n + 1))
        else:
            idea = clean({"name": f"Random {n + 1}", "score": show(random_formula(rng)), "origin": "random",
                          "top": rng.choice([2, 3, 4]), "hold": rng.choice([1, 3, 7]), "btc_filter": rng.random() < 0.5,
                          "theory": "A random formula: the search's way of trying what nobody would think of."})
        if not idea or key_of(idea) in seen:
            continue
        seen.add(key_of(idea))
        try:
            res = judge.judge(idea, trials)
        except (FormulaError, ZeroDivisionError, OverflowError, ValueError):
            continue
        n += 1
        counts["tested"] = n
        counts[res["verdict"]] = counts.get(res["verdict"], 0) + 1
        trials.append(res.pop("sr_bar"))
        item = {**idea, "res": res, "ts": time.time(), "no": n}
        recent = ([{k: item[k] for k in ("no", "name", "origin", "score", "ts")}
                   | {"verdict": res["verdict"], "fitness": res["fitness"],
                      "disc": res["disc"].get("cagr_pct"), "hold": res["hold"].get("cagr_pct")}] + recent)[:25]
        sig = (res["disc"].get("cagr_pct"), res["hold"].get("cagr_pct"), res["fees_pct"])
        twin = any((x["res"]["disc"].get("cagr_pct"), x["res"]["hold"].get("cagr_pct"), x["res"]["fees_pct"]) == sig
                   for x in board)  # a variant that trades exactly the same way adds nothing new to the board
        if (res["verdict"] != "dead" and not twin) or item["origin"] in ("Ralph", "control", "Claude", "starter"):
            board.append(item)
        board = sorted(board, key=lambda x: (-("candidate" == x["res"]["verdict"]), -x["res"]["fitness"]))
        # keep the best by discovery fitness plus every named idea (Ralph's, Claude's, the control) for the record
        named = [x for x in board if x["origin"] in ("Ralph", "control")]
        board = board[:60] + [x for x in named if x not in board[:60]]
    return {**state, "_judge": judge, "seen": list(seen)[-20000:], "trials": trials[-5000:], "board": board,
            "recent": recent, "counts": counts, "periods": judge.periods, "btc": judge.btc,
            "brain": judge.brain, "ts": time.time()}


def public(state: dict) -> dict:
    """What the dashboard gets: no internal objects."""
    return {k: v for k, v in state.items() if not k.startswith("_") and k not in ("seen", "trials")}
