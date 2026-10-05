"""The 2-year test lab: daily candles from Kraken, long-only spot strategies, honest costs.

Every strategy decides on day t's close and trades at day t+1's open, so nothing peeks ahead.
Costs per side = Fusion's fee + an estimated spread. Results are compared with simply holding Bitcoin.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field

from .data.prices import KRAKEN, kraken_pair

# Liquid coins that Bitpanda Fusion trades: the universe the research recommends (no micro caps, no memes)
UNIVERSE = ["BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LINK", "AVAX", "DOT", "LTC", "BCH", "NEAR", "UNI",
            "AAVE", "ATOM", "XLM", "SUI", "FET", "TRX", "HBAR", "ARB", "ONDO"]
FEE = 0.0025      # Fusion, per side
SPREAD = 0.0015   # estimated half-spread on liquid coins, per side


@dataclass
class Candles:
    """Daily OHLC per coin on one shared calendar (days as UTC midnight timestamps)."""
    days: list[float]
    o: dict[str, list[float | None]] = field(default_factory=dict)
    h: dict[str, list[float | None]] = field(default_factory=dict)
    l: dict[str, list[float | None]] = field(default_factory=dict)
    c: dict[str, list[float | None]] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, rows: dict[str, list[tuple]]) -> "Candles":
        days = sorted({r[0] for rs in rows.values() for r in rs})
        idx = {d: i for i, d in enumerate(days)}
        out = cls(days)
        for sym, rs in rows.items():
            for name in ("o", "h", "l", "c"):
                getattr(out, name)[sym] = [None] * len(days)
            for t, o, h, l, c in rs:
                i = idx[t]
                out.o[sym][i], out.h[sym][i], out.l[sym][i], out.c[sym][i] = o, h, l, c
        return out

    @property
    def coins(self) -> list[str]:
        return list(self.c)

    def closes(self, sym: str, i: int, n: int) -> list[float] | None:
        """The last n closes up to and including day i, or None if any is missing."""
        if i - n + 1 < 0:
            return None
        xs = self.c[sym][i - n + 1:i + 1]
        return None if any(x is None for x in xs) else xs


async def fetch_daily(client, symbols: list[str], log=lambda *a: None) -> dict[str, list[tuple]]:
    """Up to 720 daily candles per coin from Kraken (about 2 years). The forming candle is dropped."""
    out = {}
    for sym in symbols:
        try:
            r = await client.get(f"{KRAKEN}/OHLC", params={"pair": kraken_pair(sym), "interval": 1440})
            res = r.json().get("result", {})
            rows = next((v for k, v in res.items() if k != "last"), [])
            rows = [(float(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in rows][:-1]
            if len(rows) >= 60:
                out[sym] = rows
        except Exception as ex:
            log("Lab", "warn", f"No daily history for {sym}: {ex}")
        await asyncio.sleep(1.1)  # Kraken public rate limit
    return out


def synthetic_rows(symbols: list[str], days: int = 720, seed: int = 7) -> dict[str, list[tuple]]:
    """Random but trending prices for --simulate and tests (never shown as real results)."""
    import random
    rng = random.Random(seed)
    t0 = (time.time() // 86400 - days) * 86400
    out = {}
    for sym in symbols:
        p, rows, drift = 100.0, [], rng.gauss(0, 0.002)
        for d in range(days):
            o = p
            p *= math.exp(rng.gauss(drift + 0.003 * math.sin(d / 60), 0.04))
            rows.append((t0 + d * 86400, o, max(o, p) * 1.01, min(o, p) * 0.99, p))
        out[sym] = rows
    return out


# ------------------------------------------------------------------ indicators (all on data up to day i)
def sma(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def daily_vol(xs: list[float]) -> float:
    rets = [math.log(b / a) for a, b in zip(xs[:-1], xs[1:]) if a > 0 and b > 0]
    if len(rets) < 2:
        return 0.0
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))


def atr(cd: Candles, sym: str, i: int, n: int = 14) -> float | None:
    if i - n < 0:
        return None
    trs = []
    for k in range(i - n + 1, i + 1):
        h, l, pc = cd.h[sym][k], cd.l[sym][k], cd.c[sym][k - 1]
        if None in (h, l, pc):
            return None
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / n


def btc_uptrend(cd: Candles, i: int, n: int) -> bool:
    xs = cd.closes("BTC", i, n)
    return bool(xs) and xs[-1] > sma(xs)


# ------------------------------------------------------------------ strategies: day i -> target weights
class Strategy:
    name = "strategy"
    group = ""
    explain = ""
    warmup = 100

    def target(self, cd: Candles, i: int, held: dict[str, float]) -> dict[str, float] | None:
        """Weights to hold from tomorrow's open (sum <= 1), or None to keep today's positions."""
        raise NotImplementedError


class HoldBTC(Strategy):
    name, group = "Hold Bitcoin", "Benchmark"
    explain = "Buys Bitcoin on day one and does nothing else."

    def target(self, cd, i, held):
        return None if held else {"BTC": 1.0}


class HoldBasket(Strategy):
    name, group = "Hold all coins", "Benchmark"
    explain = "Equal money in every coin, rebalanced monthly."

    def target(self, cd, i, held):
        if held and i % 30:
            return None
        live = [s for s in cd.coins if cd.c[s][i] is not None]
        return {s: 1 / len(live) for s in live}


class BtcRegime(Strategy):
    group = "Bitcoin filter"

    def __init__(self, n: int):
        self.n = n
        self.name = f"Bitcoin above its {n}-day average"
        self.explain = f"Holds Bitcoin while it closes above its {n}-day average, cash otherwise."

    def target(self, cd, i, held):
        return {"BTC": 1.0} if btc_uptrend(cd, i, self.n) else {}


class Rotation(Strategy):
    """Cross-sectional momentum: hold the N strongest coins, re-checked every few days."""
    group = "Rotation"

    def __init__(self, lookback: int, top: int, every: int = 7, regime: int | None = 50, inv_vol: bool = False):
        self.lookback, self.top, self.every, self.regime, self.inv_vol = lookback, top, every, regime, inv_vol
        f = f", BTC filter {regime}d" if regime else ", no filter"
        self.name = (f"Top {top} by {lookback}-day strength{f}" + (", sized by volatility" if inv_vol else "")
                     + (", checked daily" if every == 1 else ""))
        self.explain = (f"Every {every} days: the {top} coins with the best {lookback}-day return (positive only)"
                        + (f", only while Bitcoin is above its {regime}-day average" if regime else "") + ".")

    def target(self, cd, i, held):
        if self.regime and not btc_uptrend(cd, i, self.regime):
            return {}
        if held and i % self.every:
            return None
        ranked = []
        for s in cd.coins:
            xs = cd.closes(s, i, self.lookback + 1)
            if xs:
                ranked.append((xs[-1] / xs[0] - 1, s))
        picks = [s for r, s in sorted(ranked, reverse=True)[:self.top] if r > 0]
        if not picks:
            return {}
        if not self.inv_vol:
            return {s: 1 / self.top for s in picks}
        inv = {s: 1 / max(daily_vol(cd.closes(s, i, 31)), 0.005) for s in picks}
        tot = sum(inv.values())
        return {s: min(0.5, v / tot * len(picks) / self.top) for s, v in inv.items()}


class Donchian(Strategy):
    """Time-series trend per coin: buy a breakout to a new N-day high, sell on an M-day low or a 3xATR trailing stop."""
    group = "Trend following"

    def __init__(self, entry: int, exit_: int, slots: int = 3, regime: int | None = 50, atr_mult: float = 3.0):
        self.entry, self.exit, self.slots, self.regime, self.atr_mult = entry, exit_, slots, regime, atr_mult
        f = f", BTC filter {regime}d" if regime else ", no filter"
        self.name = f"Breakout {entry}/{exit_} days, {slots} slots{f}"
        self.explain = (f"Buys a coin at a new {entry}-day high, sells at an {exit_}-day low or {atr_mult:g}x its "
                        f"daily range below the peak. At most {slots} coins at once.")
        self.peak: dict[str, float] = {}

    def target(self, cd, i, held):
        if i < max(self.entry, self.exit) + 1:
            return None
        keep = {}
        for s in held:
            c = cd.c[s][i]
            lows = [x for x in cd.l[s][i - self.exit:i] if x is not None]
            a = atr(cd, s, i)
            self.peak[s] = max(self.peak.get(s, c), c)
            out = (lows and c < min(lows)) or (a and c < self.peak[s] - self.atr_mult * a)
            if not out:
                keep[s] = 1 / self.slots
            else:
                self.peak.pop(s, None)
        if self.regime and not btc_uptrend(cd, i, self.regime):
            keep = {}
            self.peak.clear()
        else:
            cands = []
            for s in cd.coins:
                if s in keep or cd.c[s][i] is None:
                    continue
                highs = [x for x in cd.h[s][i - self.entry:i] if x is not None]
                xs = cd.closes(s, i, 31)
                if highs and xs and cd.c[s][i] > max(highs):
                    cands.append((xs[-1] / xs[0], s))
            for _, s in sorted(cands, reverse=True)[:self.slots - len(keep)]:
                keep[s] = 1 / self.slots
                self.peak[s] = cd.c[s][i]
        return keep if set(keep) != set(held) else None


def all_strategies() -> list[Strategy]:
    out: list[Strategy] = [HoldBTC(), HoldBasket(), BtcRegime(50), BtcRegime(100)]
    for lb in (14, 30, 60):
        for top in (1, 2, 3, 5):
            out.append(Rotation(lb, top))
    out += [Rotation(30, 3, regime=None), Rotation(30, 3, inv_vol=True), Rotation(30, 3, every=1)]
    for e, x in ((20, 10), (55, 20), (20, 20)):
        out.append(Donchian(e, x))
    out += [Donchian(20, 10, regime=None), Donchian(20, 10, slots=4), Donchian(55, 20, slots=2)]
    return out


# ------------------------------------------------------------------ simulator
@dataclass
class Result:
    name: str
    group: str
    explain: str
    equity: list[float]          # account value per day (start = 1.0)
    trades: int = 0
    fees: float = 0.0            # paid as a share of the starting money
    invested: float = 0.0        # share of days with money in coins

    def stats(self, a: int = 0, b: int | None = None) -> dict:
        eq = self.equity[a:b]
        if len(eq) < 2 or eq[0] <= 0:
            return {}
        total = eq[-1] / eq[0] - 1
        years = (len(eq) - 1) / 365
        rets = [y / x - 1 for x, y in zip(eq[:-1], eq[1:]) if x > 0]
        m = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - m) ** 2 for r in rets) / max(1, len(rets) - 1))
        peak, dd = eq[0], 0.0
        for x in eq:
            peak = max(peak, x)
            dd = min(dd, x / peak - 1)
        return {"return_pct": round(total * 100, 1),
                "cagr_pct": round(((1 + total) ** (1 / years) - 1) * 100, 1) if years > 0 and total > -1 else None,
                "max_dd_pct": round(dd * 100, 1),
                "sharpe": round(m / sd * math.sqrt(365), 2) if sd > 0 else 0.0}


def simulate(cd: Candles, strat: Strategy, start: int, cost: float = FEE + SPREAD) -> Result:
    """Run one strategy from day `start`. Decisions on day i's close, fills at day i+1's open."""
    cash, qty = 1.0, {}
    res = Result(strat.name, strat.group, strat.explain, [1.0])
    days_in = 0
    for i in range(start, len(cd.days) - 1):
        px = {s: cd.c[s][i] for s in qty}
        value = cash + sum(q * px[s] for s, q in qty.items() if px[s])
        held = {s: q * px[s] / value for s, q in qty.items() if px[s]}
        want = strat.target(cd, i, held)
        if want is not None:
            nxt = {s: cd.o[s][i + 1] for s in set(want) | set(qty)}
            nxt = {s: p for s, p in nxt.items() if p}
            value = cash + sum(q * nxt.get(s, px.get(s) or 0) for s, q in qty.items())
            for s in list(qty):  # sells first, so the cash is there for the buys
                tgt = want.get(s, 0.0) * value
                cur = qty[s] * nxt.get(s, 0)
                if s in nxt and tgt < cur * 0.75:  # trade only real changes, not tiny drift
                    sell = cur - tgt
                    cash += sell * (1 - cost)
                    res.fees += sell * cost
                    qty[s] -= sell / nxt[s]
                    res.trades += 1
                    if qty[s] * nxt[s] < 1e-9:
                        del qty[s]
            for s, w in want.items():
                if s not in nxt:
                    continue
                cur = qty.get(s, 0.0) * nxt[s]
                buy = min(w * value - cur, cash)
                if buy > max(0.25 * w * value, 1e-9) or (cur == 0 and buy > 1e-9):
                    cash -= buy
                    res.fees += buy * cost
                    qty[s] = qty.get(s, 0.0) + buy * (1 - cost) / nxt[s]
                    res.trades += 1
        close = {s: cd.c[s][i + 1] for s in qty}
        value = cash + sum(q * (close[s] or 0) for s, q in qty.items())
        days_in += bool(qty)
        res.equity.append(value)
    n = max(1, len(res.equity) - 1)
    res.invested = round(days_in / n * 100)
    res.fees = round(res.fees * 100, 1)
    return res


def run_all(cd: Candles, strategies: list[Strategy] | None = None) -> dict:
    """Every strategy over the full history, each half (walk-forward check) and the last 6 months."""
    strategies = strategies or all_strategies()
    start = min(len(cd.days) - 30, max(s.warmup for s in strategies))
    n = len(cd.days) - 1 - start
    half = n // 2
    rows = []
    for s in strategies:
        r = simulate(cd, s, start)
        rows.append({"name": r.name, "group": r.group, "explain": r.explain, "trades": r.trades,
                     "fees_pct": r.fees, "invested_pct": r.invested, "full": r.stats(),
                     "first_half": r.stats(0, half + 1), "second_half": r.stats(half),
                     "last_6m": r.stats(max(0, n - 182)),
                     "curve": [round(x, 4) for x in r.equity[::3]]})
    btc = next(r for r in rows if r["name"] == HoldBTC.name)
    for r in rows:
        # beats Bitcoin on risk-adjusted terms in BOTH halves: the bar against luck
        r["beats_btc_both_halves"] = all(r[k].get("sharpe", 0) > btc[k].get("sharpe", 0)
                                         for k in ("first_half", "second_half"))
    day = lambda i: time.strftime("%Y-%m-%d", time.gmtime(cd.days[i]))
    return {"ts": time.time(), "from": day(start), "to": day(len(cd.days) - 1), "days": n,
            "coins": cd.coins, "cost_per_side_pct": round((FEE + SPREAD) * 100, 2),
            "rows": sorted(rows, key=lambda r: -r["full"].get("sharpe", -9))}
