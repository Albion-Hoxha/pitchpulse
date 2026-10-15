"""Live match server: replays a synthetic match in real time over Server-Sent Events.

Run:  python -m uvicorn pitchpulse.server:app --reload
"""
import asyncio
import json
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agents import ROSTER, ask_match, commentate
from .limits import RateLimiter, client_ip
from .mcp_server import MAX_SEED, mcp
from .moments import MomentDetector
from .simulator import AWAY, HOME, simulate
from .stats import MatchStats

STATIC = Path(__file__).parent.parent / "static"
MAX_SLEEP = 1.5  # seconds; keeps long gaps (half-time) short on screen
NAMES = {"home": HOME.name, "away": AWAY.name}
ASK_LIMIT = RateLimiter(10, 600)  # questions per viewer IP per 10 minutes
MAX_STREAMS_PER_IP = 5  # room for a viewer flipping settings (each change reopens the stream)
OPEN_STREAMS = defaultdict(int)

# MCP endpoint at /mcp/ (streamable HTTP); public hosts come from MCP_ALLOWED_HOSTS.
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
    if not ASK_LIMIT.allow(client_ip(request)):
        raise HTTPException(429, "Too many questions – please wait a few minutes.")
    prefs = {"mode": q.mode, "favorite": q.favorite, "language": q.language, "player": q.player}
    port = request.scope["server"][1]  # the agent reaches our own MCP server directly, not via the public ingress
    return await ask_match(q.question, q.seed, q.until_event, prefs, NAMES, f"http://127.0.0.1:{port}/mcp/")


@app.get("/api/match/stream")
async def stream(request: Request, seed: int = Query(7, ge=0, le=MAX_SEED), speed: float = Query(30, gt=0, le=1000), start: int = Query(0, ge=0),
                 mode: str = Query("casual", pattern="^(casual|analyst)$"),
                 favorite: str = Query("", pattern="^(home|away)?$"),
                 language: str = Query("English", pattern="^[A-Za-z ]{2,30}$"),
                 player: str = ""):
    """SSE messages: {event, stats, moments, focus?} per match event, and {commentary} (with
    its timed overlay) when the agents finish a moment. `start` resumes after a pause:
    events up to that id are replayed instantly so the stats stay correct."""
    if player and player not in ROSTER:
        raise HTTPException(422, "player must be a name from /api/teams")
    ip = client_ip(request)
    if OPEN_STREAMS[ip] >= MAX_STREAMS_PER_IP:
        raise HTTPException(429, "Too many matches open at once from this address.")
    OPEN_STREAMS[ip] += 1
    prefs = {"mode": mode, "favorite": favorite, "language": language, "player": player}

    async def gen():
        try:
            async for chunk in match_feed():
                yield chunk
        finally:  # viewer left or match ended: free the slot and stop paying for unseen commentary
            OPEN_STREAMS[ip] -= 1
            for task in list(pending):
                task.cancel()

    pending = set()

    async def match_feed():
        stats = MatchStats()
        detector = MomentDetector(NAMES, focus=player or None)
        voiced = asyncio.Queue()
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
