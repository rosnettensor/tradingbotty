"""Web server: REST + WebSocket for the dashboard, and serves the built frontend."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT, load_settings
from .engine import Engine
from .llm import PRICES

DIST = ROOT / "frontend" / "dist"
MEDIA = ROOT / "media"
MEDIA_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm"}



def _take_incoming(db_path: Path) -> None:
    """A database sent by 'move to server' waits next to the live one until the next start, then takes its place."""
    incoming = db_path.with_suffix(".incoming")
    if incoming.exists():
        if db_path.exists():
            db_path.replace(db_path.with_suffix(f".before-move-{int(time.time())}.db"))
        incoming.replace(db_path)


settings = load_settings()
_take_incoming(settings.db_path)
engine = Engine(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(engine.run())
    yield
    task.cancel()


app = FastAPI(title="TradingBotty", lifespan=lifespan)


class PasswordGate:
    """With TB_PASSWORD set (always on a server), every page, API call and live feed needs the password.
    The browser asks once (any user name), then a cookie keeps you signed in for 30 days."""
    OPEN = {"/api/health", "/apple-touch-icon.png", "/icon-192.png", "/icon-512.png", "/manifest.webmanifest",  # the phone fetches the icon without the password
            "/api/widget"}  # checks its own read-only key (see widget_key)

    def __init__(self, app, password: str | None):
        self.app, self.password = app, password
        self.token = hmac.new((password or "").encode(), b"tradingbotty-session", hashlib.sha256).hexdigest()

    def _ok(self, headers: dict) -> tuple[bool, bool]:
        """(allowed, needs the cookie)"""
        for part in headers.get(b"cookie", b"").decode(errors="ignore").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "tb_auth" and hmac.compare_digest(v, self.token):
                return True, False
        auth = headers.get(b"authorization", b"").decode(errors="ignore")
        if auth.lower().startswith("basic "):
            try:
                pw = base64.b64decode(auth[6:]).decode().partition(":")[2]
            except Exception:
                pw = ""
            if hmac.compare_digest(pw.encode(), self.password.encode()):
                return True, True
        return False, False

    async def __call__(self, scope, receive, send):
        if not self.password or scope["type"] not in ("http", "websocket") or scope.get("path") in self.OPEN:
            return await self.app(scope, receive, send)
        ok, cookie = self._ok(dict(scope.get("headers") or []))
        if ok and scope["type"] == "http" and cookie:
            async def send_with_cookie(msg):
                if msg["type"] == "http.response.start":
                    msg.setdefault("headers", [])
                    msg["headers"] = list(msg["headers"]) + [(b"set-cookie", f"tb_auth={self.token}; Path=/; Max-Age=2592000; "
                                                                              "HttpOnly; Secure; SameSite=Strict".encode())]
                await send(msg)
            return await self.app(scope, receive, send_with_cookie)
        if ok:
            return await self.app(scope, receive, send)
        await asyncio.sleep(1)  # slows down password guessing
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"www-authenticate", b'Basic realm="TradingBotty", charset="UTF-8"'),
                                (b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"TradingBotty: password needed"})


app.add_middleware(PasswordGate, password=settings.password)


def widget_key() -> str:
    """A read-only key for the iPhone widget, made from the dashboard password: it opens /api/widget and nothing
    else, so the password itself never sits in the widget's script. A new password makes a new key."""
    return hmac.new((settings.password or "").encode(), b"tradingbotty-widget", hashlib.sha256).hexdigest()[:32]


@app.get("/api/widget")
async def widget(key: str = ""):
    if settings.password and not hmac.compare_digest(key.encode(), widget_key().encode()):
        await asyncio.sleep(1)  # slows down guessing
        raise HTTPException(401, "wrong widget key: copy the script again in Controls")
    return engine.widget()


@app.get("/api/widget/key")
def get_widget_key():
    """Behind the password: the key the Controls tab puts into the widget script."""
    return {"key": widget_key() if settings.password else ""}


@app.get("/api/health")
def health():
    return {"ok": True}


class ModeIn(BaseModel):
    mode: str
    confirm: str = ""


class ToggleIn(BaseModel):
    on: bool


class FastIn(BaseModel):
    on: bool | None = None
    strategy: str | None = None
    mode: str | None = None
    chf: float | None = None
    pct: float | None = None
    floor: float | None = None


class ControlsIn(BaseModel):
    changes: dict


class SourceIn(BaseModel):
    kind: str
    value: str
    name: str = ""


class AgentIn(BaseModel):
    enabled: bool | None = None
    prompt: str | None = None
    model: str | None = None
    reset_prompt: bool = False
    run_now: bool = False


@app.get("/api/state")
def get_state():
    return engine.state()


