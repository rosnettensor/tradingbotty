"""The trading rules in one place, as plain functions.

The live Buyer and the backtester both call these, so a backtest replays exactly the rules the bot trades with.
"""
from __future__ import annotations

import math

from .strategy import StrategyConfig


def exit_reason(cfg: StrategyConfig, pos, price: float, score: float, now: float, avoid: set[str]) -> str | None:
    """Why this position should be sold now, or None to keep holding. Updates the position's peak price."""
    pos.peak = max(pos.peak, price)
    change = (price / pos.avg_price - 1) * 100
    from_peak = (price / pos.peak - 1) * 100
    held_min = (now - pos.opened) / 60
    if change <= -cfg.stop_loss_pct:
        return f"stop loss {change:.1f}%"
    if change >= cfg.take_profit_pct:
        return f"take profit {change:+.1f}%"
    if change > 1.0 and from_peak <= -cfg.trailing_stop_pct:
        return f"trailing stop ({from_peak:.1f}% from peak)"
    if held_min >= cfg.min_hold_minutes and score < cfg.exit_score:
        return f"score dropped to {score:+.2f}"
    # The Professor's avoid list only blocks new buys: an AI opinion alone never forces a sell (no fee churn).
    return None


def expected_move(change_24h_pct: float, vol: float) -> float:
    """How much a coin plausibly moves: the bigger of its 24h change and its volatility projected over 4 hours."""
    return max(abs(change_24h_pct), vol * math.sqrt(240) * 100)


def entry_blocker(cfg: StrategyConfig, broker, symbol: str, score: float, now: float, avoid: set[str],
                  buys_last_hour: int, move_pct: float) -> str | None:
    """Why the strategy itself won't buy this symbol now (before the Risk Officer's limits), or None."""
    if score < cfg.entry_score:
        return "score below buy line"
    if len(broker.positions) >= cfg.max_positions:
        return "strategy's position limit"
    if symbol in broker.positions:
        return "already held"
    if symbol in avoid:
        return "Professor says avoid"
    if now - broker.last_sell.get(symbol, 0) < cfg.cooldown_minutes * 60:
        return "cooling down after a sell"
    if buys_last_hour >= cfg.max_buys_per_hour:
        return "hourly buy limit"
    if cfg.min_edge_pct > 0 and move_pct < cfg.min_edge_pct:
        return "expected move too small for the fees"
    return None


def risk_check(r: dict, broker, day_start_equity: float, kill: bool, symbol: str, usd: float,
               prices: dict) -> tuple[float, str | None]:
    """The Risk Officer's hard limits. Returns (allowed_usd, reason_if_blocked). Spending never exceeds cash."""
    if kill:
        return 0, "kill switch is on"
    equity = broker.equity(prices)
    if day_start_equity and equity < day_start_equity * (1 - r["max_daily_loss_pct"] / 100):
        return 0, "daily loss limit reached, no new buys today"
    if len(broker.positions) >= r["max_open_positions"] and symbol not in broker.positions:
        return 0, "too many open positions"
    held = broker.positions[symbol].value(prices.get(symbol, 0)) if symbol in broker.positions else 0.0
    cap = equity * r["max_position_pct"] / 100 - held
    usd = min(usd, cap, broker.cash - r["min_cash_reserve_usd"])
    if usd < r["min_order_usd"]:
        return 0, "order too small after limits"
    return usd, None


def blend(weights: dict[str, float], sig: dict[str, float]) -> float:
    """One score from all signals, roughly -1..1."""
    norm = sum(abs(x) for x in weights.values()) or 1.0
    return sum(weights[k] * sig.get(k, 0.0) for k in weights) / norm * 2
