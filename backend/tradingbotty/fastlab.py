"""The fast lab: can a quick, speculative trader beat the fees on Fusion?

Same honesty rules as the history test, on 4-hour candles of the most traded coins that Fusion also lists:
decide on a bar's close, trade at the next bar's open, pay fee + spread on every buy and sell. Wider, wilder coins
than the daily brain's 22, so the cost per side is set higher (0.5%). Results go through the same robustness checks
(both halves, most years, skill vs luck, twice the fees, walk-forward, coin luck) before any real money follows.
"""
from __future__ import annotations

import asyncio
import math
import random
import time

from . import patterns, research
from .research import BINANCE, Candles, Strategy, atr, btc_uptrend

BAR = 4 * 3600
BARS_PER_DAY = 6
HISTORY_DAYS = 3 * 365
COST = 0.0025 + 0.0025          # Fusion fee + a wider spread on smaller coins, per side
MAX_COINS = 40
STABLES = {"USDT", "USDC", "FDUSD", "TUSD", "DAI", "USDP", "EUR", "EURI", "BUSD", "USDE", "PYUSD", "AEUR", "XUSD",
           "PAXG", "WBTC", "WBETH", "BFUSD"}
TICKER = "https://data-api.binance.vision/api/v3/ticker/24hr"


# ------------------------------------------------------------------ strategies (bars are 4 hours)
def _hours(bars: int) -> str:
    return f"{bars // BARS_PER_DAY}d" if bars % BARS_PER_DAY == 0 else f"{bars * 4}h"


def vol_surge(cd: Candles, s: str, i: int, short: int = BARS_PER_DAY, long: int = 7 * BARS_PER_DAY) -> float | None:
    """Trading volume of the last day vs the week before it (1.0 = normal, 2.0 = twice the usual)."""
    vs = cd.v.get(s)
    if not vs or i - short - long + 1 < 0:
        return None
    a = [x for x in vs[i - short + 1:i + 1] if x is not None]
    b = [x for x in vs[i - short - long + 1:i - short + 1] if x is not None]
    return (sum(a) / len(a)) / (sum(b) / len(b)) if a and b and sum(b) > 0 else None


def _vol_ok(cd, s, i, need):
    if not need:
        return True
    x = vol_surge(cd, s, i)
    return x is not None and x >= need