@app.get("/api/trades")
def get_trades(limit: int = 100):
    return engine.recent_trades(limit)


@app.post("/api/mode")
async def set_mode(body: ModeIn):
    if body.mode == "live" and body.confirm != "REAL MONEY":
        raise HTTPException(400, "Type REAL MONEY to confirm live trading.")
    return await engine.set_mode(body.mode)


@app.post("/api/kill")
def kill(body: ToggleIn):
    engine.set_kill_switch(body.on)
    return {"ok": True, "kill_switch": body.on}


@app.get("/api/controls")
def get_controls():
    return engine.controls()


@app.post("/api/controls")
def set_controls(body: ControlsIn):
    try:
        return engine.set_controls(body.changes)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/sources")
def get_sources():
    return engine.sources_info()


@app.post("/api/sources/add")
async def add_source(body: SourceIn):
    return await engine.add_source(body.kind, body.value, body.name)


@app.post("/api/sources/remove")
def remove_source(body: SourceIn):
    return engine.remove_source(body.kind, body.value)


@app.get("/api/models")
def models():
    return [{"id": m, "input": p[0], "output": p[1]} for m, p in PRICES.items()]


@app.post("/api/agents/{aid}")
def set_agent(aid: str, body: AgentIn):
    try:
        return engine.set_agent(aid, body.enabled, body.prompt, body.model, body.reset_prompt, body.run_now)
    except KeyError:
        raise HTTPException(404, "no such agent")
    except ValueError as e:
        raise HTTPException(400, str(e))


class ChatIn(BaseModel):
    message: str
    history: list = []


class ConfirmIn(BaseModel):
    token: str


class StanceIn(BaseModel):
    key: str


@app.post("/api/stance")
def set_stance(body: StanceIn):
    """Set or end your course from the dashboard (the chat sets it with a confirm step)."""
    try:
        return engine.stance.set(body.key, by="you (dashboard)")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/chat/confirm")
async def chat_confirm(body: ConfirmIn):
    return await engine.orders.confirm(body.token)


@app.post("/api/chat")
async def chat(body: ChatIn):
    msg = body.message.strip()
    if not msg or len(msg) > 2000:
        raise HTTPException(400, "Ask a question of 1 to 2000 characters.")
    history = [h for h in body.history if isinstance(h, dict)]
    return await engine.chat(msg, history)


class BrainIn(BaseModel):
    on: bool
    strategy: str | None = None


@app.post("/api/brain")
async def brain_set(body: BrainIn):
    try:
        return await engine.set_brain(body.on, body.strategy)
    except ValueError as e:
        raise HTTPException(400, str(e))


class ThinkTankIn(BaseModel):
    action: str
    name: str | None = None


@app.get("/api/thinktank")
def thinktank_get():
    return engine.thinktank_info()


