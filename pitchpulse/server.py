"""Live match server: replays a synthetic match in real time over Server-Sent Events.

Run:  python -m uvicorn pitchpulse.server:app --reload
"""
import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agents import commentate
from .moments import MomentDetector
from .simulator import AWAY, HOME, simulate
from .stats import MatchStats

STATIC = Path(__file__).parent.parent / "static"
MAX_SLEEP = 1.5  # seconds; keeps long gaps (half-time) short on screen
NAMES = {"home": HOME.name, "away": AWAY.name}

app = FastAPI(title="PitchPulse")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/teams")
def teams():
    return {side: {"name": t.name, "short": t.short,
                   "players": [{"number": n, "name": name, "position": pos} for n, name, pos in t.players]}
            for side, t in (("home", HOME), ("away", AWAY))}


@app.get("/api/match/stream")
async def stream(seed: int = 7, speed: float = Query(30, gt=0, le=1000), start: int = Query(0, ge=0),
                 mode: str = Query("casual", pattern="^(casual|analyst)$"),
                 favorite: str = Query("", pattern="^(home|away)?$"),
                 language: str = Query("English", max_length=30)):
    """SSE messages: {event, stats, moments} per match event, and {commentary} when the
    agents finish voicing a moment. `start` resumes after a pause: events up to that id
    are replayed instantly so the stats stay correct."""
    prefs = {"mode": mode, "favorite": favorite, "language": language}

    async def gen():
        stats = MatchStats()
        detector = MomentDetector(NAMES)
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
            yield f"data: {json.dumps({'event': e, 'stats': stats.snapshot(), 'moments': moments})}\n\n"
            for msg in drain():
                yield msg
        if pending:
            await asyncio.gather(*pending)
        for msg in drain():
            yield msg
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
