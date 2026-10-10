"""Fusion volatility watchlist and an isolated forward paper experiment.

No execution adapter is passed to the paper portfolio. Range is not realized
volatility; momentum is between observed scans, not a claimed 24-hour return.
"""
from __future__ import annotations

import asyncio
import math
import time

from .risk import positive, spot_only


def rank(tickers: list[dict], pairs: dict, previous: dict, now: float) -> list[dict]:
    rows = []
    for t in tickers:
        symbol = str(t.get("pair", "")).partition("-")[0].upper()
        pair = pairs.get(symbol)
        if not pair or t.get("pair") != pair.get("pair"):
            continue
        try:
            spot_only(pair)
            price, high, low = (positive(t.get(k), k) for k in ("price", "high", "low"))
            volume = positive(t.get("volume"), "reported volume")
            if high < low or not low <= price <= high:
                continue
            old = previous.get(symbol) or {}
            # A restart/network gap cannot turn a stale price into a fresh signal.
            fresh = 30 <= now - old.get("ts", 0) <= 600
            momentum = (price / old["price"] - 1) * 100 if fresh else None
            breakout = bool(fresh and price > old["high"])
            rows.append({"symbol": symbol, "price": price, "high": high, "low": low,
                         "range_pct": round((high - low) / price * 100, 2),
                         "momentum_pct": round(momentum, 2) if momentum is not None else None,
                         "breakout": breakout, "volume_reported": volume,
                         "min_order": float(pair.get("minOrderAmount") or 0),
                         "newly_seen": bool(previous and not old)})
        except (ValueError, TypeError, KeyError, ZeroDivisionError):
            continue
    return sorted(rows, key=lambda r: -r["range_pct"])


def paper_step(portfolio: dict, rows: list[dict], now: float, fee_pct: float,
               slippage_pct: float, paused: bool = False) -> dict:
    """One forward observation. Never touches live balances, orders or trade tables."""
    p = {"cash": 1000.0, "positions": {}, "trades": [], "realized": 0.0, **portfolio}
    p["positions"] = {s: dict(v) for s, v in p["positions"].items()}
    p["trades"] = list(p["trades"])
    day = now // 86400 * 86400
    if p.get("loss_day") != day:
        p["daily_loss"] = sum(max(0.0, -t.get("pnl", 0)) for t in p["trades"] if t["ts"] >= day)
        p["loss_day"] = day
    # Metrics begin here for existing portfolios; never invent truncated history.
    p.setdefault("metrics_since", now)
    p.setdefault("closed_count", 0)
    p.setdefault("wins", 0)
    p.setdefault("fees_paid", 0.0)
    p["observations"] = p.get("observations", 0) + 1
    fee, slip = fee_pct / 100, slippage_pct / 100
    by = {r["symbol"]: r for r in rows}
    closed = set()
    for s, pos in list(p["positions"].items()):
        r = by.get(s)
        pos["stale"] = not bool(r)
        if not r:
            continue  # missing prices don't create a fabricated fill
        pos["mark"] = r["price"]
        pos["mark_ts"] = now
        ratio = r["price"] / pos["entry"] - 1
        if ratio > -0.08 and ratio < 0.20 and now - pos["ts"] < 86400:
            continue
        received = pos["qty"] * r["price"] * (1 - slip) * (1 - fee)
        pnl = received - pos["cost"]
        p["cash"] += received
        p["realized"] += pnl
        p["daily_loss"] += max(0.0, -pnl)
        p["closed_count"] += 1
        p["wins"] += int(pnl > 0)
        p["fees_paid"] += pos["qty"] * r["price"] * (1 - slip) * fee
        p["trades"].append({"ts": now, "symbol": s, "side": "SELL", "pnl": round(pnl, 2)})
        del p["positions"][s]
        closed.add(s)
    realized_loss = p["daily_loss"]
    unrealized_loss = sum(max(0.0, v["cost"] - v["qty"] * v["mark"]) for v in p["positions"].values())
    p["loss_used"] = round(realized_loss + unrealized_loss, 2)
    p["paused"] = paused or p["loss_used"] >= 200
    paused = p["paused"]
    # 3 slots, <=100 quote currency per trade, cash-only; no new buys under kill switch.
    for r in rows:
        s = r["symbol"]
        if (paused or not r.get("eligible") or not r["breakout"] or (r["momentum_pct"] or 0) < 2
                or s in p["positions"] or s in closed or len(p["positions"]) >= 3):
            continue
        amount = min(100.0, p["cash"] / (1 + fee))
        if amount < max(25.0, r["min_order"]):
            continue
        entry = r["price"] * (1 + slip)
        cost = amount * (1 + fee)
        p["cash"] -= cost
        p["fees_paid"] += amount * fee
        p["positions"][s] = {"qty": amount / entry, "entry": entry, "cost": cost, "ts": now, "mark": r["price"], "mark_ts": now, "stale": False}
        p["trades"].append({"ts": now, "symbol": s, "side": "BUY", "amount": amount})
    p["trades"] = p["trades"][-200:]
    p["equity"] = round(p["cash"] + sum(v["qty"] * v["mark"] for v in p["positions"].values()), 2)
    p["return_pct"] = round((p["equity"] / 1000 - 1) * 100, 2)
    p["stale_positions"] = [s for s, v in p["positions"].items() if v.get("stale")]
    history = list(p.get("history", []))
    history.append([now, p["equity"]])
    p["history"] = history[-720:]  # last 24 hours at the normal two-minute cadence
    p["peak_equity"] = max(p.get("peak_equity", max(1000.0, p["equity"])), p["equity"])
    p["max_drawdown_pct"] = max(p.get("max_drawdown_pct", 0), (1 - p["equity"] / p["peak_equity"]) * 100)
    return p