class FastBreakout(Strategy):
    """Buys a breakout to a new N-bar high; takes the gain at +X%, or sells at an M-bar low, a trailing stop or after
    a few days without progress."""
    group = "Fast: breakout"
    warmup = 300

    def __init__(self, entry: int, exit_: int, slots: int = 2, tp: float | None = 0.15, max_hold: int | None = 42,
                 regime: int | None = 300, atr_mult: float = 3.0, label: str | None = None, group: str | None = None,
                 vol_x: float | None = None):
        self.entry, self.exit, self.slots, self.tp, self.max_hold = entry, exit_, slots, tp, max_hold
        self.regime, self.atr_mult, self.vol_x = regime, atr_mult, vol_x
        bits = [f"high of {_hours(entry)}", f"low of {_hours(exit_)}", f"{slots} slot{'s' if slots > 1 else ''}"]
        if tp:
            bits.append(f"take profit +{round(tp * 100)}%")
        if max_hold:
            bits.append(f"max {_hours(max_hold)}")
        if not regime:
            bits.append("no BTC filter")
        if vol_x:
            bits.append(f"volume {vol_x:g}x")
        self.name = label or "Fast breakout: " + ", ".join(bits)
        if group:
            self.group = group
        self.explain = (f"Every 4 hours: buys a coin that closes above its {_hours(entry)} high (strongest first, at "
                        f"most {slots} at once)"
                        + (f" on at least {vol_x:g}x its usual trading volume" if vol_x else "")
                        + (", only while Bitcoin is above its 50-day average" if regime else "")
                        + f". Sells under its {_hours(exit_)} low, {atr_mult:g}x its 4-hour range below the peak"
                        + (f", at +{round(tp * 100)}% profit" if tp else "")
                        + (f", or after {_hours(max_hold)} if neither happened" if max_hold else "") + ".")
        self.peak: dict[str, float] = {}
        self.entry_px: dict[str, float] = {}
        self.since: dict[str, int] = {}

    def _drop(self, s):
        self.peak.pop(s, None)
        self.entry_px.pop(s, None)
        self.since.pop(s, None)

    def target(self, cd, i, held):
        if i < max(self.entry, self.exit) + 1:
            return None
        keep = {}
        for s in held:
            c = cd.c[s][i]
            if c is None:
                keep[s] = 1 / self.slots
                continue
            lows = [x for x in cd.l[s][i - self.exit:i] if x is not None]
            a = atr(cd, s, i)
            self.peak[s] = max(self.peak.get(s, c), c)
            ep = self.entry_px.get(s, c)
            out = ((lows and c < min(lows)) or (a and c < self.peak[s] - self.atr_mult * a)
                   or (self.tp and c >= ep * (1 + self.tp))
                   or (self.max_hold and i - self.since.get(s, i) >= self.max_hold and c < ep * 1.02))
            if out:
                self._drop(s)
            else:
                keep[s] = 1 / self.slots
        if self.regime and not btc_uptrend(cd, i, self.regime):
            for s in list(keep):
                self._drop(s)
            keep = {}
        else:
            cands = []
            for s in cd.coins:
                if s in keep or cd.c[s][i] is None:
                    continue
                highs = [x for x in cd.h[s][i - self.entry:i] if x is not None]
                xs = cd.closes(s, i, self.entry + 1)
                if highs and xs and cd.c[s][i] > max(highs) and _vol_ok(cd, s, i, self.vol_x):
                    cands.append((xs[-1] / xs[0], s))
            for _, s in sorted(cands, reverse=True)[:self.slots - len(keep)]:
                keep[s] = 1 / self.slots
                self.peak[s] = self.entry_px[s] = cd.c[s][i]
                self.since[s] = i
        return keep if set(keep) != set(held) else None


class PumpRider(Strategy):
    """Jumps on the coins that moved most in the last day, rides them with a tight trailing stop, takes the gain."""
    group = "Fast: momentum"
    warmup = 300

    def __init__(self, look: int = 6, min_move: float = 0.08, slots: int = 2, tp: float = 0.12, stop: float = 0.06,
                 max_hold: int = 12, regime: int | None = 300, vol_x: float | None = None):
        self.look, self.min_move, self.slots, self.tp, self.stop, self.max_hold = look, min_move, slots, tp, stop, max_hold
        self.regime, self.vol_x = regime, vol_x
        self.name = (f"Pump rider: up {round(min_move * 100)}%+ in {_hours(look)}, {slots} slot{'s' if slots > 1 else ''}, "
                     f"take +{round(tp * 100)}% / stop -{round(stop * 100)}%, max {_hours(max_hold)}"
                     + (f", volume {vol_x:g}x" if vol_x else ""))
        self.explain = (f"Every 4 hours: buys the coins that rose at least {round(min_move * 100)}% over the last "
                        f"{_hours(look)}" + (f" on at least {vol_x:g}x their usual volume" if vol_x else "")
                        + f" (biggest first), betting the move continues. Sells at +{round(tp * 100)}%, "
                        f"{round(stop * 100)}% below the peak, or after {_hours(max_hold)}.")
        self.peak: dict[str, float] = {}
        self.entry_px: dict[str, float] = {}
        self.since: dict[str, int] = {}

    def target(self, cd, i, held):
        keep = {}
        for s in held:
            c = cd.c[s][i]
            if c is None:
                keep[s] = 1 / self.slots
                continue
            self.peak[s] = max(self.peak.get(s, c), c)
            ep = self.entry_px.get(s, c)
            if c >= ep * (1 + self.tp) or c <= self.peak[s] * (1 - self.stop) or i - self.since.get(s, i) >= self.max_hold:
                for d in (self.peak, self.entry_px, self.since):
                    d.pop(s, None)
            else:
                keep[s] = 1 / self.slots
        if not self.regime or btc_uptrend(cd, i, self.regime):
            cands = []
            for s in cd.coins:
                if s in keep:
                    continue
                xs = cd.closes(s, i, self.look + 1)
                if xs and xs[-1] / xs[0] - 1 >= self.min_move and _vol_ok(cd, s, i, self.vol_x):
                    cands.append((xs[-1] / xs[0], s))
            for _, s in sorted(cands, reverse=True)[:self.slots - len(keep)]:
                keep[s] = 1 / self.slots
                self.peak[s] = self.entry_px[s] = cd.c[s][i]
                self.since[s] = i
        return keep if set(keep) != set(held) else None


