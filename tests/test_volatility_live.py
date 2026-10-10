"""Real execution path with a fake exchange only; no credentials or network orders."""
import asyncio
import time

import pytest

from fakes import FakeFusion, _engine
from tradingbotty.brokers.fusion import FusionError, FusionPendingOrder
from tradingbotty.config import load_settings
from tradingbotty.engine import Engine
from tradingbotty.operations import snapshot
from tradingbotty.volatility import CONFIRMATION


class PilotFusion(FakeFusion):
    def __init__(self):
        super().__init__()
        self.price = 10.0
        self.high = 10.0
        self.depth = 10000
        self.pairs = {s: {"pair": s + "-CHF", "minOrderAmount": 25} for s in ("SOL", "BTC")}

    async def tickers(self):
        return [{"pair": "SOL-CHF", "price": self.price, "high": self.high, "low": 8, "volume": 100}]

    async def liquidity(self, sym):
        return {"spread_pct": self.spread, "depth_quote": self.depth}

    async def prices(self):
        return {s: self.price for s in self.pairs}


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, PilotFusion())
    e.settings.simulate = False
    e.wallet = {"total": 100, "currency": "CHF", "ts": time.time()}
    e.set_controls({"live.max_order": 100, "live.max_invest": 100})
    return e


def ready(e):
    e.volatility.configure(True, CONFIRMATION)
    c = e.volatility.cfg(); c["activated_at"] = time.time() - 240; e.db.set("volatility_live", c)
    e.db.set("speculation", {"ts": time.time() - 1, "currency": "CHF", "rows": [
        {"symbol": s, "price": 10, "eligible": True, "breakout": True, "momentum_pct": 3,
         "reference_ts": time.time() - 120, "reference_price": 9.5, "reference_high": 9.8}
        for s in ("SOL", "BTC")]})


def buy(e, symbol="SOL", want=999):
    return asyncio.run(e.execution.buy(symbol, want, "pilot test", book="volatility"))


def test_no_auto_activation_or_paper_migration_and_explicit_ack(pilot):
    e = pilot
    e.db.set("speculation", {"paper": {"positions": {"SOL": {"qty": 1000}}}})
    assert not e.volatility.cfg()["enabled"]
    with pytest.raises(ValueError, match="VOLATILITY LIVE"):
        e.volatility.configure(True)
    with pytest.raises(ValueError, match="freigegeben"):
        buy(e)
    e.volatility.configure(True, CONFIRMATION)
    assert not e.db.get("volatility_qty") and not e.live.buys
    assert "Scans" in e.volatility.reason() or "Scan" in e.volatility.reason()
    e.volatility.configure(False)
    assert not e.volatility.cfg()["enabled"]


def test_scan_needs_two_post_activation_observations(pilot, monkeypatch):
    e = pilot
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    e.volatility.configure(True, CONFIRMATION)
    now[0] = 1120
    asyncio.run(e.speculation.scan())
    assert not e.live.buys
    now[0] = 1240
    e.live.price = e.live.high = 11
    asyncio.run(e.speculation.scan())
    assert e.live.buys == [("SOL", 30)]
    assert e.db.get("volatility_qty") == {"SOL": 3}
    assert not e.db.get("live_qty") and not e.db.get("fast_qty")
    assert e.recent_trades()[0]["book"] == "volatility"
    assert e.speculation.status()["live"]["trades"][0]["notional"] == 30


def test_gateway_caps_even_manual_calls_and_blocks_overlap(pilot):
    e = pilot; ready(e)
    amount, _ = buy(e)
    assert amount == 30
    with pytest.raises(ValueError, match="Platz"):
        buy(e, "BTC")
    with pytest.raises(ValueError, match="other strategy"):
        asyncio.run(e.execution.buy("SOL", 30, "manual", book="brain"))
    assert len(e.live.buys) == 1
    q, c = e.portfolio.holdings()
    assert q == {"SOL": 3} and c == {"SOL": 30}


@pytest.mark.parametrize("change,match", [
    (lambda e: e.live.pairs["SOL"].update(minOrderAmount=31), "minimum"),
    (lambda e: e.live.pairs["SOL"].update(leverage=2), "leverage"),
    (lambda e: setattr(e.live, "depth", 999), "Orderbuch"),
    (lambda e: setattr(e.live, "price", 9.7), "nicht mehr gültig"),
    (lambda e: e.db.set("guard", {"SOL": {"until": time.time() + 1000}}), "Guardian"),
    (lambda e: e.db.set("speculation", {"ts": time.time()-181, "currency": "CHF"}), "Scan"),
])
def test_market_and_signal_fail_closed(pilot, change, match):
    ready(pilot); change(pilot)
    with pytest.raises(ValueError, match=match):
        buy(pilot)
    assert not pilot.live.buys


