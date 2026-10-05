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
from .llm import PRICES
from .strategy import StrategyConfig

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


class BuyNowIn(BaseModel):
    amount: float


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


class VariantConfigIn(BaseModel):
    changes: dict
    as_new: bool = False
    name: str = ""


class BacktestIn(BaseModel):
    variant_id: str | None = None
    config: dict | None = None
    hours: float = 24
    n: int = 40


def _cfg_for(body: BacktestIn) -> StrategyConfig:
    v = engine.variants.get(body.variant_id or "") or engine.champion()
    base = v.config.to_dict() if v else {}
    return StrategyConfig.from_dict({**base, **(body.config or {})}).clamped()


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


@app.post("/api/buy_now")
async def buy_now(body: BuyNowIn):
    """Your button: a real buy of `amount` in the best coin right now (or within an hour, or a clear reason why not)."""
    try:
        return await engine.force_buy(body.amount)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/buy_now/cancel")
def buy_now_cancel():
    engine.cancel_force_buy()
    return {"ok": True}


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
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/variants/{vid}/clone")
def clone(vid: str):
    v = engine.variants.get(vid)
    if not v:
        raise HTTPException(404, "no such variant")
    child = engine.add_variant(v.config.mutate(engine.agent("optimizer").rng), parent_id=vid, note="cloned by you")
    return {"ok": True, "id": child.id, "name": child.name}


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


@app.get("/api/radar")
def get_radar():
    return engine.radar()


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


@app.post("/api/variants/{vid}/config")
def set_variant_config(vid: str, body: VariantConfigIn):
    try:
        v = engine.set_variant_config(vid, body.changes, body.as_new, body.name)
    except KeyError:
        raise HTTPException(404, "no such variant")
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": v.id, "name": v.name, "config": v.config.to_dict()}


@app.post("/api/variants/{vid}/retire")
def retire(vid: str):
    v = engine.variants.get(vid)
    if not v:
        raise HTTPException(404, "no such variant")
    if v.champion:
        raise HTTPException(400, "promote another strategy before retiring the champion")
    engine.retire_variant(vid)
    return {"ok": True}


@app.post("/api/backtest")
async def run_backtest(body: BacktestIn):
    try:
        return await asyncio.to_thread(engine.backtest_sync, _cfg_for(body), max(2.0, min(168.0, body.hours)))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/autotune")
async def run_autotune(body: BacktestIn):
    try:
        ranked = await asyncio.to_thread(engine.autotune_sync, _cfg_for(body), max(5, min(120, body.n)),
                                         max(2.0, min(168.0, body.hours)))
    except ValueError as e:
        raise HTTPException(400, str(e))
    top = ranked[:12]
    current = next((r for r in ranked if r["label"] == "current settings"), None)
    if current and current not in top:
        top.append(current)
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
    return {"results": top, "tested": len(ranked)}


@app.get("/api/candles/{symbol}")
def candles(symbol: str, minutes: int = 240):
    try:
        return engine.candles(symbol.upper(), max(30, min(7 * 1440, minutes)))
    except KeyError:
        raise HTTPException(404, "unknown symbol")


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
