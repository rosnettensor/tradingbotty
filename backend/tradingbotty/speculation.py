"""Fusion volatility watchlist and an isolated forward paper experiment.

No execution adapter is passed to the paper portfolio. Range is not realized
volatility; momentum is between observed scans, not a claimed 24-hour return.
"""
from __future__ import annotations

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
    fee, slip = fee_pct / 100, slippage_pct / 100
    by = {r["symbol"]: r for r in rows}
    closed = set()
    for s, pos in list(p["positions"].items()):
        r = by.get(s)
        if not r:
            continue  # missing prices don't create a fabricated fill
        pos["mark"] = r["price"]
        ratio = r["price"] / pos["entry"] - 1
        if ratio > -0.08 and ratio < 0.20 and now - pos["ts"] < 86400:
            continue
        received = pos["qty"] * r["price"] * (1 - slip) * (1 - fee)
        pnl = received - pos["cost"]
        p["cash"] += received
        p["realized"] += pnl
        p["trades"].append({"ts": now, "symbol": s, "side": "SELL", "pnl": round(pnl, 2)})
        del p["positions"][s]
        closed.add(s)
    day = now // 86400 * 86400
    realized_loss = sum(max(0.0, -t.get("pnl", 0)) for t in p["trades"] if t["ts"] >= day)
    unrealized_loss = sum(max(0.0, v["cost"] - v["qty"] * v["mark"]) for v in p["positions"].values())
    paused = paused or realized_loss + unrealized_loss >= 200
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
        p["positions"][s] = {"qty": amount / entry, "entry": entry, "cost": cost, "ts": now, "mark": r["price"]}
        p["trades"].append({"ts": now, "symbol": s, "side": "BUY", "amount": amount})
    p["trades"] = p["trades"][-200:]
    p["equity"] = round(p["cash"] + sum(v["qty"] * v["mark"] for v in p["positions"].values()), 2)
    p["return_pct"] = round((p["equity"] / 1000 - 1) * 100, 2)
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
            for r in rows[:10]:
                r["eligible"] = False
                try:
                    liquidity = await broker.liquidity(r["symbol"])
                    if not all(math.isfinite(v) and v >= 0 for v in liquidity.values()):
                        raise ValueError("invalid liquidity")
                    r.update(liquidity)
                    r["eligible"] = (r["range_pct"] >= 8 and r["spread_pct"] <= e.settings["live"]["max_spread_pct"]
                                     and r["depth_quote"] >= 1000 and r["min_order"] <= 100)
                except Exception:
                    r["note"] = "liquidity unavailable; excluded"
            paper = paper_step(state.get("paper", {}), rows, now, e.settings["paper"]["fee_pct"],
                               e.settings["paper"]["slippage_pct"], e.kill_switch)
            e.db.set("speculation", {"ts": now, "currency": currency, "rows": rows[:30], "paper": paper, "error": None})
            e.db.set("speculation_previous", {r["symbol"]: {"ts": now, "price": r["price"], "high": r["high"]} for r in rows})
        except Exception as ex:
            e.db.set("speculation", {**state, "error": str(ex)[:160]})
