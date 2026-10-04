"""Strategy settings (what the Optimizer tunes) and the indicator math the analysts use."""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, fields

SIGNALS = ["momentum", "trend", "reversion", "breakout", "hype", "news", "market"]


@dataclass
class StrategyConfig:
    # how much each signal counts in the Predictor's score (can be negative = contrarian)
    w_momentum: float = 1.0
    w_trend: float = 1.0
    w_reversion: float = 0.3
    w_breakout: float = 0.6
    w_hype: float = 0.5
    w_news: float = 0.7
    w_market: float = 0.5
    entry_score: float = 0.35       # buy when score is above this
    exit_score: float = -0.15       # sell when score falls below this
    take_profit_pct: float = 6.0
    stop_loss_pct: float = 4.0
    trailing_stop_pct: float = 3.0
    position_pct: float = 20.0      # share of equity per new position (Risk Officer caps it)
    max_positions: int = 4
    min_hold_minutes: float = 20.0
    cooldown_minutes: float = 60.0  # wait after selling before re-buying the same symbol
    trade_stocks: bool = False      # stocks are watched for signals; trading them comes later
    min_edge_pct: float = 0.0       # only buy coins expected to move at least this much (0 = off)

    def weights(self) -> dict[str, float]:
        return {s: getattr(self, f"w_{s}") for s in SIGNALS}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyConfig":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

    def fingerprint(self) -> str:
        return hashlib.sha1(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:8]

    def mutate(self, rng: random.Random, strength: float = 0.25) -> "StrategyConfig":
        """A nearby variant: the Optimizer's way of exploring."""
        d = self.to_dict()
        for k, v in d.items():
            if isinstance(v, bool) or rng.random() > 0.5:
                continue
            if k.startswith("w_"):
                d[k] = round(max(-2.0, min(2.0, v + rng.gauss(0, strength))), 3)
            elif k in ("entry_score", "exit_score"):  # thresholds can be negative: nudge, don't scale
                d[k] = round(v + rng.gauss(0, strength * 0.4), 3)
            elif k == "min_edge_pct" and v == 0:
                continue  # an off gate stays off unless a variant starts with one
            elif isinstance(v, int):
                d[k] = max(1, min(6, v + rng.choice([-1, 1])))
            else:
                d[k] = round(max(0.05, v * math.exp(rng.gauss(0, strength))), 3)
        d["entry_score"] = min(0.9, max(0.1, d["entry_score"]))
        d["exit_score"] = min(d["entry_score"] - 0.1, max(-0.9, d["exit_score"]))
        d["position_pct"] = min(25.0, max(5.0, d["position_pct"]))
        d["min_edge_pct"] = min(15.0, d["min_edge_pct"])
        return StrategyConfig.from_dict(d)


# A few hand-made starting personalities so experiments begin with real contrast
SEED_VARIANTS = {
    "Balanced": StrategyConfig(),
    "Momentum Rider": StrategyConfig(w_momentum=1.6, w_trend=1.2, w_reversion=-0.2, w_breakout=1.0, w_hype=0.3,
                                     entry_score=0.4, take_profit_pct=8, stop_loss_pct=3.5, trailing_stop_pct=2.5),
    "Hype Surfer": StrategyConfig(w_momentum=0.6, w_trend=0.4, w_reversion=0.0, w_breakout=0.5, w_hype=1.8, w_news=1.2,
                                  entry_score=0.3, take_profit_pct=10, stop_loss_pct=5, min_hold_minutes=10),
    # The Professor's first research idea: only trade when the likely move beats 2x the ~3% round-trip fee
    "Fee Guard": StrategyConfig(min_edge_pct=6.0, entry_score=0.35, take_profit_pct=8, stop_loss_pct=5,
                                min_hold_minutes=45),
    "Dip Buyer": StrategyConfig(w_momentum=-0.4, w_trend=0.3, w_reversion=1.6, w_breakout=-0.3, w_hype=0.2, w_news=0.5,
                                entry_score=0.3, exit_score=-0.05, take_profit_pct=4, stop_loss_pct=6, min_hold_minutes=30),
}


# ---------------- indicator math (pure functions, no AI, free) ----------------

def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains = losses = 0.0
    for a, b in zip(values[-period - 1:-1], values[-period:]):
        d = b - a
        gains += max(d, 0)
        losses += max(-d, 0)
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def volatility(values: list[float]) -> float:
    """Std-dev of 1-minute log returns."""
    if len(values) < 3:
        return 0.0
    rets = [math.log(b / a) for a, b in zip(values[:-1], values[1:]) if a > 0 and b > 0]
    if len(rets) < 2:
        return 0.0
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))


def squash(x: float) -> float:
    """Map any number into -1..1."""
    return math.tanh(x)


def technical_signals(closes: list[float]) -> dict[str, float]:
    """Momentum, trend, mean-reversion and breakout signals in -1..1 from 1-minute closes."""
    if len(closes) < 30:
        return {"momentum": 0.0, "trend": 0.0, "reversion": 0.0, "breakout": 0.0, "vol": 0.0}
    last = closes[-1]
    vol = volatility(closes[-120:]) or 1e-4
    ret15 = math.log(last / closes[-16])
    momentum = squash(ret15 / (vol * math.sqrt(15)) / 2)
    fast, slow = ema(closes[-120:], 20), ema(closes[-240:], 60)
    trend = squash((fast - slow) / slow / (vol * 4)) if slow else 0.0
    five_min = closes[::-5][::-1][-40:]
    reversion = (50 - rsi(five_min)) / 50
    window = closes[-61:-1]
    hi, lo = max(window), min(window)
    if last > hi:
        breakout = min(1.0, (last - hi) / hi / vol / 2 + 0.5)
    elif last < lo:
        breakout = max(-1.0, (last - lo) / lo / vol / 2 - 0.5)
    else:
        breakout = 0.0
    return {"momentum": momentum, "trend": trend, "reversion": reversion, "breakout": breakout, "vol": vol}