class DipBuyer(Strategy):
    """Buys a sharp one-day drop in a coin that is still in an uptrend, sells the bounce."""
    group = "Fast: dip"
    warmup = 300

    def __init__(self, drop: float = 0.10, slots: int = 2, tp: float = 0.06, stop: float = 0.08, max_hold: int = 18,
                 trend: int = 120, regime: int | None = 300):
        self.drop, self.slots, self.tp, self.stop, self.max_hold, self.trend = drop, slots, tp, stop, max_hold, trend
        self.regime = regime
        self.name = (f"Dip buyer: down {round(drop * 100)}%+ in 1d inside an uptrend, {slots} slot{'s' if slots > 1 else ''}, "
                     f"take +{round(tp * 100)}% / stop -{round(stop * 100)}%")
        self.explain = (f"Every 4 hours: buys a coin that fell at least {round(drop * 100)}% in a day while it is "
                        f"still above its {_hours(trend)} average, betting on a bounce. Sells at +{round(tp * 100)}%, "
                        f"-{round(stop * 100)}%, or after {_hours(max_hold)}.")
        self.entry_px: dict[str, float] = {}
        self.since: dict[str, int] = {}

    def target(self, cd, i, held):
        keep = {}
        for s in held:
            c = cd.c[s][i]
            ep = self.entry_px.get(s, c)
            if c is not None and (c >= ep * (1 + self.tp) or c <= ep * (1 - self.stop)
                                  or i - self.since.get(s, i) >= self.max_hold):
                self.entry_px.pop(s, None)
                self.since.pop(s, None)
            else:
                keep[s] = 1 / self.slots
        if not self.regime or btc_uptrend(cd, i, self.regime):
            cands = []
            for s in cd.coins:
                if s in keep:
                    continue
                xs = cd.closes(s, i, self.trend)
                if xs and xs[-1] > sum(xs) / len(xs) and xs[-1] / xs[-7] - 1 <= -self.drop:
                    cands.append((xs[-1] / xs[-7], s))
            for _, s in sorted(cands)[:self.slots - len(keep)]:
                keep[s] = 1 / self.slots
                self.entry_px[s] = cd.c[s][i]
                self.since[s] = i
        return keep if set(keep) != set(held) else None


def _action(st: Strategy) -> Strategy:
    st.group = "Fast: action"
    return st


