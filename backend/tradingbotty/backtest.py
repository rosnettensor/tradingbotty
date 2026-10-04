"""Backtester: replays a strategy over the price history the bot has recorded, with the same rules and fees.

Honest limits: it replays prices only. Hype and news have no history yet, so they count as neutral, and the
Professor's risk appetite is 1.0. History grows while the bot runs (up to 7 days are kept).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .brokers.paper import PaperBroker
from .decisions import blend, entry_blocker, exit_reason, expected_move, risk_check
import bisect

from .strategy import StrategyConfig, swing_signal, technical_signals

LOOKBACK_MIN = 240  # indicator warm-up before the tested window starts


@dataclass
class Dataset:
    minutes: list[int]                        # unix seconds, one per minute
    closes: dict[str, list[float | None]]     # forward-filled close per minute
    fresh: dict[str, list[bool]]              # True when there was a real trade recently (stocks: market open)
    kinds: dict[str, str]
    start_index: int                          # first minute that is traded (after warm-up)
    signals: dict[int, dict[str, dict]] = field(default_factory=dict)  # step index -> symbol -> signals
    step: int = 2
    hourly: dict[str, list[tuple[float, float]]] = field(default_factory=dict)

    @property
    def hours(self) -> float:
        return (len(self.minutes) - self.start_index) / 60


def build_dataset(series: dict[str, list[tuple[float, float]]], kinds: dict[str, str], hours: float,
                  step: int = 2, hourly: dict[str, list[tuple[float, float]]] | None = None) -> Dataset | None:
    """series: symbol -> [(ts, close)] of 1-minute candles."""
    crypto_ends = [s[-1][0] for sym, s in series.items() if s and kinds.get(sym) == "crypto"]
    ends = crypto_ends or [s[-1][0] for s in series.values() if s]
    if not ends:
        return None
    end = int(max(ends) // 60 * 60)
    first = min(s[0][0] for s in series.values() if s)
    start = int(max(first, end - (hours * 60 + LOOKBACK_MIN) * 60) // 60 * 60)
    minutes = list(range(start, end + 60, 60))
    if len(minutes) < 90:
        return None
    pos = {m: i for i, m in enumerate(minutes)}
    closes, fresh = {}, {}
    for sym, rows in series.items():
        col: list[float | None] = [None] * len(minutes)
        real = [False] * len(minutes)
        for ts, c in rows:
            i = pos.get(int(ts // 60 * 60))
            if i is not None and c:
                col[i] = c
                real[i] = True
        last, since = None, 10**9
        for i in range(len(minutes)):
            if col[i] is not None:
                last, since = col[i], 0
            else:
                col[i] = last
                since += 1
            real[i] = last is not None and (kinds.get(sym) == "crypto" or since <= 5)
        if any(v is not None for v in col):
            closes[sym], fresh[sym] = col, real
    warm = min(LOOKBACK_MIN, max(60, len(minutes) // 4))
    ds = Dataset(minutes, closes, fresh, {s: kinds.get(s, "crypto") for s in closes}, warm, step=step,
                 hourly={s: sorted(h) for s, h in (hourly or {}).items() if h})
    _precompute(ds)
    return ds


def _precompute(ds: Dataset) -> None:
    """Signals don't depend on the strategy, so compute them once and reuse them for every config tested."""
    def mood(sig):
        return 0.0 if not sig else 0.6 * sig["trend"] + 0.4 * sig["momentum"]

    hourly_ts = {s: [t for t, _ in h] for s, h in ds.hourly.items()}
    swing_cache: dict[tuple[str, int], float] = {}
    for i in range(ds.start_index, len(ds.minutes), ds.step):
        row = {}
        for sym, col in ds.closes.items():
            window = [c for c in col[max(0, i - 299):i + 1] if c is not None]
            if len(window) < 30:
                continue
            sig = technical_signals(window)
            back = col[max(0, i - 1440)] or window[0]
            sig["change_24h"] = (col[i] / back - 1) * 100 if back else 0.0
            sig["swing"] = 0.0
            if sym in ds.hourly:  # multi-day trend from hourly closes known at that time (no peeking ahead)
                k = bisect.bisect_right(hourly_ts[sym], ds.minutes[i] - 3600)
                if (sym, k) not in swing_cache:
                    h = ds.hourly[sym][max(0, k - 24 * 25):k]
                    swing_cache[(sym, k)] = swing_signal([c for _, c in h] + [col[i]])
                sig["swing"] = swing_cache[(sym, k)]
            row[sym] = sig
        crypto_mood = 0.7 * mood(row.get("BTC"))
        stock_moods = [mood(row.get(s)) for s in ("SPY", "QQQ") if s in row]
        stock_mood = sum(stock_moods) / len(stock_moods) if stock_moods else 0.0
        for sym, sig in row.items():
            sig["market"] = max(-1.0, min(1.0, crypto_mood if ds.kinds[sym] == "crypto" else stock_mood))
        ds.signals[i] = row