def test_pause_during_network_check_and_changed_reservation_invalidate_order(pilot):
    e = pilot; ready(e)
    async def liquidity(sym):
        e.volatility.configure(False)
        return {"depth_quote": 10000, "spread_pct": .1}
    e.live.liquidity = liquidity
    with pytest.raises(ValueError, match="freigegeben"):
        buy(e)
    assert not e.live.buys
    # Daily also must recalculate when a pilot allocation changes during its checks.
    async def spread(sym):
        e.volatility.configure(True, CONFIRMATION)
        return .1
    e.live.spread_pct = spread
    with pytest.raises(ValueError, match="settings changed"):
        asyncio.run(e.execution.buy("BTC", 80, "daily"))
    assert not e.live.buys


def test_reservations_and_wallet_keep_pilot_owned_after_pause(pilot):
    e = pilot; ready(e)
    assert e.portfolio.allocation()["daily"] == 49
    amount, _ = asyncio.run(e.execution.buy("BTC", 80, "manual", book="brain"))
    assert amount == 49.75  # remaining 50 cash less existing fee cushion
    buy(e)
    asyncio.run(e._poll_wallet())
    assert e.wallet["volatility_coins"][0]["symbol"] == "SOL"
    assert "SOL" not in e.wallet["yours"]
    assert e.wallet["total"] == 100
    assert (asyncio.run(e.execution.spare_coins(e.live.bal))) == {}
    e.volatility.configure(False)
    assert e.portfolio.allocation()["volatility_committed"] == 30
    assert e.portfolio.allocation()["daily"] == 68.6
    assert e.portfolio.volatility_cash_reserve() == 0


def test_pause_stale_scan_and_restart_do_not_disable_exits(pilot):
    e = pilot; ready(e); buy(e)
    e.volatility.configure(False)
    e.db.set("speculation", {"error": "offline"})
    restarted = Engine(load_settings()); restarted.live = e.live; restarted.settings.simulate = False
    assert not restarted.volatility.cfg()["enabled"]
    restarted.live.price = 9
    asyncio.run(restarted.volatility.watch())
    assert not restarted.db.get("volatility_qty")
    assert len(restarted.live.sells) == 1
    assert restarted.recent_trades()[0]["side"] == "SELL"


def test_partial_fill_and_exit_keep_canonical_cost_and_fee_basis(pilot):
    e = pilot; ready(e)
    async def partial(sym, amount):
        e.live.bal[sym] = 1.5
        return {"execution": {"quantity": 1.5, "notional": 15, "fee": .15}}
    e.live.buy = partial
    assert buy(e) == (15, 1.5)
    asyncio.run(e.execution.sell("SOL", "half", book="volatility", fraction=.5))
    assert e.db.get("volatility_qty") == {"SOL": .75}
    assert e.db.get("volatility_cost") == {"SOL": 7.5}
    assert e.portfolio.fee_basis("volatility", "SOL", .75) == pytest.approx(.075)
    assert e._open_buys("volatility", "SOL")


def test_two_submissions_per_day_survive_toggles_and_restart(pilot):
    e = pilot; ready(e)
    async def rejected(*args):
        raise FusionError("definite rejection", http_status=422)
    e.live.buy = rejected
    for i in range(2):
        with pytest.raises(FusionError): buy(e)
        # Simulate the next scan; never retry a failed scan.
        with pytest.raises(ValueError): buy(e)
        e.db.execute("UPDATE orders SET created_at=created_at-120 WHERE book='volatility'")
    e.volatility.configure(False); e.volatility.configure(True, CONFIRMATION)
    assert e.volatility.stats()["orders_today"] == 2
    assert "Kaufaufträge" in e.volatility.reason()
    restarted = Engine(load_settings())
    assert restarted.volatility.stats()["orders_today"] == 2


