"""Phone briefing (WhatsApp/Telegram), the dashboard password and moving the bot without trading twice."""
import asyncio
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeFusion, _engine  # noqa: E402


class FakeHTTP:
    def __init__(self):
        self.calls = []

    async def get(self, url, params=None, **kw):
        self.calls.append(("GET", url, params))
        return type("R", (), {"status_code": 200, "text": "Message queued"})()

    async def post(self, url, json=None, content=None, **kw):
        self.calls.append(("POST", url, json if json is not None else {"text": content.decode()}))
        return type("R", (), {"status_code": 200, "text": "{}"})()


def _phone(e, monkeypatch):
    http = FakeHTTP()
    monkeypatch.setattr(e.prices, "client", http)
    e.settings.simulate = False
    e.settings.whatsapp_phone, e.settings.whatsapp_key = "+41 79 123 45 67", "123456"
    return http


def test_whatsapp_message_and_morning_briefing_once_a_day(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    http = _phone(e, monkeypatch)
    assert e.phone_channels() == ["WhatsApp"]
    assert asyncio.run(e.notify("hi"))
    method, url, params = http.calls[0]
    assert "callmebot" in url and params["phone"] == "+41791234567" and params["apikey"] == "123456"
    e.settings.raw["phone"]["morning_hour"] = 0          # any hour counts as "morning" in the test
    asyncio.run(e.morning_tick())
    asyncio.run(e.morning_tick())
    briefings = [c for c in http.calls[1:] if "TradingBotty" in c[2]["text"]]
    assert len(briefings) == 1 and "Account" in briefings[0][2]["text"]


def test_trade_alert_for_both_traders(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    http = _phone(e, monkeypatch)

    async def go():
        e._record("SOL", "BUY", {"quantity": 1, "price": 30}, 30.0, "breakout", book="fast")
        await asyncio.sleep(0)
    asyncio.run(go())
    assert any("Fast pot" in c[2]["text"] and "BUY SOL 30.00" in c[2]["text"] for c in http.calls)
    e.settings.raw["phone"]["trades"] = False
    n = len(http.calls)
    asyncio.run(go())
    assert len(http.calls) == n


def test_moved_bot_refuses_live(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    e.db.set("moved", {"ts": 1.0, "to": "https://x.onrender.com"})
    r = asyncio.run(e.set_mode("live"))
    assert not r["ok"] and "twice" in r["error"]


def test_password_gate():
    from tradingbotty.server import PasswordGate

    seen = []

    async def app(scope, receive, send):
        seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gate = PasswordGate(app, "geheim")

    def call(path, headers=()):
        out = []

        async def send(m):
            out.append(m)
        asyncio.run(gate({"type": "http", "path": path, "headers": list(headers)}, None, send))
        return out[0]

    assert call("/api/state")["status"] == 401
    assert call("/api/health")["status"] == 200
    bad = base64.b64encode(b"bot:falsch")
    assert call("/", [(b"authorization", b"Basic " + bad)])["status"] == 401
    good = base64.b64encode(b"bot:geheim")
    r = call("/", [(b"authorization", b"Basic " + good)])
    assert r["status"] == 200
    cookie = dict(r["headers"])[b"set-cookie"].decode().split(";")[0]
    assert call("/api/state", [(b"cookie", cookie.encode())])["status"] == 200


def test_ntfy_message(tmp_path, monkeypatch):
    e = _engine(tmp_path, monkeypatch, FakeFusion())
    http = FakeHTTP()
    monkeypatch.setattr(e.prices, "client", http)
    e.settings.simulate = False
    e.settings.ntfy_topic = "tradingbotty-secret123"
    assert e.phone_channels() == ["ntfy"]
    assert asyncio.run(e.notify("hallo"))
    assert http.calls == [("POST", "https://ntfy.sh/tradingbotty-secret123", {"text": "hallo"})]
