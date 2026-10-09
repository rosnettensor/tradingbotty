"""Bauplan 2, phase 2: the separation of powers around the daily brain's money.

- Parliament proposes: the History Lab's strategies, the Think Tank's promoted ideas, and the new ones (mood switch,
  sizing by wildness, daily dip buyer). Proposing never moves money.
- Shadow depots: every candidate runs forward from the day it joined, on the real daily candles with fees, as if it
  had 100 of your currency. Same simulator as the History Lab, so test and shadow can't drift apart.
- The examiner (court) decides who may touch real money: robust in the History Lab, still positive at twice the fees,
  and at least SHADOW_DAYS in the shadow without doing much worse than holding Bitcoin over those days.
- The bank spreads the money by Thompson sampling: for every candidate it combines the last year of the test (counted
  a quarter) with its shadow days (counted fully) into a belief about its daily return, draws from those beliefs many
  times, and gives each candidate the share of draws it won. Cash competes too. No candidate gets more than MAX_SHARE
  once two are eligible, and only examined candidates get real money.
- In shadow mode (the default) the bank only shows what it would do and keeps its own shadow result. Live, the daily
  brain buys the bank's blend instead of one strategy, with all the usual checks.
"""
from __future__ import annotations

import math
import random
import time

from . import research

SHADOW_DAYS = 14
MAX_SHARE = 0.5
PRIOR_WEIGHT = 0.25    # one day of the History Lab counts a quarter of one day in the shadow
PRIOR_DAYS = 365
DRAWS = 4000
CANDIDATES_MAX = 7
BEHIND_BTC_MAX = 0.08  # the shadow may trail holding Bitcoin by at most 8 points over the last WINDOW days
WINDOW = 30
NEW = ["Mood switch: breakout 20/10 in a bull market, dip buying when sideways, cash otherwise",
       "Mood switch: breakout 20/10 in a bull market, cash otherwise",
       "Breakout 20/10 days, 3 slots, BTC filter 50d, sized by wildness",
       "Dip buyer daily: down 15%+ in 2 days, above its 100-day average, take +10% / stop -10%, BTC filter 50d"]
CASH = "Cash"
PROBE_SHARE = 0.15     # probation: the best candidate still in the shadow trades this share of the brain's money
PROBE_MAX = 0.25       # ...raised so each coin reaches Fusion's minimum, but never above this
PROBE_COIN = 30.0      # the smallest probation buy per coin, in your currency (Fusion's minimum is 25, some 30)
MODES = ("shadow", "probe", "live")


