"""The fast lab: speculative rules on 4-hour candles, a 24-hour signal test and Bitcoin links. Research only."""
import asyncio
import math
import random
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty import fastlab, patterns, research  # noqa: E402

from fakes import FakeFusion, _engine  # noqa: E402

BAR = fastlab.BAR


def _cd(coins=("BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LINK", "AVAX", "NEAR"), days=400):
    return research.Candles.from_rows(fastlab.synthetic_4h(list(coins), days=days))


def test_fast_strategies_run_on_4h_candles_with_honest_costs():
    names = [s.name for s in fastlab.fast_strategies()]
    assert len(names) == len(set(names)) and all(fastlab.by_name(n) for n in names)
    cd = _cd()
    assert cd.ppy == 2190 and cd.v["BTC"][10] > 0
    for strat in fastlab.fast_strategies()[1:6] + [fastlab.by_name(n) for n in names if "volume" in n]:
        r = research.simulate(cd, strat, 300, fastlab.COST)
        assert r.ppy == 2190 and r.equity[-1] > 0
        assert r.trades == 0 or r.fees > 0
    cheap = research.simulate(cd, fastlab.fast_strategies()[1], 300, 0.001).equity[-1]
    dear = research.simulate(cd, fastlab.fast_strategies()[1], 300, 0.01).equity[-1]
    assert dear < cheap


def test_volume_check_only_buys_on_heavy_trading():
    cd = _cd()
    strat = fastlab.FastBreakout(18, 9, 2, vol_x=50.0)          # never that much volume: never buys
    assert research.simulate(cd, strat, 300, fastlab.COST).trades == 0


def test_signal_test_finds_bitcoin_leading_the_altcoins_and_calls_noise_chance():
    rng = random.Random(3)
    n, t0 = 6 * 900, time.time() // BAR * BAR - 6 * 900 * BAR
    btc = [rng.gauss(0, 0.012) for _ in range(n)]
    rows = {}
    for c in ("BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LINK", "AVAX", "DOT"):
        p, out = 100.0, []
        for i in range(n):
            # altcoins follow Bitcoin's last 4 hours over the next bars (the planted lead-lag)
            r = btc[i] if c == "BTC" else 0.5 * sum(btc[max(0, i - 3):i]) + rng.gauss(0, 0.01)
            o = p
            p *= math.exp(r)
            out.append((t0 + i * BAR, o, max(o, p) * 1.002, min(o, p) * 0.998, p, 1e6 * rng.uniform(0.5, 1.5)))
        rows[c] = out
    cd = research.Candles.from_rows(rows)
    res = patterns.analyse(cd, 300, fastlab.fast_candidates(), fastlab.BARS_PER_DAY)
    assert res["horizon_days"] == 1.0
    by = {(r["name"], r["kind"]): r for r in res["rows"]}
    lead = by[("Bitcoin's last 4h → altcoins next 24h", "market")]
    assert lead["verdict"] == "pattern" and lead["corr"] > 0 and "better day" in lead["direction"]
    assert all(r["verdict"] in ("chance", "no data") for (name, _), r in by.items() if "Random" in name)


def test_bitcoin_links_track_each_coin_over_time():
    c = fastlab.correlations(_cd())
    assert c["coins"] and c["series"]
    row = c["coins"][0]
    assert -1 <= row["corr_30d"] <= 1 and row["coin"] != "BTC"
    assert [x["corr_30d"] for x in c["coins"]] == sorted(x["corr_30d"] for x in c["coins"])


def test_coins_come_from_binance_volume_and_must_be_on_fusion(tmp_path):
    now = time.time() // BAR * BAR

    def handler(req: httpx.Request):
        if "ticker" in str(req.url):
            return httpx.Response(200, json=[
                {"symbol": "USDCUSDT", "quoteVolume": "9e12"}, {"symbol": "ETHUSDT", "quoteVolume": "5e9"},
                {"symbol": "BTCUSDT", "quoteVolume": "9e9"}, {"symbol": "NOTONFUSIONUSDT", "quoteVolume": "8e9"},
                {"symbol": "SOLUSDT", "quoteVolume": "2e9"}, {"symbol": "ETHBTC", "quoteVolume": "1e12"}])
        start = int(req.url.params["startTime"]) // 1000
        k = [[(t) * 1000, "1", "2", "0.5", "1.5", "10", 0, "15"] for t in range(int(start), int(now) + BAR, BAR)][:1000]
        return httpx.Response(200, json=k)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    coins = asyncio.run(fastlab.pick_coins(client, {"BTC", "ETH", "SOL", "USDC"}))
    assert coins == ["BTC", "ETH", "SOL"]

    from tradingbotty.db import DB
    db = DB(tmp_path / "f.db")
    db.set("h4:ETH", [[now - 5000 * BAR, 1, 1, 1, 1]])            # old rows without volume are dropped
    out = asyncio.run(fastlab.update_4h(client, db, ["ETH"]))
    rows = out["ETH"]
    assert len(rows[0]) == 6 and rows[0][5] == 15.0 and rows[-1][0] < now   # the forming bar is left out
    assert len(rows) >= 400 and db.get("h4:ETH")


def test_engine_runs_the_fast_lab_and_the_researcher_reports_it(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    res = asyncio.run(e.run_fastlab())
    assert res["bar_hours"] == 4 and res["cost_per_side_pct"] == 0.5 and res["simulated"]
    assert res["patterns"]["rows"] and res["correlation"]["coins"] and res["coin_luck"]
    assert e.db.get("fastlab")["rows"]
    from tradingbotty.agents.crew import Researcher
    assert "fast strategies robust" in Researcher._fast(res)