def fast_strategies() -> list[Strategy]:
    out: list[Strategy] = [research.HoldBTC()]
    for entry, exit_ in ((12, 6), (18, 9), (30, 12)):          # 2/1, 3/1.5 and 5/2 days
        for slots in (1, 2):
            out.append(FastBreakout(entry, exit_, slots))
    out += [FastBreakout(18, 9, 2, tp=0.25), FastBreakout(18, 9, 2, tp=None),          # how much gain to take
            FastBreakout(18, 9, 2, max_hold=None), FastBreakout(18, 9, 3),
            FastBreakout(18, 9, 2, regime=None)]
    out += [PumpRider(), PumpRider(min_move=0.15, tp=0.2, stop=0.08), PumpRider(look=3, min_move=0.06, tp=0.08,
                                                                              stop=0.04, max_hold=6),
            PumpRider(slots=1)]
    # does real buying (volume) behind a move make it last?
    out += [FastBreakout(18, 9, 2, vol_x=2.0), FastBreakout(12, 6, 1, vol_x=2.0), PumpRider(vol_x=2.0),
            PumpRider(min_move=0.15, tp=0.2, stop=0.08, vol_x=3.0)]
    out += [DipBuyer(), DipBuyer(drop=0.15, tp=0.1, stop=0.1), DipBuyer(slots=1)]
    # action: many quick trades with small targets, several a week. Does anything survive the fees?
    out += [_action(FastBreakout(6, 3, 2, tp=0.06, max_hold=12)),
            _action(FastBreakout(6, 3, 2, tp=0.1, max_hold=18, vol_x=2.0)),
            _action(PumpRider(look=6, min_move=0.08, tp=0.1, stop=0.05, max_hold=6, vol_x=2.0)),
            _action(PumpRider(look=2, min_move=0.05, tp=0.06, stop=0.04, max_hold=6)),
            _action(DipBuyer(drop=0.07, tp=0.05, stop=0.06, max_hold=12))]
    # the daily brain's rules on the same coins and bars, as the slow yardstick
    out.append(FastBreakout(120, 60, 3, tp=None, max_hold=None, label="Slow yardstick: the daily brain's 20/10-day rules",
                            group="Slow (for comparison)"))
    return out


def by_name(name: str) -> Strategy | None:
    return next((s for s in fast_strategies() if s.name == name), None)


# ------------------------------------------------------------------ data: 4-hour candles from Binance, cached
async def pick_coins(client, fusion: set[str] | None) -> list[str]:
    """The most traded coins on Binance (by USD volume) that Fusion also lists. Bitcoin always, for the filter."""
    data = (await client.get(TICKER)).json()
    if not isinstance(data, list):
        raise ValueError(str(data)[:120])
    vol = {}
    for t in data:
        sym = t.get("symbol", "")
        if sym.endswith("USDT"):
            base = sym[:-4]
            if base not in STABLES and (not fusion or base in fusion):
                vol[base] = float(t.get("quoteVolume") or 0)
    coins = [c for c, _ in sorted(vol.items(), key=lambda x: -x[1])][:MAX_COINS]
    return ["BTC"] + [c for c in coins if c != "BTC"][:MAX_COINS - 1]


async def fetch_4h(client, sym: str, since: float) -> list[tuple]:
    rows, start = [], int(since * 1000)
    while True:
        r = await client.get(BINANCE, params={"symbol": f"{sym}USDT", "interval": "4h", "startTime": start, "limit": 1000})
        data = r.json()
        if not isinstance(data, list):
            raise ValueError(str(data)[:120])
        rows += [(x[0] / 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7])) for x in data]
        if len(data) < 1000:
            break
        start = int(data[-1][0]) + BAR * 1000
        await asyncio.sleep(0.25)
    return rows


async def update_4h(client, db, coins: list[str], log=lambda *a: None, progress=lambda *a: None) -> dict[str, list[tuple]]:
    """Three years of 4-hour candles per coin, cached (kv h4:SYM); later runs download only the new bars."""
    out = {}
    now = time.time() // BAR * BAR
    oldest = now - HISTORY_DAYS * 86400
    for k, sym in enumerate(coins):
        progress(k, len(coins), sym)
        have = [tuple(r) for r in (db.get(f"h4:{sym}") or []) if r[0] >= oldest and len(r) > 5]
        since = have[-1][0] + BAR if have else oldest
        new = []
        if since < now:
            try:
                new = await fetch_4h(client, sym, since)
            except Exception as ex:
                log("Fast Lab", "warn", f"No 4-hour history for {sym} ({str(ex)[:60]}).")
        merged = {r[0]: r for r in have}
        merged.update({r[0]: r for r in new if r[0] < now})  # the current bar is still forming
        rows = sorted(merged.values())
        if len(rows) >= 400:
            db.set(f"h4:{sym}", [list(r) for r in rows])
            out[sym] = rows
        await asyncio.sleep(0.1)
    return out


