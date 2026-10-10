"""The only gateway to broker mutations. Strategy modules never own an exchange client.

One account lock, admission policy, persistent order journal and atomic fill booking.
The Engine's legacy methods delegate here for compatibility with existing integrations.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid

from . import risk
from .brokers.fusion import FusionPendingOrder, FusionError, FusionBroker
from .brokers.bitpanda import BitpandaBroker


class ExecutionService:
    def __init__(self, engine):
        self.e = engine
        self.lock = asyncio.Lock()

    def permission(self):
        e = self.e
        if e.mode != "live" or not e.live:
            raise ValueError("live trading is off")
        if e.kill_switch:
            raise ValueError("kill switch is on")
        if e.db.get("unresolved_order"):
            raise ValueError("unresolved order: reconcile before resuming")
        if e.db.get("moved"):
            raise ValueError("another instance owns the account")
        if e.settings.simulate and isinstance(e.live, (FusionBroker, BitpandaBroker)):
            raise ValueError("simulation data cannot authorize real exchange orders")

    def recover(self):
        """A crash after POST and before booking must never silently replay an order."""
        pending = self.e.db.query("SELECT id,book,symbol,side,state FROM orders WHERE state IN ('submitting','uncertain')")
        if pending:
            self.e._unresolved(FusionPendingOrder({"status": "reconciliation required after restart", "local_orders": pending}))

    def _begin(self, book, symbol, side, request):
        self.permission()  # immediately before every broker mutation, after all awaited checks
        oid, now = uuid.uuid4().hex, time.time()
        self.e.db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?)",
                          (oid, now, now, book, symbol, side, "submitting", json.dumps(request), None))
        return oid

    def _state(self, oid, state, result):
        self.e.db.execute("UPDATE orders SET state=?,updated_at=?,result_json=? WHERE id=?",
                          (state, time.time(), json.dumps(result), oid))

    def _uncertain(self, oid, error):
        order = dict(error.order) if isinstance(error, FusionPendingOrder) else {"status": "unknown", "error": str(error)[:200]}
        order["local_id"] = oid
        self._state(oid, "uncertain", order)
        self.e._unresolved(FusionPendingOrder(order))

    async def _call(self, oid, fn, *args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except FusionPendingOrder as error:
            self._uncertain(oid, error)
            raise
        except FusionError as error:
            # A definite broker rejection did not create exposure; transport uncertainty did.
            if error.http_status and error.http_status >= 500:
                self._uncertain(oid, error)
            else:
                self._state(oid, "rejected", {"error": str(error)[:200]})
            raise
        except BaseException as error:
            self._uncertain(oid, error)
            raise

    @staticmethod
    def _fill(result):
        ex = dict((result or {}).get("execution") or {})
        qty = risk.positive(ex.get("quantity"), "confirmed fill quantity")
        amount = risk.positive(ex.get("notional"), "confirmed fill amount")
        fee = float(ex.get("fee") or 0)
        if not math.isfinite(fee) or fee < 0:
            raise ValueError("invalid confirmed fee")
        return {**ex, "quantity": qty, "notional": amount, "price": amount / qty, "fee": fee}

    async def buy(self, sym, want, reason, book="brain", *, automatic=False, context=None, revision=None):
        async with self.lock:
            try:
                self.permission()
                self.e.portfolio.keys(book)
                if automatic:
                    self.e.strategies.admit(book, sym, revision)
            except ValueError as error:
                self.e._risk_note(f"BUY {sym}", [["admission", "stop", str(error)]], f"not sent: {error}")
                raise
            return await self._buy_locked(sym, want, reason, book, automatic=automatic, context=context, revision=revision)

    async def sell(self, symbol, reason, book="brain", fraction=1.0):
        async with self.lock:
            try:
                self.permission()
                self.e.portfolio.keys(book)
                if not math.isfinite(fraction) or not 0 < fraction <= 1:
                    raise ValueError("sell fraction must be within (0,1]")
            except ValueError as error:
                self.e._log("Live Desk", "warn", f"SELL {symbol} not sent: {error}")
                return None
            return await self._sell_locked(symbol, reason, book, fraction)

    async def _sell_locked(self, symbol, reason, book="brain", fraction=1.0):
        e, oid = self.e, None
        kq, kc = e.portfolio.keys(book)
        try:
            owned = e.db.get(kq, {})
            mine = owned.get(symbol, 0.0)
            if mine <= 0:
                return None
            if hasattr(e.live, "total_balance") and await e.live.total_balance(symbol) == 0:
                # A fresh total (available + locked) confirms an external manual disposal.
                with e.db.transaction():
                    owned.pop(symbol, None)
                    cost = e.db.get(kc, {}); cost.pop(symbol, None)
                    e.db.set(kq, owned); e.db.set(kc, cost)
                    if book == "fast":
                        c = e.fast.cfg(); c["pos"].pop(symbol, None); e.fast.save(c)
                return None
            oid = self._begin(book, symbol, "SELL", {"fraction": fraction, "owned": mine, "reason": reason})
            result = await self._call(oid, e.live.sell_fraction, symbol, fraction, owned=mine)
            if result is None:
                # No POST is sent by the adapter if the available rounded quantity is zero.
                self._state(oid, "no_fill", {})
                return None
            ex = self._fill(result)
            sold = ex["quantity"]
            if sold > mine + max(1e-12, mine * 1e-9):
                raise ValueError("fill exceeds attributed ownership")
            cost = e.db.get(kc, {})
            paid = float(cost.get(symbol, 0) or 0)
            basis = {"cost": paid * min(1, sold / mine), "qty": sold,
                     "fee": e.portfolio.fee_basis(book, symbol, sold)}
            if mine - sold > 1e-12:
                owned[symbol], cost[symbol] = mine - sold, paid - basis["cost"]
            else:
                owned.pop(symbol, None); cost.pop(symbol, None)
            # Positions, realized result, fast attribution and journal commit together.
            with e.db.transaction():
                e.db.set(kq, owned); e.db.set(kc, cost)
                e._record(symbol, "SELL", ex, ex["notional"], reason, book, basis)
                if book == "fast":
                    ex["_basis"] = basis
                    ex["_booked_fast_pnl"] = e.fast.booked_sell(symbol, ex)
                self._state(oid, "filled", {"order_id": result.get("order_id"), "execution": ex})
            e.live_errors = 0
            e._log("Live Desk", "live", f"LIVE SELL {symbol}: {ex['notional']:.2f} {e.live.currency} filled ({reason}).")
            return ex
        except Exception as error:
            if oid and e.db.query("SELECT state FROM orders WHERE id=?", (oid,))[0]["state"] == "submitting":
                self._uncertain(oid, error)
            e.live_errors += 1
            e._log("Live Desk", "error", f"LIVE SELL {symbol} failed: {error}")
            if e.live_errors >= 3:
                e.db.set("mode", "paper")
            return None

    def minimum(self, sym: str, pairs: dict | None = None) -> float:
        """Fusion's smallest order for a coin: its listed minimum, or more if Fusion told us so in a rejection."""
        e = self.e
        pairs = pairs if pairs is not None else (getattr(e.live or e._viewer, "pairs", None) or {})
        return max(float((pairs.get(sym) or {}).get("minOrderAmount") or 0), float(e.db.get("fusion_min", {}).get(sym, 0)))


    def learn_minimum(self, sym: str, ex: Exception) -> bool:
        """Fusion says "Enter a higher amount than 30 CHF" when its list shows a lower minimum: remember the real one."""
        e = self.e
        m = re.search(r"higher amount than ([\d.]+)", str(ex))
        if not m:
            return False
        mins = e.db.get("fusion_min", {})
        mins[sym] = round(float(m.group(1)) * 1.02, 2)
        e.db.set("fusion_min", mins)
        return True


    async def _buy_locked(self, sym: str, want: float, reason: str, book: str = "brain", *, automatic=False, context=None, revision=None) -> tuple[float, float]:
        """One real buy of `want` in account currency, through the Risk Officer's checks, raising cash from your coins
        if allowed (daily brain only). Records it as that trader's coin. Returns (amount spent, quantity received)."""
        e = self.e
        cfg = e.settings["live"]
        def policy_version():
            return json.dumps({"live": e.settings["live"], "risk": e.settings["risk"],
                               "fast": e.strategies.revision("fast")}, sort_keys=True)
        policy = policy_version()

        def recheck():
            self.permission()
            if policy != policy_version():
                raise ValueError("risk or allocation settings changed; recalculate the order")
            if automatic:
                e.strategies.admit(book, sym, revision)

        risk.positive(want, "order amount")
        cur = e.live.currency
        checks: list[list[str]] = []
        order = f"BUY {sym} {want:.2f} {cur}"

        def stop(msg: str) -> ValueError:
            checks.append(["result", "stop", msg])
            e._risk_note(order, checks, f"not sent: {msg}")
            return ValueError(msg)

        if e.kill_switch:
            raise stop("kill switch is on")
        if e.db.get("unresolved_order"):
            raise stop("unresolved Fusion order: reconcile it before resuming")
        if book in ("brain", "fast") and e.portfolio.other_owner(book, sym):
            raise stop("the other strategy already owns this coin; no overlapping books")
        checks.append(["kill switch", "ok", "off"])
        try:
            risk.spot_only((getattr(e.live, "pairs", {}) or {}).get(sym, {}))
        except ValueError as ex:
            raise stop(str(ex)) from ex
        if book == "fast":
            amount = min(want, max(0.0, e.portfolio.fast_budget() - e.portfolio.invested("fast")))
            checks.append(["fast pot", "ok", f"{want:.2f} {cur} from the fast pot (its own limit, set in the Fast Trader Lab)"])
        elif book == "test":
            amount = want
            checks.append(["system check", "ok", f"{want:.2f} {cur} test round trip, sold again right away"])
        else:
            invested = sum(e.db.get("live_cost", {}).values())
            cap = min(cfg["max_invest"], e.portfolio.allocation()["daily"]) if automatic else cfg["max_invest"]
            amount = min(want, cap - invested)
            checks.append(["cap on money in coins", "ok" if amount >= want - 0.01 else "cut",
                           f"{invested:.2f} of {cfg['max_invest']:.0f} {cur} used, {max(0.0, cfg['max_invest'] - invested):.2f} free"])
        if book == "brain" and amount < want - 0.01:
            e.agent("livedesk").say(f"{reason}: lowered {want:.2f} to {amount:.2f} {cur} for {sym} to stay inside your "
                                       f"cap of {cfg['max_invest']:.0f} {cur} in bot trades (raise it in Controls).", "warn")
        pairs = getattr(e.live, "pairs", None) or {}
        if amount <= 0:
            raise stop("investment cap is full")
        try:
            # Fresh account-currency marks for all books; stale dashboard values never authorize a buy.
            bal = await e.live.balances()
            prices = await e.live.prices() if hasattr(e.live, "prices") else {}
            owned, costs = e.portfolio.holdings()
            unrealized = 0.0
            for s, q in owned.items():
                if q > 0:
                    px = risk.positive(prices.get(s), f"{s} risk price")
                    unrealized += max(0.0, costs.get(s, 0.0) - q * px)
            day = time.time() // 86400 * 86400
            realized = e.db.query("SELECT COALESCE(SUM(-pnl),0) loss FROM trades "
                                     "WHERE mode='live' AND side='SELL' AND pnl<0 AND ts>=?", (day,))[0]["loss"]
            loss = realized + unrealized
            positions = {s for s, q in owned.items() if q > 0}
            amount = risk.approve(amount, cfg["max_order"], loss, positions, sym,
                                  int(e.settings["risk"]["max_open_positions"]))
            e.db.set("risk_status", {"ts": time.time(), "daily_loss": round(loss, 2),
                                       "daily_limit": risk.MAX_DAILY_LOSS, "single_limit": risk.MAX_TRADE,
                                       "currency": cur, "positions": len(positions)})
            checks.append(["cash-only risk gate", "ok", f"order ≤ {min(cfg['max_order'], risk.MAX_TRADE):g} {cur}; "
                           f"loss {loss:.2f}/{risk.MAX_DAILY_LOSS:g}; {len(positions)} positions"])
        except Exception as ex:
            raise stop(str(ex)) from ex
        min_amt = e._min_order(sym, pairs)
        if amount < max(min_amt, 1):
            raise stop(f"{amount:.2f} {cur} is below the minimum order ({max(min_amt, 1):g} {cur}) or your cap is full")
        checks.append(["Fusion minimum", "ok", f"{amount:.2f} ≥ {max(min_amt, 1):g} {cur}"])
        if hasattr(e.live, "spread_pct"):
            try:
                spread = await e.live.spread_pct(sym)
            except Exception as ex:
                raise stop(f"spread unavailable: {str(ex)[:60]}") from ex
            else:
                if not math.isfinite(spread) or spread < 0:
                    raise stop("invalid spread")
                if spread > cfg["max_spread_pct"]:
                    e.ghosts.add(book, sym, "risk", f"Spread {spread:.2f}% über deinem Limit", amount)
                    raise stop(f"spread {spread:.2f}% is above your {cfg['max_spread_pct']}% limit")
                checks.append(["spread", "ok", f"{spread:.2f}% ≤ {cfg['max_spread_pct']}%"])
        bal = await e.live.balances()
        reserve = e.portfolio.fast_cash_reserve() if book != "fast" else 0.0  # the fast pot's cash stays for the fast pot
        room = max(0.0, bal.get("FIAT", 0.0) - reserve) * 0.995  # Fusion adds its fee on top: all your cash is "too big"
        if reserve:
            checks.append(["fast pot cash", "kept", f"{reserve:.2f} {cur} stays free for the fast pot"])
        if amount > room:
            if cfg.get("use_my_coins") and book == "brain":
                recheck()
                checks.append(["cash", "short", f"{room:.2f} {cur} usable: selling some of your coins first"])
                room = max(0.0, await self._raise_cash(amount - room, await self.spare_coins(bal), pairs) - reserve) * 0.995
                bal = await e.live.balances()
            amount = min(amount, room)
            if amount < max(min_amt, 1):  # not enough cash for Fusion's minimum: don't send a doomed order
                raise stop(f"only {room:.2f} {cur} cash usable, below the {max(min_amt, 1):g} {cur} minimum"
                           + (" (the fast pot never sells your coins)" if book == "fast"
                              else "" if cfg.get("use_my_coins") else " (the bot may not sell your coins)"))
        checks.append(["cash incl. fee room", "ok", f"{amount:.2f} ≤ 99.5% of {bal.get('FIAT', 0.0):.2f} {cur}"])
        if e.kill_switch:
            raise stop("kill switch is on")
        # Controls, evidence and activation may have changed during network checks.
        recheck()
        oid = self._begin(book, sym, "BUY", {"amount": amount, "reason": reason, "automatic": automatic})
        try:
            result = await self._call(oid, e.live.buy, sym, amount)
            ex = self._fill(result)
            amount, got = ex["notional"], ex["quantity"]
            kq, kc = e.portfolio.keys(book)
            with e.db.transaction():
                cost, owned = e.db.get(kc, {}), e.db.get(kq, {})
                cost[sym] = cost.get(sym, 0.0) + amount
                owned[sym] = owned.get(sym, 0.0) + got
                e.db.set(kc, cost); e.db.set(kq, owned)
                e._record(sym, "BUY", ex, amount, reason, book, checks=checks)
                if book == "fast":
                    c = e.fast.cfg()
                    old = c["pos"].get(sym) or {}
                    c["pos"][sym] = {"entry": amount / got, "bar": (time.time() // 14400 - 1) * 14400,
                                     "ts": time.time(), **old, **(context or {}),
                                     "entry_quote": cost[sym] / owned[sym], "cost": cost[sym], "qty": owned[sym]}
                    e.fast.save(c)
                self._state(oid, "filled", {"order_id": result.get("order_id"), "execution": ex})
            e._risk_note(f"BUY {sym} {amount:.2f} {cur}", checks, "sent")
            return amount, got
        except BaseException as error:
            if e.db.query("SELECT state FROM orders WHERE id=?", (oid,))[0]["state"] == "submitting":
                self._uncertain(oid, error)
            if isinstance(error, Exception) and e._learn_min(sym, error):
                raise stop(f"Fusion wants more than {amount:.2f} {cur} for {sym}; noted for next time") from error
            raise


    async def spare_coins(self, bal: dict) -> dict[str, tuple[float, float]]:
        """Your coins the bot may sell for cash: tradable, not bought by the bot, not about to be bought by the
        brain. symbol -> (quantity, value in account currency)."""
        e = self.e
        if not hasattr(e.live, "prices"):
            return {}
        prices = await e.live.prices()
        mine, _ = e.portfolio.holdings()
        pairs = getattr(e.live, "pairs", {}) or {}
        out = {}
        for sym, qty in bal.items():
            if sym in ("FIAT", e.live.currency) or sym in mine or sym not in pairs or not qty:
                continue
            if sym in e.__dict__.get("_brain_target", {}):
                continue  # the brain is about to buy this coin: selling yours of it to pay would only cost fees
            value = qty * prices.get(sym, 0.0)
            if value >= 1:
                out[sym] = (qty, value)
        return out


    async def _raise_cash(self, need: float, spare: dict, pairs: dict) -> float:
        """Sell spare coins, biggest first, until `need` more cash is there. Returns the new cash balance."""
        e = self.e
        for sym, (qty, value) in sorted(spare.items(), key=lambda kv: -kv[1][1]):
            if not e.settings["live"].get("use_my_coins"):
                raise ValueError("permission to use existing coins was withdrawn")
            if need <= 0:
                break
            low = max(e._min_order(sym, pairs), 1)
            if value < low * 1.02:
                continue  # worth less than Fusion's minimum order: it can't be sold, try the next coin
            frac = min(1.0, need * 1.01 / value)
            if value * frac < low:
                frac = min(1.0, low * 1.05 / value)
            oid = self._begin("funding", sym, "SELL", {"fraction": frac, "owned": qty, "reason": "operator-authorized funding"})
            try:
                result = await self._call(oid, e.live.sell_fraction, sym, frac, owned=qty)
                if result is None:
                    self._state(oid, "no_fill", {})
                    continue
                ex = self._fill(result)
                got = ex["notional"] - ex["fee"]
                with e.db.transaction():
                    e._record(sym, "SELL", ex, ex["notional"], "your coin, sold for cash (you allowed it)", book="funding")
                    self._state(oid, "filled", {"order_id": result.get("order_id"), "execution": ex})
            except Exception as error:
                if e.db.query("SELECT state FROM orders WHERE id=?", (oid,))[0]["state"] == "submitting":
                    self._uncertain(oid, error)
                if e.db.get("unresolved_order"):
                    raise
                e._learn_min(sym, error)
                e.agent("livedesk").say(f"Couldn't sell your {sym} to fund a buy: {str(error)[:120]}", "warn")
                continue
            e.agent("livedesk").say(f"Sold {sym} for {got:.2f} {e.live.currency} to fund a buy (you allowed it).", "live")
            need -= got
        return (await e.live.balances()).get("FIAT", 0.0)
