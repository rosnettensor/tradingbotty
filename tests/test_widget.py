"""The iPhone widget: a read-only key opens /api/widget and nothing else."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fakes import FakeFusion, _engine  # noqa: E402


def test_widget_numbers(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    e.wallet = {"total": 360.0, "currency": "CHF", "fiat": 50.0, "bot_edge": 1.2, "change_24h": -3.4}
    e.db.set("live_qty", {"UNI": 1.0, "NEAR": 2.0})
    e.db.set("wallet_hist", [[1, 350.0, 0.5], [2, 360.0, 1.2]])
    e.stance.set("bunker")
    w = e.widget()
    assert w["total"] == 360.0 and w["bot_edge"] == 1.2 and w["brain"] == ["NEAR", "UNI"]
    assert w["mode"] == "LIVE" and w["course"] == "🏦 Bunkern" and w["fast"] is None


def test_widget_key_opens_only_the_widget(monkeypatch):
    from tradingbotty import server
    monkeypatch.setattr(server.settings, "password", "geheim")
    key = server.widget_key()
    assert len(key) == 32 and "geheim" not in key
    gate = server.PasswordGate(lambda *a: None, "geheim")

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
    gate.app = app
    out = []

    async def send(m):
        out.append(m)
    asyncio.run(gate({"type": "http", "path": "/api/widget", "headers": []}, None, send))
    assert out[0]["status"] == 200                       # the gate lets it through; the endpoint checks the key
    out.clear()
    asyncio.run(gate({"type": "http", "path": "/api/state", "headers": []}, None, send))
    assert out[0]["status"] == 401                       # the key is no password: everything else stays shut
    import pytest
    with pytest.raises(server.HTTPException):
        asyncio.run(server.widget(key="wrong"))
    monkeypatch.setattr(server, "engine", type("E", (), {"widget": lambda self: {"ok": 1}})())
    assert asyncio.run(server.widget(key=key)) == {"ok": 1}
