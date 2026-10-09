"""Bauplan 2, phase 2: shadow depots, the examiner, and the bank's split of the brain's money."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty import bank, research  # noqa: E402

COINS = ["BTC", "ETH", "SOL", "ADA", "XRP", "DOGE", "NEAR", "UNI"]


def run(c):
    return asyncio.run(c)


def _cd(days=900):
    return research.Candles.from_rows(research.synthetic_rows(COINS, days=days))


def test_new_proposals_are_in_the_history_lab():
    for n in bank.NEW:
        assert research.by_name(n), n


def test_daily_dip_buyer_buys_a_sharp_fall_in_an_uptrend_and_sells_the_bounce():
    rows = research.synthetic_rows(["BTC", "SOL"], days=400)
    sol = rows["SOL"]
    up = [(d, p, p * 1.01, p * 0.99, p) for d, p in ((d, 100 * 1.01 ** j) for j, (d, *_ ) in enumerate(sol))]
    k = 300
    base = up[k - 2][4]
    up[k] = (up[k][0], base, base, base * 0.8, base * 0.82)             # -18% in two days
    up[k + 1] = (up[k + 1][0], base * 0.82, base * 0.95, base * 0.82, base * 0.92)   # bounce: +12%
    rows["SOL"] = up
    rows["BTC"] = [(d, 100 + j, 101 + j, 99 + j, 100 + j) for j, (d, *_ ) in enumerate(rows["BTC"])]
    cd = research.Candles.from_rows(rows)
    st = research.DayDip()
    assert st.target(cd, k, {}) == {"SOL": 1 / 3}
    assert st.target(cd, k + 1, {"SOL": 1 / 3}) == {}                  # +10% take profit


def test_split_caps_each_strategy_once_two_compete_and_only_pays_the_examined():
    prob = {"A": 0.8, "B": 0.15, "C": 0.04, bank.CASH: 0.01}
    s = bank.split(prob, {"A", "B"})
    assert s["A"] == 0.5 and "C" not in s and abs(sum(s.values()) - 1) < 1e-6
    assert bank.split(prob, {"A"})["A"] > 0.98                           # alone: no cap
    assert bank.split(prob, set()) == {bank.CASH: 1.0}


def test_thompson_prefers_the_better_belief_but_keeps_some_doubt():
    p = bank.thompson({"good": (0.002, 0.001), "bad": (-0.001, 0.001), bank.CASH: (0.0, 1e-5)}, seed=1)
    assert p["good"] > 0.9 and p["bad"] < 0.02


def test_examiner_needs_the_lab_double_fees_and_the_shadow():
    b = bank.Bank(None)
    row = {"robust": True, "fees2x": {"return_pct": 40}, "years_won": 7, "years_total": 9, "skill_prob": 0.9}
    assert b.examine(None, {})[0] == "lab"
    assert b.examine({**row, "robust": False}, {})[0] == "failed"
    assert b.examine({**row, "fees2x": {"return_pct": -3}}, {})[0] == "failed"
    assert b.examine(row, {"days": 5})[0] == "shadow"
    assert b.examine(row, {"days": 20, "ret": -0.15, "btc": 0.0})[0] == "failed"
    assert b.examine(row, {"days": 20, "ret": -0.03, "btc": 0.0})[0] == "passed"


def test_bank_round_and_live_blend(tmp_path, monkeypatch):
    from fakes import FakeFusion, _engine
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    cd = _cd()
    strat = "Breakout 20/10 days, 3 slots, BTC filter 50d"
    e.db.set("brain", {"on": True, "strategy": strat})
    names = [strat, *bank.NEW]
    res = {"rows": [{"name": n, "robust": True, "fees2x": {"return_pct": 10}, "full": {"sharpe": 1.0},
                     "years_won": 6, "years_total": 9, "skill_prob": 0.9, "group": "x"} for n in names]}
    rep = e.bank.refresh(cd, res)
    stages = {r["name"]: r["stage"] for r in rep["rows"]}
    assert stages[strat] == "incumbent" and stages[bank.NEW[0]] == "shadow"
    assert set(rep["split"]) <= {strat, bank.CASH}                       # only the incumbent may hold real money
    # two weeks later everyone has a shadow: pretend they joined 20 days ago
    b = e.bank.cfg()
    for n in b["shadow"]:
        b["shadow"][n]["since"] = cd.days[-21]
    e.bank.save(b)
    rep = e.bank.refresh(cd, res)
    assert any(r["stage"] in ("passed", "failed") and r["days"] >= 14 for r in rep["rows"])
    assert all(v <= bank.MAX_SHARE + 1e-6 for k, v in rep["split"].items() if k != bank.CASH) or len(rep["split"]) <= 2
    w, steps, owners = e.bank.target(cd)
    assert sum(w.values()) <= 1.0001 and all(s.startswith("Bank:") for s in steps)
    assert set(owners) == set(w) or set(w) <= set(owners)
    info = e.bank.set_live(True)
    assert info["live"] and "day" not in e.brain()


def test_probation_gives_the_best_shadow_candidate_a_small_real_share(tmp_path, monkeypatch):
    from fakes import FakeFusion, _engine
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    cd = _cd()
    strat = "Breakout 20/10 days, 3 slots, BTC filter 50d"
    e.db.set("brain", {"on": True, "strategy": strat})
    res = {"rows": [{"name": n, "robust": True, "fees2x": {"return_pct": 10}, "full": {"sharpe": 1.0},
                     "years_won": 6, "years_total": 9, "skill_prob": 0.9, "group": "x"} for n in [strat, *bank.NEW]]}
    rep = e.bank.refresh(cd, res)
    assert rep["probe"] in bank.NEW and rep["probe_split"][strat] == 1 - bank.PROBE_SHARE
    assert e.bank.mode() == "shadow"
    info = e.bank.set_mode("probe")
    assert info["mode"] == "probe" and not info["live"]
    w, steps, owners = e.bank.target(cd, budget=300.0)
    assert sum(w.values()) <= 1.0001
    probe_coins = research.current_target(cd, research.by_name(rep["probe"]))
    for s in probe_coins:                              # each probation coin reaches Fusion's minimum
        assert w.get(s, 0) * 300 >= bank.PROBE_COIN - 0.01 or w.get(s, 0) >= bank.PROBE_MAX / len(probe_coins) - 1e-3
    assert any("(Probe)" in s for s in steps) or not probe_coins
    import pytest
    with pytest.raises(ValueError):
        e.bank.set_mode("yolo")


def test_probation_never_sells_everything_when_the_court_fails_the_brains_strategy(tmp_path, monkeypatch):
    from fakes import FakeFusion, _engine
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    cd = _cd()
    strat = "Breakout 20/10 days, 3 slots, BTC filter 50d"
    e.db.set("brain", {"on": True, "strategy": strat})
    rows = [{"name": n, "robust": n != strat, "fees2x": {"return_pct": 10}, "full": {"sharpe": 1.0},
             "years_won": 6, "years_total": 9, "skill_prob": 0.9, "group": "x"} for n in [strat, *bank.NEW]]
    rep = e.bank.refresh(cd, {"rows": rows})
    assert next(r for r in rep["rows"] if r["name"] == strat)["stage"] == "failed"
    assert rep["probe_split"].get(strat) == 1 - bank.PROBE_SHARE and bank.CASH not in rep["probe_split"]