def test_loss_stop_persists_and_profits_never_scale_budget(pilot):
    e = pilot; ready(e)
    for pnl in (100, -10):
        e.db.execute("INSERT INTO trades(ts,variant_id,mode,symbol,side,qty,price,notional,fee,pnl) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (time.time(), "volatility", "live", "SOL", "SELL", 1, 10, 10, 0, pnl))
    assert e.volatility.budget() == 50
    with pytest.raises(ValueError, match="Verlustgrenze"): buy(e)
    e.volatility.configure(False)
    with pytest.raises(ValueError, match="Verlustgrenze"): e.volatility.configure(True, CONFIRMATION)
    assert not e.live.buys


def test_uncertain_pilot_order_uses_global_freeze_and_no_replay(pilot):
    e = pilot; ready(e)
    async def uncertain(*args): raise FusionPendingOrder({"id": "fake-pending"})
    e.live.buy = uncertain
    with pytest.raises(FusionPendingOrder): buy(e)
    assert e.kill_switch and e.mode == "paper"
    assert e.db.query("SELECT state FROM orders")[0]["state"] == "uncertain"
    assert not e.db.get("volatility_qty")
    with pytest.raises(ValueError): buy(e)
    assert len(e.db.query("SELECT * FROM orders")) == 1


def test_global_stop_missing_prices_and_currency_mismatch_block_exit(pilot):
    e = pilot; ready(e); buy(e)
    e.live.price = 9
    e.db.set("kill_switch", True)
    asyncio.run(e.volatility.watch())
    assert not e.live.sells
    e.db.set("kill_switch", False)
    e.live.price = float("nan")
    asyncio.run(e.volatility.watch())
    assert not e.live.sells and e.volatility.status()["exit_check"]["error"]
    e.live.price = 9; e.live.currency = "EUR"
    asyncio.run(e.volatility.watch())
    assert not e.live.sells
    assert asyncio.run(e.execution.sell("SOL", "manual", book="volatility")) is None


def test_pilot_visible_as_separate_experimental_lane(pilot):
    d = snapshot(pilot)
    lane = next(l for l in d["lanes"] if l["id"] == "speculation")
    assert lane["status"] == "off"
    mod = next(m for m in d["architecture"]["modules"] if m["id"] == "speculation")
    assert mod["live_capable"] and mod["role"] == "experimental"
    ready(pilot)
    assert snapshot(pilot)["lanes"][2]["status"] == "armed"


def test_activation_api_is_password_protected_and_requires_confirmation(pilot, monkeypatch):
    from fastapi.testclient import TestClient
    from tradingbotty import server
    monkeypatch.setattr(server, "engine", pilot)
    client = TestClient(server.PasswordGate(server.app, "pilot-secret"))
    endpoint = "/api/speculation/live"
    assert client.post(endpoint, json={"enabled": True, "confirmation": CONFIRMATION}).status_code == 401
    client.auth = ("user", "pilot-secret")
    assert client.post(endpoint, json={"enabled": True}).status_code == 400
    assert client.post(endpoint, json={"enabled": True, "confirmation": CONFIRMATION}).status_code == 200
    assert pilot.volatility.cfg()["enabled"] and not pilot.live.buys
    assert client.post(endpoint, json={"enabled": False}).status_code == 200
    assert not pilot.volatility.cfg()["enabled"]


def test_concurrent_pilot_buys_can_fill_only_one_slot(pilot):
    ready(pilot)
    async def run():
        return await asyncio.gather(pilot.execution.buy("SOL", 30, "one", book="volatility"),
                                    pilot.execution.buy("BTC", 30, "two", book="volatility"), return_exceptions=True)
    result = asyncio.run(run())
    assert sum(isinstance(r, ValueError) for r in result) == 1
    assert len(pilot.live.buys) == 1 and len(pilot.db.get("volatility_qty")) == 1


def test_old_exit_cannot_sell_a_new_position(pilot):
    e = pilot; ready(e); buy(e)
    ts = e._open_buys("volatility", "SOL")[0]["ts"]
    result = asyncio.run(e.execution.sell("SOL", "stale decision", book="volatility", expected_entry_ts=ts-1))
    assert result is None and not e.live.sells


def test_failed_exit_stays_visible_and_keeps_owned_quantity(pilot):
    e = pilot; ready(e); buy(e); e.live.price = 9
    async def no_fill(*args, **kwargs): return None
    e.live.sell_fraction = no_fill
    asyncio.run(e.volatility.watch())
    assert e.db.get("volatility_qty") == {"SOL": 3}
    assert "nicht bestätigt" in e.volatility.status()["exit_check"]["error"]


def test_confirmation_cannot_enable_simulated_data(pilot):
    pilot.settings.simulate = True
    with pytest.raises(ValueError, match="Simulationsdaten"):
        pilot.volatility.configure(True, CONFIRMATION)
    assert not pilot.volatility.cfg()["enabled"]


def test_pilot_cap_does_not_consume_fast_reserved_cash(pilot):
    e = pilot; ready(e)
    cfg=e.fast.cfg(); cfg.update(on=True, chf=80); e.fast.save(cfg)
    with pytest.raises(ValueError, match="minimum"):
        buy(e)
    assert not e.live.buys and not e.live.sells