def synthetic_4h(coins: list[str], days: int = 900) -> dict[str, list[tuple]]:
    """Random stand-ins for --simulate and tests: daily random walks cut into 6 bars a day."""
    daily = research.synthetic_rows(coins, days=days * BARS_PER_DAY, seed=11)
    t0 = time.time() // BAR * BAR - days * BARS_PER_DAY * BAR
    rnd = random.Random(5)
    out = {}
    for s, rows in daily.items():
        out[s] = [(t0 + k * BAR, o, h, l, c, c * 1e5 * (1 + abs(c / o - 1) * 20) * rnd.uniform(0.5, 1.5))
                  for k, (_, o, h, l, c) in enumerate(rows)]
    return out


# ------------------------------------------------------------------ signals: what tells us something about the next day?
def _ret(cd, s, i, n):
    xs = cd.closes(s, i, n + 1)
    return xs[-1] / xs[0] - 1 if xs else None


def _logrets(cd, s, i, n):
    xs = cd.closes(s, i, n + 1)
    return [math.log(b / a) for a, b in zip(xs[:-1], xs[1:])] if xs else None


def _corr_beta(cd, s, i, n):
    """Correlation and beta of a coin's 4-hour moves with Bitcoin's over the last n bars."""
    a, b = _logrets(cd, s, i, n), _logrets(cd, "BTC", i, n)
    if not a or not b:
        return None, None
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    if not va or not vb:
        return None, None
    return cov / math.sqrt(va * vb), cov / vb


def _rsi(cd, s, i, n=14):
    xs = cd.closes(s, i, n + 1)
    if not xs:
        return None
    up = sum(max(0, b - a) for a, b in zip(xs[:-1], xs[1:]))
    dn = sum(max(0, a - b) for a, b in zip(xs[:-1], xs[1:]))
    return 100.0 if dn == 0 else 100 - 100 / (1 + up / dn)


def _own_move(cd, s, i):
    """The coin's last-day move minus what Bitcoin's move explains (beta x Bitcoin's move)."""
    r, rb = _ret(cd, s, i, BARS_PER_DAY), _ret(cd, "BTC", i, BARS_PER_DAY)
    _, beta = _corr_beta(cd, s, i, 7 * BARS_PER_DAY)
    return r - beta * rb if None not in (r, rb, beta) else None


def _breadth(cd, i):
    rs = [r for r in (_ret(cd, s, i, BARS_PER_DAY) for s in cd.coins if s != "BTC") if r is not None]
    return sum(1 for r in rs if r > 0) / len(rs) if len(rs) >= 5 else None


def _dispersion(cd, i):
    rs = [r for r in (_ret(cd, s, i, BARS_PER_DAY) for s in cd.coins if s != "BTC") if r is not None]
    if len(rs) < 5:
        return None
    m = sum(rs) / len(rs)
    return math.sqrt(sum((r - m) ** 2 for r in rs) / (len(rs) - 1))