def run(ds: Dataset, cfg: StrategyConfig, settings: dict, start_cash: float = 100.0, keep_trades: int = 60) -> dict:
    r, p = settings["risk"], settings["paper"]
    broker = PaperBroker(start_cash, p["fee_pct"], p["slippage_pct"])
    fees = {"crypto": p["fee_pct"], "stock": p.get("stock_fee_pct", 0.05)}
    min_fees = {"crypto": 0.0, "stock": p.get("stock_min_fee_usd", 0.0)}
    weights = cfg.weights()
    trades, curve, buys = [], [], []
    wins = sells = 0
    fee_total = 0.0
    peak, max_dd = start_cash, 0.0
    day_start_eq, day_start_t = start_cash, ds.minutes[ds.start_index]
    tradable_kind = {"crypto": cfg.trade_crypto, "stock": cfg.trade_stocks}
    last_prices: dict[str, float] = {}

    for i in range(ds.start_index, len(ds.minutes), ds.step):
        now = ds.minutes[i]
        row = ds.signals.get(i, {})
        prices = {s: ds.closes[s][i] for s in ds.closes if ds.closes[s][i]}
        last_prices = prices
        if now - day_start_t >= 86400:
            day_start_t, day_start_eq = now, broker.equity(prices)
        if cfg.hold:
            if not trades:
                for sym in [s for s in ds.closes if ds.kinds[s] == "crypto" and prices.get(s)][:4]:
                    usd = min(broker.equity(prices) * 0.24, broker.cash - 0.01)
                    fill = broker.buy(sym, usd, prices[sym], fee_pct=fees["crypto"], now=now)
                    fee_total += fill.fee
                    trades.append({"ts": now, "symbol": sym, "side": "BUY", "price": fill.price,
                                   "notional": round(usd, 2), "pnl": None, "reason": "buy & hold benchmark"})
            eq = broker.equity(prices)
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100 if peak else 0)
            curve.append((now, round(eq, 3)))
            continue
        scores = {s: blend(weights, {**sig, "hype": 0.0, "news": 0.0}) for s, sig in row.items()
                  if tradable_kind.get(ds.kinds[s])}
        # exits
        for sym, pos in list(broker.positions.items()):
            if not ds.fresh[sym][i]:
                continue  # stock market closed: can't sell
            why = exit_reason(cfg, pos, prices[sym], scores.get(sym, 0.0), now, set())
            if why:
                fill = broker.sell(sym, pos.qty, prices[sym], fee_pct=fees[ds.kinds[sym]], now=now,
                                   min_fee=min_fees[ds.kinds[sym]])
                fee_total += fill.fee
                sells += 1
                wins += fill.pnl > 0
                trades.append({"ts": now, "symbol": sym, "side": "SELL", "price": fill.price,
                               "notional": round(fill.notional, 2), "pnl": round(fill.pnl, 3), "reason": why})
        # entries
        buys = [t for t in buys if now - t < 3600]
        for sym, score in sorted(scores.items(), key=lambda kv: -kv[1]):
            if score < cfg.entry_score:
                break
            if not ds.fresh[sym][i]:
                continue
            sig = row[sym]
            if entry_blocker(cfg, broker, sym, score, now, set(), len(buys), expected_move(sig["change_24h"], sig["vol"])):
                continue
            want = broker.equity(prices) * cfg.position_pct / 100
            usd, why = risk_check(r, broker, day_start_eq, False, sym, want, prices)
            if why:
                continue
            try:
                fill = broker.buy(sym, usd, prices[sym], fee_pct=fees[ds.kinds[sym]], now=now,
                                  min_fee=min_fees[ds.kinds[sym]])
            except ValueError:
                continue  # minimum fee bigger than the order
            fee_total += fill.fee
            buys.append(now)
            trades.append({"ts": now, "symbol": sym, "side": "BUY", "price": fill.price,
                           "notional": round(usd, 2), "pnl": None, "reason": f"score {score:+.2f}"})
        eq = broker.equity(prices)
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100 if peak else 0)
        curve.append((now, round(eq, 3)))

    final = broker.equity(last_prices)
    ret = (final / start_cash - 1) * 100
    every = max(1, len(curve) // 300)
    btc = ds.closes.get("BTC")
    hold = (btc[-1] / btc[ds.start_index] - 1) * 100 if btc and btc[ds.start_index] else None
    return {
        "return_pct": round(ret, 2), "max_drawdown_pct": round(max_dd, 2), "fitness": round(ret - 0.5 * max_dd, 2),
        "trades": len(trades), "sells": sells, "win_rate": round(wins / sells * 100, 1) if sells else None,
        "fees": round(fee_total, 2), "final_equity": round(final, 2), "open_positions": len(broker.positions),
        "hours": round(ds.hours, 1), "btc_hold_pct": None if hold is None else round(hold, 2),
        "curve": curve[::every] + curve[-1:], "trade_list": trades[-keep_trades:],
    }


def autotune(ds: Dataset, base: StrategyConfig, settings: dict, n: int = 40, strength: float = 0.4,
             seed: int | None = None) -> list[dict]:
    """Try n mutations of `base` on the same history and return them best first (with the base for comparison)."""
    rng = random.Random(seed)
    tried = [("current settings", base)]
    seen = {base.fingerprint()}
    while len(tried) < n + 1:
        cand = base.mutate(rng, strength * (0.5 + rng.random()))
        if cand.fingerprint() in seen:
            continue
        seen.add(cand.fingerprint())
        tried.append((f"candidate {len(tried)}", cand))
    out = []
    for label, cfg in tried:
        res = run(ds, cfg, settings, keep_trades=0)
        res.pop("curve")
        res.pop("trade_list")
        out.append({"label": label, "config": cfg.to_dict(), **res})
    # a strategy that never trades "wins" whenever fees eat everything, but it teaches nothing: rank those last
    out.sort(key=lambda x: (x["trades"] > 0, x["fitness"]), reverse=True)
    return out


def overfit_note(result: dict) -> str:
    """Plain-words caution for small samples."""
    if result["hours"] < 24:
        return f"Only {result['hours']:.0f}h of history so far: treat this as a hint, not proof."
    if result["trades"] < 10:
        return "Few trades in this window: luck can dominate."
    return ""