def _day_index(cd, ts: float) -> int:
    day = int(ts // 86400) * 86400
    for k in range(len(cd.days) - 1, -1, -1):
        if cd.days[k] <= day:
            return k
    return 0


def _rets(eq: list[float]) -> list[float]:
    return [b / a - 1 for a, b in zip(eq[:-1], eq[1:]) if a > 0]


def belief(prior: list[float], shadow: list[float]) -> tuple[float, float]:
    """Mean daily return and its uncertainty from the test's last year (down-weighted) and the shadow days."""
    w = [PRIOR_WEIGHT] * len(prior) + [1.0] * len(shadow)
    xs = prior + shadow
    n = sum(w)
    if n <= 0:
        return 0.0, 0.01
    m = sum(wi * x for wi, x in zip(w, xs)) / n
    var = sum(wi * (x - m) ** 2 for wi, x in zip(w, xs)) / max(n - 1, 1)
    return m, math.sqrt(var / n) if var > 0 else 0.001


def thompson(beliefs: dict[str, tuple[float, float]], seed: int) -> dict[str, float]:
    """Share of draws each candidate (and cash) wins."""
    rng = random.Random(seed)
    names = list(beliefs)
    wins = dict.fromkeys(names, 0)
    for _ in range(DRAWS):
        best = max(names, key=lambda k: rng.gauss(*beliefs[k]))
        wins[best] += 1
    return {k: wins[k] / DRAWS for k in names}


def split(prob: dict[str, float], eligible: set[str]) -> dict[str, float]:
    """Real money: only eligible candidates and cash share it, by their winning share; capped once two compete."""
    keep = {k: v for k, v in prob.items() if k in eligible or k == CASH}
    tot = sum(keep.values())
    if tot <= 0:
        return {CASH: 1.0}
    share = {k: v / tot for k, v in keep.items()}
    players = [k for k in share if k != CASH and share[k] > 0]
    if len(players) >= 2:
        for _ in range(5):  # cap, hand the overflow to the others that are under the cap
            over = sum(max(0.0, share[k] - MAX_SHARE) for k in players)
            if over <= 1e-9:
                break
            under = [k for k in players if share[k] < MAX_SHARE]
            for k in players:
                share[k] = min(share[k], MAX_SHARE)
            room = sum(MAX_SHARE - share[k] for k in under)
            for k in under:
                share[k] += over * (MAX_SHARE - share[k]) / room if room > 0 else 0
            if room <= 0:
                share[CASH] = share.get(CASH, 0.0) + over
    return {k: round(v, 4) for k, v in share.items() if v > 0.0005}


class Bank:
    """kv "bank": {"live": bool, "shadow": {name: {"since", "by"}}, "report": {...}, "history": [[day, value]]}."""

    def __init__(self, engine):
        self.e = engine

    def cfg(self) -> dict:
        b = self.e.db.get("bank") or {}
        b.setdefault("live", False)
        b.setdefault("mode", "live" if b["live"] else "shadow")
        b["live"] = b["mode"] == "live"
        b.setdefault("shadow", {})
        b.setdefault("history", [])
        return b

    def save(self, b: dict) -> None:
        self.e.db.set("bank", b)

    def mode(self) -> str:
        return self.cfg()["mode"]

    def candidates(self, res: dict | None) -> list[tuple[str, str]]:
        """(name, who proposed it): the brain's own strategy, the new proposals, then the best robust ones."""
        out: list[tuple[str, str]] = []
        live = self.e.brain().get("strategy")
        if live:
            out.append((live, "im Amt"))
        out += [(n, "neu (Bauplan 2)") for n in NEW]
        rows = (res or {}).get("rows") or []
        for r in sorted((r for r in rows if r.get("robust")), key=lambda r: -r["full"].get("sharpe", -9)):
            out.append((r["name"], "Think Tank" if r.get("group", "").startswith("Think") else "History Lab"))
        seen, uniq = set(), []
        for n, by in out:
            if n not in seen and research.by_name(n):
                seen.add(n)
                uniq.append((n, by))
        return uniq[:CANDIDATES_MAX]

    def examine(self, row: dict | None, sh: dict) -> tuple[str, str]:
        """The court: (stage, why). Stages: lab, failed, shadow, passed."""
        if not row:
            return "lab", "wartet auf den nächsten Lauf des History Labs"
        if not row.get("robust"):
            return "failed", (f"im History Lab nicht robust (schlägt BTC in {row.get('years_won', 0)} von "
                              f"{row.get('years_total', 0)} Jahren, Zufall {round((1 - row.get('skill_prob', 0)) * 100)}%)")
        f2 = (row.get("fees2x") or {}).get("return_pct")
        if f2 is not None and f2 <= 0:
            return "failed", "verliert mit doppelten Gebühren"
        days = sh.get("days", 0)
        if days < SHADOW_DAYS:
            return "shadow", f"im Schatten, Tag {days} von {SHADOW_DAYS}"
        if sh.get("ret", 0) < sh.get("btc", 0) - BEHIND_BTC_MAX:
            return "failed", (f"im Schatten (letzte {min(days, WINDOW)} Tage) {sh['ret'] * 100:+.1f}% gegen BTC "
                              f"{sh['btc'] * 100:+.1f}%: mehr als {BEHIND_BTC_MAX * 100:.0f} Punkte zurück")
        return "passed", f"robust im Labor und {days} Tage im Schatten bestanden"

    def refresh(self, cd, res: dict | None) -> dict:
        """Daily: shadow results, the court's verdicts, the bank's split. Pure arithmetic on the daily candles."""
        b = self.cfg()
        now = time.time()
        cands = self.candidates(res)
        rows = {r["name"]: r for r in (res or {}).get("rows") or []}
        for n, by in cands:
            b["shadow"].setdefault(n, {"since": now, "by": by})
            b["shadow"][n]["by"] = by if by != "History Lab" else b["shadow"][n].get("by", by)
        last = len(cd.days) - 1
        btc = cd.c.get("BTC") or []
        report, beliefs, shadow_rets = [], {}, {}
        live_name = self.e.brain().get("strategy")
        for n, _ in cands:
            sh = b["shadow"][n]
            k = _day_index(cd, sh["since"])
            fwd = research.simulate(cd, research.by_name(n), k) if k < last else None
            eq = fwd.equity if fwd else [1.0]
            days = len(eq) - 1
            b_ret = (btc[last] / btc[k] - 1) if btc and k < last and btc[k] and btc[last] else 0.0
            peak, dd = 1.0, 0.0
            for x in eq:
                peak = max(peak, x)
                dd = min(dd, x / peak - 1)
            # the court looks at the last WINDOW days (or all of them, if fewer), so an old bad month fades out
            w0 = max(k, last - WINDOW)
            w_ret = eq[-1] / eq[w0 - k] - 1 if eq[w0 - k] else 0.0
            w_btc = (btc[last] / btc[w0] - 1) if btc and btc[w0] and btc[last] else 0.0
            s = {"days": days, "ret": w_ret, "btc": w_btc, "dd": dd}
            prior = _rets(research.simulate(cd, research.by_name(n), max(0, last - PRIOR_DAYS)).equity)
            shadow_rets[n] = _rets(eq)
            beliefs[n] = belief(prior, shadow_rets[n])
            stage, why = self.examine(rows.get(n), s)
            if n == live_name and stage in ("lab", "shadow"):
                stage, why = "incumbent", "verwaltet das Geld schon: bleibt, bis jemand Besseres bestanden hat"
            r = rows.get(n) or {}
            report.append({"name": n, "by": sh.get("by"), "since": sh["since"], "stage": stage, "why": why,
                           "shadow_pct": round(s["ret"] * 100, 2), "btc_pct": round(b_ret * 100, 2),
                           "shadow_dd_pct": round(dd * 100, 1), "days": days,
                           "test": {k2: r.get("full", {}).get(k2) for k2 in ("cagr_pct", "max_dd_pct", "sharpe")},
                           "robust": r.get("robust"), "years": f"{r.get('years_won', '?')}/{r.get('years_total', '?')}",
                           "belief_pct_yr": round(((1 + beliefs[n][0]) ** 365 - 1) * 100, 1),
                           "curve": [round(x, 4) for x in eq[-60:]]})
        beliefs[CASH] = (0.0, 1e-5)
        prob = thompson(beliefs, seed=int(cd.days[last] // 86400))
        eligible = {r["name"] for r in report if r["stage"] in ("passed", "incumbent")}
        live_split = split(prob, eligible)
        shadow_split = split(prob, {r["name"] for r in report})  # what it would do if everyone were examined
        # probation: the incumbent keeps its money, the best candidate the examiner still watches gets a small share
        waiting = [r["name"] for r in report if r["stage"] == "shadow"]
        probe = max(waiting, key=lambda n: prob.get(n, 0), default=None)
        inc = [r["name"] for r in report if r["stage"] == "incumbent"] or [r["name"] for r in report if r["stage"] == "passed"]
        probe_split = {CASH: 1.0}
        if inc:
            probe_split = {inc[0]: 1.0 - (PROBE_SHARE if probe else 0.0)}
            if probe:
                probe_split[probe] = PROBE_SHARE
        for r in report:
            r["probe_pct"] = round(probe_split.get(r["name"], 0) * 100, 1)
            r["win_pct"] = round(prob.get(r["name"], 0) * 100, 1)
            r["share_pct"] = round(live_split.get(r["name"], 0) * 100, 1)
            r["shadow_share_pct"] = round(shadow_split.get(r["name"], 0) * 100, 1)
        # the bank's own shadow: yesterday's shadow split applied to today's returns of every candidate
        day = cd.days[last]
        hist = [h for h in b["history"] if h[0] < day]
        prev = b.get("report", {}).get("shadow_split") or {}
        if hist and prev:
            r_day = sum(w * (shadow_rets.get(n) or [0.0])[-1] for n, w in prev.items() if n != CASH)
            hist.append([day, round(hist[-1][1] * (1 + r_day), 6)])
        elif not hist:
            hist.append([day, 1.0])
        b["history"] = hist[-400:]
        b["report"] = {"ts": now, "day": day, "rows": report, "split": live_split, "shadow_split": shadow_split,
                       "probe_split": probe_split, "probe": probe,
                       "cash_win_pct": round(prob.get(CASH, 0) * 100, 1), "eligible": sorted(eligible)}
        self.save(b)
        return b["report"]

    def target(self, cd, budget: float | None = None) -> tuple[dict[str, float], list[str], dict]:
        """The blend of the candidates' holdings by the bank's split (live) or the probation split (probe).
        Returns (weights, steps, owners): owners maps each coin to the strategy with the biggest stake in it."""
        rep = self.cfg().get("report") or {}
        probing = self.mode() == "probe"
        split_ = (rep.get("probe_split") if probing else rep.get("split")) or {}
        probe = rep.get("probe") if probing else None
        weights: dict[str, float] = {}
        stake: dict[str, tuple[float, object]] = {}
        steps = []
        for name, share in sorted(split_.items(), key=lambda kv: -kv[1]):
            if name == CASH:
                steps.append(f"Bank: {share * 100:.0f}% bleibt in Cash")
                continue
            strat = research.by_name(name)
            if not strat:
                continue
            tgt = research.current_target(cd, strat)
            if name == probe and tgt and budget:  # each probation coin at least Fusion's minimum, all of them capped
                floor = PROBE_COIN / budget
                tgt = {k: max(share * w, floor) / share for k, w in tgt.items()}
                total = sum(share * w for w in tgt.values())
                if total > PROBE_MAX:
                    tgt = {k: w * PROBE_MAX / total for k, w in tgt.items()}
            for sym, w in tgt.items():
                weights[sym] = weights.get(sym, 0.0) + share * w
                if share * w > stake.get(sym, (0.0, None))[0]:
                    stake[sym] = (share * w, research.by_name(name))
            steps.append(f"Bank{' (Probe)' if name == probe else ''}: {share * 100:.0f}% nach \"{name}\" → "
                         + (", ".join(sorted(tgt)) or "Cash"))
        tot = sum(weights.values())
        if tot > 1:
            weights = {k: v / tot for k, v in weights.items()}
        return ({k: round(v, 4) for k, v in weights.items() if v > 0.01}, steps,
                {k: st for k, (_, st) in stake.items()})

    def set_live(self, on: bool) -> dict:
        return self.set_mode("live" if on else "shadow")

    def set_mode(self, mode: str) -> dict:
        if mode not in MODES:
            raise ValueError(f"unknown bank mode {mode}")
        b = self.cfg()
        b["mode"], b["live"] = mode, mode == "live"
        self.save(b)
        on = mode != "shadow"
        br = self.e.brain()
        br.pop("day", None)  # the brain re-decides at its next check with (or without) the bank's blend
        self.e.db.set("brain", br)
        rep = b.get("report") or {}
        split_ = (rep.get("probe_split") if mode == "probe" else rep.get("split")) or {}
        self.e._log("Bank", "live" if on else "info",
                    (f"Bank {'PROBATION' if mode == 'probe' else 'LIVE'}: the daily brain now buys this blend: "
                     + ", ".join(f"{k} {v * 100:.0f}%" for k, v in split_.items()))
                    if on else "Bank back in shadow mode: the daily brain follows its one strategy again.")
        return self.info()

    def info(self) -> dict:
        b = self.cfg()
        h = b.get("history") or []
        return {"live": b["live"], "mode": b["mode"], "report": b.get("report"), "history": h[-120:],
                "probe_share": PROBE_SHARE,
                "shadow_pct": round((h[-1][1] - 1) * 100, 2) if h else None,
                "since": h[0][0] if h else None, "shadow_days": SHADOW_DAYS, "max_share": MAX_SHARE}