def fast_candidates(seed: int = 9) -> list[dict]:
    """Signals for the next 24 hours: the classic ones, a few less common ones, and random controls."""
    rnd_m, rnd_c = random.Random(seed), random.Random(seed + 1)
    noise_m: dict[int, float] = {}
    noise_c: dict[tuple, float] = {}
    D = BARS_PER_DAY
    return [
        {"name": "Last 24h move", "kind": "coin", "source": "prices",
         "idea": "Classic: does yesterday's winner keep running (momentum) or give it back (reversal)?",
         "f": lambda cd, s, i: _ret(cd, s, i, D)},
        {"name": "Last 4h move", "kind": "coin", "source": "prices",
         "idea": "Very short moves are often noise that reverses.",
         "f": lambda cd, s, i: _ret(cd, s, i, 1)},
        {"name": "Last 7 days move", "kind": "coin", "source": "prices",
         "idea": "Medium-term momentum, the daily brain's edge, on a one-day horizon.",
         "f": lambda cd, s, i: _ret(cd, s, i, 7 * D)},
        {"name": "RSI (14 x 4h)", "kind": "coin", "source": "prices",
         "idea": "Classic: high RSI = overbought (should fall), low = oversold (should bounce).",
         "f": _rsi},
        {"name": "Volume surge (24h vs the week before)", "kind": "coin", "source": "Binance volume",
         "idea": "Unusual trading: new buyers arriving, or the crowd piling in at the top?",
         "f": vol_surge},
        {"name": "Move on heavy volume", "kind": "coin", "source": "prices + volume",
         "idea": "A move backed by twice the usual volume should last longer than one on thin trading.",
         "f": lambda cd, s, i: (lambda r, v: r * min(v, 5) if r is not None and v is not None else None)(
             _ret(cd, s, i, D), vol_surge(cd, s, i))},
        {"name": "Distance to the 3-day high", "kind": "coin", "source": "prices",
         "idea": "The fast breakout rule: coins at a fresh high vs coins far below it.",
         "f": lambda cd, s, i: (lambda hs, c: c / max(hs) - 1 if hs and c else None)(
             [x for x in cd.h[s][max(0, i - 3 * D):i] if x is not None], cd.c[s][i])},
        {"name": "Volatility, last 3 days", "kind": "coin", "source": "prices",
         "idea": "Do wild coins pay for their risk on a one-day view, or just lose more?",
         "f": lambda cd, s, i: (lambda r: math.sqrt(sum(x * x for x in r) / len(r)) if r else None)(
             _logrets(cd, s, i, 3 * D))},
        {"name": "Own move (Bitcoin's influence removed)", "kind": "coin", "source": "prices, idea",
         "idea": "A coin that rose more than its usual link to Bitcoin explains has its own news. Does that last?",
         "f": _own_move},
        {"name": "Correlation with Bitcoin, last 7 days", "kind": "coin", "source": "prices, idea",
         "idea": "Coins breaking away from Bitcoin often have their own story (good or bad).",
         "f": lambda cd, s, i: _corr_beta(cd, s, i, 7 * D)[0]},
        {"name": "Beta to Bitcoin, last 7 days", "kind": "coin", "source": "prices",
         "idea": "Coins that swing harder than Bitcoin: more gain in good days, more pain in bad ones.",
         "f": lambda cd, s, i: _corr_beta(cd, s, i, 7 * D)[1]},
        {"name": "Random number (control)", "kind": "coin", "source": "chance",
         "idea": "Pure chance, as a yardstick. Must always fail.",
         "f": lambda cd, s, i: noise_c.setdefault((s, i), rnd_c.gauss(0, 1))},
        {"name": "Bitcoin's last 4h → altcoins next 24h", "kind": "market", "target": "ALTS", "source": "prices, idea",
         "idea": "Lead-lag: Bitcoin moves first and the smaller coins follow a few hours later.",
         "f": lambda cd, s, i: _ret(cd, "BTC", i, 1)},
        {"name": "Bitcoin's last 24h → altcoins next 24h", "kind": "market", "target": "ALTS", "source": "prices",
         "idea": "Does money rotate into altcoins after a strong Bitcoin day?",
         "f": lambda cd, s, i: _ret(cd, "BTC", i, D)},
        {"name": "Bitcoin's last 24h → Bitcoin next 24h", "kind": "market", "source": "prices",
         "idea": "Does Bitcoin's own daily move continue or reverse?",
         "f": lambda cd, s, i: _ret(cd, "BTC", i, D)},
        {"name": "Breadth: share of coins up over 24h", "kind": "market", "target": "ALTS", "source": "prices",
         "idea": "When almost everything rises together the move is broad, or already exhausted.",
         "f": lambda cd, s, i: _breadth(cd, i)},
        {"name": "Altcoin dispersion (how differently coins move)", "kind": "market", "target": "ALTS",
         "source": "prices, idea", "idea": "High dispersion = coin-picking season; low = everything follows Bitcoin.",
         "f": lambda cd, s, i: _dispersion(cd, i)},
        {"name": "Market-wide volume surge", "kind": "market", "target": "ALTS", "source": "Binance volume",
         "idea": "A frenzy across all coins: fuel for more, or the top?",
         "f": lambda cd, s, i: (lambda xs: sum(xs) / len(xs) if len(xs) >= 5 else None)(
             [x for x in (vol_surge(cd, s2, i) for s2 in cd.coins) if x is not None])},
        {"name": "Weekend ahead", "kind": "market", "target": "ALTS", "source": "calendar",
         "idea": "Thin weekend trading: do coins drift differently on Saturday and Sunday?",
         "f": lambda cd, s, i: 1.0 if time.gmtime(cd.days[i] + BAR).tm_wday >= 5 else 0.0},
        {"name": "Random number (control)", "kind": "market", "target": "ALTS", "source": "chance",
         "idea": "Pure chance, as a yardstick. Must always fail.",
         "f": lambda cd, s, i: noise_m.setdefault(i, rnd_m.gauss(0, 1))},
    ]


