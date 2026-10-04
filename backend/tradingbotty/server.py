"""Web server: REST + WebSocket for the dashboard, and serves the built frontend."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT, load_settings
from .engine import Engine

DIST = ROOT / "frontend" / "dist"

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


@app.get("/api/state")
def get_state():
    return engine.state()


@app.get("/api/experiments")
def get_experiments():
    return engine.experiments()


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


@app.post("/api/auto_promote")
def auto_promote(body: ToggleIn):
    engine.db.set("auto_promote", body.on)
    return {"ok": True}


@app.post("/api/promote/{vid}")
def promote(vid: str):
    try:
        engine.promote(vid)
    except KeyError:
        raise HTTPException(404, "no such variant")
    return {"ok": True}


@app.post("/api/variants/{vid}/clone")
def clone(vid: str):
    v = engine.variants.get(vid)
    if not v:
        raise HTTPException(404, "no such variant")
    child = engine.add_variant(v.config.mutate(engine.agent("optimizer").rng), parent_id=vid, note="cloned by you")
    return {"ok": True, "id": child.id, "name": child.name}


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
