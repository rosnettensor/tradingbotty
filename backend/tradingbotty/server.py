"""Web server: REST + WebSocket for the dashboard, and serves the built frontend."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT, load_settings
from .engine import Engine
from .llm import PRICES

DIST = ROOT / "frontend" / "dist"
MEDIA = ROOT / "media"
MEDIA_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm"}

settings = load_settings()
engine = Engine(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(engine.run())
    yield
    task.cancel()


app = FastAPI(title="TradingBotty", lifespan=lifespan)


class ModeIn(BaseModel):
    mode: str
    confirm: str = ""


class ToggleIn(BaseModel):
    on: bool


class FlowIn(BaseModel):
    amount: float


class FastIn(BaseModel):
    on: bool | None = None
    strategy: str | None = None
    mode: str | None = None
    chf: float | None = None
    pct: float | None = None


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


class BrainIn(BaseModel):
    on: bool
    strategy: str | None = None


@app.post("/api/brain")
async def brain_set(body: BrainIn):
    try:
        return await engine.set_brain(body.on, body.strategy)
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


@app.post("/api/account/flow")
async def account_flow(body: FlowIn):
    if not body.amount or abs(body.amount) > 1e7:
        raise HTTPException(400, "enter the amount you paid in (+) or took out (-)")
    return engine.book_flow(body.amount, "entered by you")


@app.get("/api/fast")
async def fast_get():
    return engine.fast.status()


@app.post("/api/fast")
async def fast_set(body: FastIn):
    try:
        return await engine.fast.set(body.on, body.strategy, body.mode, body.chf, body.pct)
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


@app.post("/api/telegram/test")
async def telegram_test():
    if not (settings.telegram_token and settings.telegram_chat):
        raise HTTPException(400, "Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env and restart first.")
    ok = await engine.notify("TradingBotty test message: your phone briefing works.\n\n" + engine.daily_report())
    if not ok:
        raise HTTPException(400, "Telegram refused the message: check the token and chat id (see the agent feed).")
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
