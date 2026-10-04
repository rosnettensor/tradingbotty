"""Strategy settings (what the Optimizer tunes) and the indicator math the analysts use."""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass, fields

SIGNALS = ["momentum", "trend", "reversion", "breakout", "swing", "hype", "news", "market"]


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
    w_swing: float = 0.0            # multi-day trend (hourly candles over 1-3 weeks)
    entry_score: float = 0.35       # buy when score is above this
    exit_score: float = -0.15       # sell when score falls below this
    take_profit_pct: float = 6.0
    stop_loss_pct: float = 4.0
    trailing_stop_pct: float = 3.0
    position_pct: float = 20.0      # share of equity per new position (Risk Officer caps it)
    max_positions: int = 4
    min_hold_minutes: float = 20.0
    cooldown_minutes: float = 60.0  # wait after selling before re-buying the same symbol
    trade_stocks: bool = False      # paper-trade US stocks too (only while the US market is open)
    min_edge_pct: float = 0.0       # only buy coins expected to move at least this much (0 = off)
    trade_crypto: bool = True
    max_buys_per_hour: int = 6      # speed limit on new positions
    hold: bool = False              # benchmark: buy a basket once and never sell

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
                d[k] = round(v + rng.gauss(0, strength), 3)
            elif k in ("entry_score", "exit_score"):  # thresholds can be negative: nudge, don't scale
                d[k] = round(v + rng.gauss(0, strength * 0.4), 3)
            elif k == "min_edge_pct" and v == 0:
                continue  # an off gate stays off unless a variant starts with one
            elif isinstance(v, int):
                d[k] = v + rng.choice([-1, 1])
            else:
                d[k] = round(max(0.05, v * math.exp(rng.gauss(0, strength))), 3)
        return StrategyConfig.from_dict(d).clamped()

    def clamped(self) -> "StrategyConfig":
        """Pull every setting back into its allowed range (used for mutations and for edits from the dashboard)."""
        d = self.to_dict()
        for k, meta in STRATEGY_FIELDS.items():
            if "min" in meta and k in d and not isinstance(d[k], bool):
                v = max(meta["min"], min(meta["max"], float(d[k])))
                v = round(round(v / meta["step"]) * meta["step"], 4)  # snap to the slider's step
                d[k] = int(round(v)) if meta.get("int") else v
        d["exit_score"] = min(d["entry_score"] - 0.1, d["exit_score"])
        if not d["trade_crypto"] and not d["trade_stocks"]:
            d["trade_crypto"] = True
        return StrategyConfig.from_dict(d)