@app.post("/api/thinktank")
def thinktank_post(body: ThinkTankIn):
    try:
        return engine.thinktank_action(body.action, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/research/run")
async def research_run():
    try:
        return await engine.run_research()
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/research")
async def research_get():
    return engine.db.get("research", {}) or {}


@app.post("/api/fastlab/run")
async def fastlab_run():
    try:
        return await engine.run_fastlab()
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/fastlab")
async def fastlab_get():
    return engine.db.get("fastlab", {}) or {}


@app.get("/api/fastlab/status")
async def fastlab_status():
    return engine.__dict__.get("fast_status") or {"running": False}


@app.get("/api/fast")
async def fast_get():
    return engine.fast.status()


@app.post("/api/fast")
async def fast_set(body: FastIn):
    try:
        return await engine.fast.set(body.on, body.strategy, body.mode, body.chf, body.pct, body.floor)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/fast/close")
async def fast_close():
    try:
        return await engine.fast.close_all()
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/patterns")
async def patterns_get():
    return engine.db.get("patterns", {}) or {}


@app.get("/api/report")
def report():
    return {"text": engine.daily_report(), "telegram": bool(settings.telegram_token and settings.telegram_chat)}


@app.get("/api/report/weekly")
@app.post("/api/report/weekly")
async def report_weekly(send: int = 0):
    """The Sunday report card: ?send=0 shows the text, ?send=1 sends it to your phone now."""
    if send:
        if not engine.phone_channels():
            raise HTTPException(400, "Add NTFY_TOPIC (or WhatsApp or Telegram) to .env and restart first.")
        if not await engine.send_weekly():
            raise HTTPException(400, "The message was refused: check the keys in .env (details in the agent feed).")
    return {**engine.weekly(), "sent": bool(send)}


@app.get("/api/scoreboard")
def scoreboard():
    """Bauplan 2, phase 1: each agent's score in your currency, the loss check and the ghost trades."""
    return engine.scoreboard()


@app.get("/api/diary")
def diary(limit: int = 300):
    """The trade diary: every real trade of the Daily Brain and the fast pot, newest first."""
    return {"entries": engine.diary(max(1, min(300, limit)))}


@app.post("/api/phone/test")
@app.post("/api/telegram/test")
async def phone_test():
    if not engine.phone_channels():
        raise HTTPException(400, "Add WHATSAPP_PHONE and WHATSAPP_APIKEY (or the Telegram pair) to .env and restart first.")
    ok = await engine.notify("✅ Test: your phone messages work. This is what the morning briefing looks like:\n\n"
                             + engine.daily_report(), title=engine.report_title(), tags=["white_check_mark"])
    if not ok:
        raise HTTPException(400, "The message was refused: check the keys in .env (details in the agent feed).")
    return {"ok": True, "channels": engine.phone_channels()}


# ---- moving the bot between this computer and a server, without ever trading twice
@app.get("/api/move/export")
async def move_export(request: Request):
    """Switches this bot to STANDBY for good (until 'trade here again') and hands out its whole database."""
    if engine.__dict__.get("_brain_busy") or engine.__dict__.get("_fast_trading"):
        raise HTTPException(409, "A decision is running right now. Try again in a minute.")
    await engine.set_mode("paper")
    tmp = Path(tempfile.mkdtemp()) / "tradingbotty.db"
    engine.db.snapshot(tmp)
    engine.db.set("moved", {"ts": time.time(), "to": request.query_params.get("to", "the server")})
    engine._log("Engine", "live", "Bot moved away: this copy stays on STANDBY so the money is never traded twice.")
    return FileResponse(tmp, filename="tradingbotty.db", media_type="application/octet-stream")


@app.post("/api/move/import")
async def move_import(request: Request):
    """Takes the database from the other copy and restarts with it (on STANDBY: you switch LIVE yourself)."""
    if engine.mode == "live":
        raise HTTPException(409, "This copy is LIVE. Switch it to STANDBY first.")
    data = await request.body()
    if not data.startswith(b"SQLite format 3\x00"):
        raise HTTPException(400, "That is not a TradingBotty database.")
    incoming = settings.db_path.with_suffix(".incoming")
    incoming.write_bytes(data)
    con = sqlite3.connect(incoming)
    con.execute("DELETE FROM kv WHERE key IN ('moved')")
    con.execute("INSERT INTO kv(key,value) VALUES('mode','\"paper\"') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    con.commit()
    con.close()
    engine._log("Engine", "info", f"Received the bot's database ({len(data) // 1024} KB). Restarting with it now.")
    asyncio.get_running_loop().call_later(1.5, os._exit, 0)  # the server starts it again with the new database
    return {"ok": True, "kb": len(data) // 1024}


@app.post("/api/move/back")
def move_back():
    """'Trade on this computer again': only after the other copy is on STANDBY or switched off."""
    engine.db.set("moved", None)
    engine._log("Engine", "info", "This copy may trade again (the moved-away lock is off). LIVE still needs REAL MONEY.")
    return {"ok": True}


@app.get("/api/candles/{symbol}")
def candles(symbol: str, minutes: int = 240):
    try:
        return engine.candles(symbol.upper(), max(30, min(7 * 1440, minutes)))
    except KeyError:
        raise HTTPException(404, "unknown symbol")


@app.get("/api/daily/{symbol}")
async def daily(symbol: str, days: int = 120):
    try:
        return await engine.daily(symbol.upper(), max(30, min(1000, days)))
    except KeyError:
        raise HTTPException(404, "unknown symbol")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/media")
def media():
    """Optional custom visuals the user dropped into the media folder (see media/README.md)."""
    files = sorted(p.name for p in MEDIA.glob("*") if p.suffix.lower() in MEDIA_TYPES) if MEDIA.exists() else []
    pick = lambda stem: next((f"/media/{f}" for f in files if f.rsplit(".", 1)[0] == stem), None)  # noqa: E731
    return {
        "background": pick("background"), "logo": pick("logo"),
        "avatars": {f.rsplit(".", 1)[0].removeprefix("agent-"): f"/media/{f}" for f in files if f.startswith("agent-")},
    }


@app.get("/media/{name}")
def media_file(name: str):
    f = (MEDIA / name).resolve()
    if f.parent != MEDIA.resolve() or f.suffix.lower() not in MEDIA_TYPES or not f.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(f)


@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()
    q = engine.bus.subscribe()
    try:
        await socket.send_text(json.dumps({"kind": "hello", "data": engine.state()}, default=str))
        while True:
            await socket.send_text(await q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        engine.bus.unsubscribe(q)


if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
else:
    @app.get("/")
    def no_frontend():
        return JSONResponse({"error": "frontend not built. Run: cd frontend && npm install && npm run build"})
