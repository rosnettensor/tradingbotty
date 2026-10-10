"""Strategy catalogue and deployment checks shared by execution and the dashboard.

Research creates evidence. It cannot create a new live execution path or promote
a module on its own. Daily and Fast are deployed signal families; Volatility has an explicit experimental mandate.
"""
from __future__ import annotations

import hashlib
import json

from .readiness import evidence


class StrategyRegistry:
    def __init__(self, engine):
        self.e = engine

    def checks(self, book, now=None):
        e = self.e
        if book == "fast":
            return [evidence(e.db.get("fastlab"), e.fast.cfg()["strategy"], now)]
        if book != "brain":
            raise ValueError("research modules cannot submit live orders")
        b = e.brain()
        mode, cfg = e.bank.mode(), e.bank.cfg()
        report = cfg.get("report") or {}
        if mode != "shadow" and report.get("split"):
            split = report.get("probe_split" if mode == "probe" else "split") or {}
            names = [n for n, w in split.items() if n != "Cash" and w > 0]
        else:
            names = [b.get("strategy") or ""]
        return [evidence(e.db.get("research"), name, now) for name in names]

    def revision(self, book):
        e = self.e
        if book == "fast":
            c = e.fast.cfg()
            cfg = {k: c.get(k) for k in ("on", "strategy", "mode", "chf", "pct", "floor")}
        elif book == "brain":
            b, bank = e.brain(), e.bank.cfg()
            report = bank.get("report") or {}
            cfg = {"on": b.get("on"), "strategy": b.get("strategy"), "bank": e.bank.mode(),
                   "split": report.get("probe_split" if e.bank.mode() == "probe" else "split")}
        elif book == "volatility":
            cfg = e.volatility.cfg()
        else:
            raise ValueError("research modules cannot submit live orders")
        return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:20]

    def admit(self, book, symbol, revision=None):
        e = self.e
        if book not in ("brain", "fast", "volatility"):
            raise ValueError("research modules cannot submit live orders")
        if revision is not None and revision != self.revision(book):
            raise ValueError("strategy settings changed; waiting for a new decision")
        if book == "volatility":
            e.volatility.admit(symbol)  # explicit experimental mandate, never fabricated backtest evidence
        else:
            enabled = e.brain_on() if book == "brain" else e.fast.on()
            if not enabled:
                raise ValueError("strategy is disabled")
            proofs = self.checks(book)
            if not proofs or not all(p["ready"] for p in proofs):
                raise ValueError("strategy evidence: " + next((p["reason"] for p in proofs if not p["ready"]), "Cash allocation"))
        if not e.stance.buys_allowed():
            raise ValueError("new purchases paused by your course")
        if symbol in e.stance.locked():
            raise ValueError("coin locked after profit protection")
        if e.guard().get(symbol):
            raise ValueError("Guardian blocks this coin")

    def catalogue(self):
        e = self.e
        return [
            {"id": "daily", "book": "brain", "name": "Daily", "role": "strategy", "cadence": "1 Tag",
             "live_capable": True, "enabled": e.brain_on(), "description": "Tagesstrategie; Bank verteilt bei Bedarf deren Budget."},
            {"id": "fast", "book": "fast", "name": "Fast", "role": "strategy", "cadence": "4 Stunden",
             "live_capable": True, "enabled": e.fast.on(), "description": "Kurzfristige Strategie; Ausstiege werden jede Minute geprüft."},
            {"id": "speculation", "book": "volatility", "name": "Volatility · Pilot", "role": "experimental", "cadence": "2 Minuten",
             "live_capable": True, "enabled": e.volatility.cfg()["enabled"], "description": "Begrenzter Echtgeld-Pilot nach manueller Freigabe. Maximal eine Position; noch kein Profitabilitätsnachweis."},
            {"id": "think", "name": "Think Tank", "role": "research", "live_capable": False, "enabled": False,
             "description": "Entwickelt Strategiekandidaten. Kandidaten brauchen weitere Laborprüfungen und eine bewusste Aktivierung."},
        ]