# What each strategy setting means. The dashboard builds its sliders from this, and clamped() enforces the ranges.
STRATEGY_FIELDS = {
    "entry_score": {"label": "Buy threshold", "min": 0.1, "max": 0.9, "step": 0.01, "group": "When to buy",
                    "help": "Buy when a coin's blended score is above this. Lower = buys more often, higher = pickier."},
    "max_buys_per_hour": {"label": "Max buys per hour", "min": 1, "max": 20, "step": 1, "int": True, "group": "When to buy",
                          "help": "Speed limit on new positions, so a noisy hour can't burn fees."},
    "cooldown_minutes": {"label": "Cooldown after selling (min)", "min": 0, "max": 480, "step": 5, "group": "When to buy",
                         "help": "Wait this long before buying the same coin again."},
    "min_edge_pct": {"label": "Fee guard: min expected move (%)", "min": 0, "max": 15, "step": 0.5, "group": "When to buy",
                     "help": "Only buy coins likely to move at least this much. 0 = off. About 2x the round-trip fee is sensible."},
    "position_pct": {"label": "Position size (% of equity)", "min": 5, "max": 50, "step": 1, "group": "How much",
                     "help": "How much of the account goes into each new buy. The Risk Officer's hard cap still applies."},
    "max_positions": {"label": "Max open positions", "min": 1, "max": 10, "step": 1, "int": True, "group": "How much",
                      "help": "How many coins it may hold at the same time."},
    "exit_score": {"label": "Sell threshold", "min": -0.9, "max": 0.5, "step": 0.01, "group": "When to sell",
                   "help": "Sell when the score falls below this (after the minimum hold time)."},
    "take_profit_pct": {"label": "Take profit (%)", "min": 0.5, "max": 50, "step": 0.5, "group": "When to sell",
                        "help": "Sell everything once the position is up this much."},
    "stop_loss_pct": {"label": "Stop loss (%)", "min": 0.5, "max": 30, "step": 0.5, "group": "When to sell",
                      "help": "Sell once the position is down this much. Smaller = less risk per trade, more fees."},
    "trailing_stop_pct": {"label": "Trailing stop (%)", "min": 0.5, "max": 20, "step": 0.5, "group": "When to sell",
                          "help": "Once in profit, sell if the price drops this much from its peak."},
    "min_hold_minutes": {"label": "Min hold time (min)", "min": 0, "max": 720, "step": 5, "group": "When to sell",
                         "help": "Don't sell on a weak score before this (stops and take-profit still work)."},
    "w_momentum": {"label": "Momentum", "min": -2, "max": 2, "step": 0.05, "group": "Signal weights",
                   "help": "Last 15 minutes' move compared with normal volatility."},
    "w_trend": {"label": "Trend", "min": -2, "max": 2, "step": 0.05, "group": "Signal weights",
                "help": "Fast vs slow moving average (20 vs 60 minutes)."},
    "w_reversion": {"label": "Dip buying", "min": -2, "max": 2, "step": 0.05, "group": "Signal weights",
                    "help": "RSI: positive when oversold. High weight = buys dips."},
    "w_breakout": {"label": "Breakout", "min": -2, "max": 2, "step": 0.05, "group": "Signal weights",
                   "help": "Price breaking above its last-hour high (or below its low)."},
    "w_swing": {"label": "Multi-day trend", "min": -2, "max": 2, "step": 0.05, "group": "Signal weights",
                "help": "Price trend over 1 to 3 weeks from hourly candles. Slow, so it trades rarely and pays fewer fees."},
    "w_hype": {"label": "Social hype", "min": -2, "max": 2, "step": 0.05, "group": "Signal weights",
               "help": "Reddit buzz and CoinGecko trending, corrected by the Hype vs Price Detective."},
    "w_news": {"label": "News", "min": -2, "max": 2, "step": 0.05, "group": "Signal weights",
               "help": "News Hunter's rating of recent headlines, fading over about 3 hours."},
    "w_market": {"label": "Market mood", "min": -2, "max": 2, "step": 0.05, "group": "Signal weights",
                 "help": "Is the whole market risk-on? Bitcoin trend plus Fear & Greed for coins, S&P and Nasdaq for stocks."},
    "trade_crypto": {"label": "Trade crypto", "group": "Markets", "help": "Buy coins (24/7)."},
    "trade_stocks": {"label": "Trade US stocks (paper)", "group": "Markets",
                     "help": "Buy stocks and ETFs from your watchlist, only while the US market is open. Paper only for now."},
}


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
    # Stocks cost a minimum fee per order (about 1 USD), so it trades rarely, in bigger slices, leaning on the slow trend
    "Stock Picker": StrategyConfig(trade_stocks=True, trade_crypto=False, w_hype=0.3, w_news=1.0, w_swing=1.0,
                                   entry_score=0.45, take_profit_pct=6, stop_loss_pct=4, trailing_stop_pct=3,
                                   min_hold_minutes=180, cooldown_minutes=1440, max_buys_per_hour=1, position_pct=25),
    # Trades the slow multi-day trend: few trades, wide stops. Research on crypto trend-following favors days over minutes.
    "Swing Trader": StrategyConfig(w_momentum=0.1, w_trend=0.3, w_reversion=0.0, w_breakout=0.2, w_swing=2.0, w_hype=0.1,
                                   w_news=0.3, w_market=0.6, entry_score=0.35, exit_score=-0.1, take_profit_pct=20,
                                   stop_loss_pct=8, trailing_stop_pct=7, min_hold_minutes=720, cooldown_minutes=1440,
                                   max_buys_per_hour=2, position_pct=24),
    # The yardstick: buy the four biggest coins once and do nothing. A strategy is only good if it beats this.
    "Buy & Hold": StrategyConfig(hold=True),
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


def swing_signal(hourly: list[float]) -> float:
    """Multi-day trend in -1..1 from hourly closes: 7- and 20-day returns scaled by daily volatility."""
    if len(hourly) < 24 * 8:
        return 0.0
    daily = hourly[::-24][::-1]
    rets = [math.log(b / a) for a, b in zip(daily[:-1], daily[1:]) if a > 0 and b > 0][-20:]
    if len(rets) < 5:
        return 0.0
    m = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) or 0.02
    last = hourly[-1]
    r7 = math.log(last / hourly[-1 - 24 * 7])
    days = min(20, (len(hourly) - 1) // 24)
    r_long = math.log(last / hourly[-1 - 24 * days])
    return squash(0.5 * r7 / (vol * math.sqrt(7)) + 0.5 * r_long / (vol * math.sqrt(days)))


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
