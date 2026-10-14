"""Live match server: replays a synthetic match in real time over Server-Sent Events.

Run:  python -m uvicorn pitchpulse.server:app --reload
"""
import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agents import ROSTER, ask_match, commentate
from .mcp_server import MAX_SEED, mcp
from .moments import MomentDetector
from .simulator import AWAY, HOME, simulate
from .stats import MatchStats

STATIC = Path(__file__).parent.parent / "static"
MAX_SLEEP = 1.5  # seconds; keeps long gaps (half-time) short on screen
NAMES = {"home": HOME.name, "away": AWAY.name}

# MCP endpoint at /mcp/ (streamable HTTP). shortcut: DNS-rebinding protection allows localhost only; set the
# public host in transport_security when deploying to Azure.
mcp_app = mcp.streamable_http_app()
app = FastAPI(title="PitchPulse", lifespan=lambda _: mcp.session_manager.run())
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/mcp", mcp_app)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/teams")
def teams():
    return {side: {"name": t.name, "short": t.short,
                   "players": [{"number": n, "name": name, "position": pos} for n, name, pos in t.players]}
            for side, t in (("home", HOME), ("away", AWAY))}


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=200)
    seed: int = Field(7, ge=0, le=MAX_SEED)
    until_event: int = Field(0, ge=0)
    mode: str = Field("casual", pattern="^(casual|analyst)$")
    favorite: str = Field("", pattern="^(home|away)?$")
    language: str = Field("English", pattern="^[A-Za-z ]{2,30}$")
    player: str = ""


@app.post("/api/ask")
async def ask(q: Question, request: Request):
    """Ask the Match: an Agent Framework agent answers from the match MCP server, fact-checked."""
    if q.player and q.player not in ROSTER:
        raise HTTPException(422, "player must be a name from /api/teams")
    prefs = {"mode": q.mode, "favorite": q.favorite, "language": q.language, "player": q.player}
    return await ask_match(q.question, q.seed, q.until_event, prefs, NAMES, f"{request.base_url}mcp/")


@app.get("/api/match/stream")
async def stream(seed: int = Query(7, ge=0, le=MAX_SEED), speed: float = Query(30, gt=0, le=1000), start: int = Query(0, ge=0),
                 mode: str = Query("casual", pattern="^(casual|analyst)$"),
                 favorite: str = Query("", pattern="^(home|away)?$"),
                 language: str = Query("English", pattern="^[A-Za-z ]{2,30}$"),
                 player: str = ""):
    """SSE messages: {event, stats, moments, focus?} per match event, and {commentary} (with
    its timed overlay) when the agents finish a moment. `start` resumes after a pause:
    events up to that id are replayed instantly so the stats stay correct."""
    if player and player not in ROSTER:
        raise HTTPException(422, "player must be a name from /api/teams")
    prefs = {"mode": mode, "favorite": favorite, "language": language, "player": player}

    async def gen():
        stats = MatchStats()
        detector = MomentDetector(NAMES, focus=player or None)
        voiced, pending = asyncio.Queue(), set()
        last_t = None

        async def voice(m):
            await voiced.put({"commentary": {"moment_id": m["id"], **await commentate(m, NAMES, prefs)}})

        def drain():
            while not voiced.empty():
                yield f"data: {json.dumps(voiced.get_nowait())}\n\n"

        for e in simulate(seed=seed):
            stats.update(e)
            moments = detector.check(e, stats)
            if e["id"] <= start:
                continue
            if last_t is not None:
                await asyncio.sleep(min(max(e["t"] - last_t, 0) / speed, MAX_SLEEP))
            last_t = e["t"]
            for m in moments:  # agents run in the background; the match feed never waits for them
                task = asyncio.create_task(voice(m))
                pending.add(task)
                task.add_done_callback(pending.discard)
            msg = {"event": e, "stats": stats.snapshot(), "moments": moments}
            if player:
                msg["focus"] = {"name": player, **stats.players.get(player, {})}
            yield f"data: {json.dumps(msg)}\n\n"
            for msg in drain():
                yield msg
        if pending:
            await asyncio.gather(*pending)
        for msg in drain():
            yield msg
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