class Speculation:
    def __init__(self, engine):
        self.e = engine

    def status(self):
        state = self.e.db.get("speculation", {})
        return {"mode": "paper", "currency": self.e.settings["live"]["currency"],
                "rows": [], "paper": {}, **state,
                "stale": time.time() - state.get("ts", 0) > 600}

    async def scan(self):
        e = self.e
        broker = e.live or e._viewer
        if e.settings.simulate or not broker or not hasattr(broker, "tickers"):
            return  # requires real Fusion read access; never fake exchange data
        state = e.db.get("speculation", {})
        now = time.time()
        try:
            tickers = await broker.tickers()
            previous = e.db.get("speculation_previous", {})
            currency = e.settings["live"]["currency"]
            if state.get("currency", currency) != currency:
                # A new quote currency needs a new experiment and signal baseline.
                state, previous = {}, {}
            rows = rank(tickers, broker.pairs, previous, now)
            if not rows:
                raise ValueError("no valid Fusion tickers")
            for r in rows:
                r.update(eligible=False, note="Außerhalb der Top 10: Orderbuch nicht geprüft")
            semaphore = asyncio.Semaphore(3)
            async def qualify(r):
                async with semaphore:
                    try:
                        liquidity = await broker.liquidity(r["symbol"])
                        if not all(math.isfinite(liquidity[k]) and liquidity[k] >= 0
                                   for k in ("spread_pct", "depth_quote")):
                            raise ValueError("invalid liquidity")
                        r.update(liquidity)
                        reasons = []
                        if r["range_pct"] < 8: reasons.append("24h-Spanne unter 8%")
                        if r["spread_pct"] > e.settings["live"]["max_spread_pct"]: reasons.append("Spread zu hoch")
                        if r["depth_quote"] < 1000: reasons.append("Orderbuchtiefe unter 1.000")
                        if r["min_order"] > 100: reasons.append("Mindestorder über 100")
                        r["eligible"] = not reasons
                        r["note"] = " · ".join(reasons) if reasons else (
                            "Referenzscan fehlt" if r["momentum_pct"] is None else
                            "Paper-Signal" if r["breakout"] and r["momentum_pct"] >= 2 else "Wartet auf Ausbruch + Momentum")
                    except Exception:
                        r["note"] = "Orderbuch nicht verfügbar"
            await asyncio.gather(*(qualify(r) for r in rows[:10]))
            paper = paper_step(state.get("paper", {}), rows, now, e.settings["paper"]["fee_pct"],
                               e.settings["paper"]["slippage_pct"], e.kill_switch)
            e.db.set("speculation", {"ts": now, "currency": currency, "rows": rows[:30], "paper": paper, "error": None,
                                       "total_pairs": len(rows), "checked_pairs": min(10, len(rows)),
                                       "eligible_pairs": sum(r["eligible"] for r in rows),
                                       "fee_pct": e.settings["paper"]["fee_pct"],
                                       "slippage_pct": e.settings["paper"]["slippage_pct"]})
            e.db.set("speculation_previous", {r["symbol"]: {"ts": now, "price": r["price"], "high": r["high"]} for r in rows})
        except Exception as ex:
            e.db.set("speculation", {**state, "error": str(ex)[:160]})
