"""The Pulse tab's numbers: every coin's moves and lines, the correlation map and the market's breadth."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tradingbotty import market, research  # noqa: E402


def test_snapshot_has_every_coin_a_symmetric_map_and_breadth_in_percent():
    cd = research.Candles.from_rows(research.synthetic_rows(["BTC", "ETH", "SOL", "ADA", "XRP"], days=500))
    s = market.snapshot(cd, {"SOL": cd.c["SOL"][-1] * 1.1})
    rows = {r["symbol"]: r for r in s["coins"]}
    assert set(rows) == {"BTC", "ETH", "SOL", "ADA", "XRP"} and rows["SOL"]["live"] and not rows["ETH"]["live"]
    assert abs(rows["SOL"]["moves"]["1d"] - (1.1 * cd.c["SOL"][-1] / cd.c["SOL"][-2] - 1) * 100) < 0.01
    assert rows["BTC"]["btc_corr"] == 1.0 and rows["ETH"]["spark"][0] == 1.0
    m = s["matrix"]
    assert m["symbols"][0] == "BTC" and len(m["values"]) == 5
    assert all(m["values"][i][j] == m["values"][j][i] for i in range(5) for j in range(5))
    assert s["breadth"] and all(0 <= b["above50"] <= 100 and 0 <= b["highs"] <= 100 for b in s["breadth"])
    assert s["breadth"][-1]["day"] == cd.days[-1] and s["summary"]["coins"] == 5