def correlations(cd: Candles) -> dict:
    """How tightly each coin follows Bitcoin now (last 30 days) vs over the whole period, and the average link
    of altcoins to Bitcoin month by month."""
    i = len(cd.days) - 1
    month = 30 * BARS_PER_DAY
    coins = []
    for s in cd.coins:
        if s == "BTC":
            continue
        c30, b30 = _corr_beta(cd, s, i, month)
        n = sum(1 for x in cd.c[s] if x is not None) - 1
        call, ball = _corr_beta(cd, s, i, min(n, i))
        if c30 is None:
            continue
        coins.append({"coin": s, "corr_30d": round(c30, 2), "corr_all": None if call is None else round(call, 2),
                      "beta_30d": round(b30, 2), "change": None if call is None else round(c30 - call, 2),
                      "move_30d_pct": None if _ret(cd, s, i, month) is None else round(_ret(cd, s, i, month) * 100, 1),
                      "vol_surge": None if vol_surge(cd, s, i) is None else round(vol_surge(cd, s, i), 2)})
    coins.sort(key=lambda r: r["corr_30d"])
    series = []
    for k in range(month, len(cd.days), month):
        cs = [c for c in (_corr_beta(cd, s, k, month)[0] for s in cd.coins if s != "BTC") if c is not None]
        if len(cs) >= 5:
            series.append({"date": time.strftime("%Y-%m-%d", time.gmtime(cd.days[k])),
                           "avg_corr": round(sum(cs) / len(cs), 2),
                           "dispersion_pct": None if _dispersion(cd, k) is None else round(_dispersion(cd, k) * 100, 1)})
    return {"coins": coins, "series": series}


def run(cd: Candles) -> dict:
    """All fast strategies with the full set of checks at the fast lab's higher cost per side, the signal test on
    the next 24 hours, and the link of every coin to Bitcoin over time."""
    res = research.run_all(cd, fast_strategies(), cost=COST, lookup=by_name)
    res["bar_hours"] = 4
    if not res.get("coin_luck"):  # nothing robust: still show whether the best-looking fast rules hold up
        fast = sorted((r for r in res["rows"] if r["group"].startswith("Fast")), key=lambda r: -r["full"].get("sharpe", -9))
        start = max(s.warmup for s in fast_strategies())
        res["coin_luck"] = research.coin_luck(cd, [r["name"] for r in fast[:3]], start, lookup=by_name, cost=COST)
    res["cost_per_side_pct"] = round(COST * 100, 2)
    res["patterns"] = patterns.analyse(cd, 300, fast_candidates(), BARS_PER_DAY)
    res["correlation"] = correlations(cd)
    return res
