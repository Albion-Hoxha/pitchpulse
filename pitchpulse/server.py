"""Live match server: replays a synthetic match in real time over Server-Sent Events.

Run:  python -m uvicorn pitchpulse.server:app --reload
"""
import asyncio
import json
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import telemetry, voice
from .agents import METRICS, ROSTER, ask_match, commentate
from .limits import RateLimiter, client_ip
from .mcp_server import MAX_SEED, mcp
from .moments import MomentDetector
from .simulator import AWAY, HOME, simulate
from .stats import MatchStats

STATIC = Path(__file__).parent.parent / "static"
MAX_SLEEP = 1.5  # seconds; keeps long gaps (half-time) short on screen
NAMES = {"home": HOME.name, "away": AWAY.name}
METRIC_PATTERN = f"^({'|'.join(METRICS)})?$"  # the viewer's focus metric, or none
ASK_LIMIT = RateLimiter(10, 600)  # questions per viewer IP per 10 minutes
VOICE_LIMIT = RateLimiter(60, 600)  # spoken lines per viewer IP per 10 minutes
FINISH_LIMIT = RateLimiter(30, 600)  # lines finished after a pause, per viewer IP per 10 minutes
MAX_STREAMS_PER_IP = 5  # room for a viewer flipping settings (each change reopens the stream)
OPEN_STREAMS = defaultdict(int)

# MCP endpoint at /mcp/ (streamable HTTP); public hosts come from MCP_ALLOWED_HOSTS.
mcp_app = mcp.streamable_http_app()
app = FastAPI(title="PitchPulse", lifespan=lambda _: mcp.session_manager.run())
telemetry.setup(app)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/mcp", mcp_app)


@app.middleware("http")
async def revalidate_ui(request: Request, call_next):
    """Browsers must re-check the page and its scripts, so viewers get each deploy at once."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/teams")
def teams():
    return {side: {"name": t.name, "short": t.short,
                   "players": [{"number": n, "name": name, "position": pos} for n, name, pos in t.players]}
            for side, t in (("home", HOME), ("away", AWAY))}


class Viewer(BaseModel):
    seed: int = Field(7, ge=0, le=MAX_SEED)
    mode: str = Field("casual", pattern="^(casual|analyst)$")
    favorite: str = Field("", pattern="^(home|away)?$")
    language: str = Field("English", pattern="^[A-Za-z ]{2,30}$")
    player: str = ""
    metric: str = Field("", pattern=METRIC_PATTERN)

    def prefs(self):
        return {"mode": self.mode, "favorite": self.favorite, "language": self.language, "player": self.player,
                "metric": self.metric}


class Question(Viewer):
    question: str = Field(min_length=3, max_length=200)
    until_event: int = Field(0, ge=0)


class Unfinished(Viewer):
    moment_id: str = Field(pattern=r"^\d{1,6}:[a-z_]{1,20}$")


@app.post("/api/commentary")
async def finish_commentary(u: Unfinished, request: Request):
    """Finish a moment's line after the viewer paused: closing the stream cancelled the agents' work on it."""
    if u.player and u.player not in ROSTER:
        raise HTTPException(422, "player must be a name from /api/teams")
    if not FINISH_LIMIT.allow(client_ip(request)):
        raise HTTPException(429, "Too many requests – please wait a few minutes.")
    event_id = int(u.moment_id.split(":")[0])
    stats, detector = MatchStats(), MomentDetector(NAMES, focus=u.player or None)  # same detector as the stream
    for e in simulate(seed=u.seed):
        stats.update(e)
        moment = next((m for m in detector.check(e, stats) if m["id"] == u.moment_id), None)
        if moment or e["id"] >= event_id:
            break
    if not moment:
        raise HTTPException(404, "No such moment in this match.")
    c = await commentate(moment, NAMES, u.prefs())
    return {"moment_id": moment["id"], **c, **speakable(c["text"], c["source"], u.language)}


@app.post("/api/ask")
async def ask(q: Question, request: Request):
    """Ask the Match: an Agent Framework agent answers from the match MCP server, fact-checked."""
    if q.player and q.player not in ROSTER:
        raise HTTPException(422, "player must be a name from /api/teams")
    if not ASK_LIMIT.allow(client_ip(request)):
        raise HTTPException(429, "Too many questions – please wait a few minutes.")
    prefs = q.prefs()
    port = request.scope["server"][1]  # the agent reaches our own MCP server directly, not via the public ingress
    answer = await ask_match(q.question, q.seed, q.until_event, prefs, NAMES, f"http://127.0.0.1:{port}/mcp/")
    return {**answer, **speakable(answer["answer"], answer["source"], q.language)}


def speakable(text, source, language):
    """Voice fields for a line we generated: template lines are English whatever the viewer picked."""
    lang = language if source == "ai" else "English"
    return {"voice_language": lang, "voice_token": voice.token(text, lang)}


class Speech(BaseModel):
    text: str = Field(min_length=1, max_length=voice.MAX_CHARS)
    language: str = Field("English", pattern="^[A-Za-z ]{2,30}$")
    token: str = Field(min_length=64, max_length=64)
    rate: int = Field(0, ge=0, le=50)  # % faster speech so the voice keeps up with a fast-forwarded match


@app.post("/api/voice")
async def say(s: Speech, request: Request):
    """Speak a PitchPulse line with Azure AI Speech; only lines carrying our HMAC token are accepted."""
    if not voice.verify(s.text, s.language, s.token):
        raise HTTPException(403, "Only PitchPulse's own lines can be spoken.")
    if not VOICE_LIMIT.allow(client_ip(request)):
        raise HTTPException(429, "Too many voice requests – please wait a few minutes.")
    try:
        audio = await voice.speak(s.text, s.language, s.rate)
    except Exception as exc:  # the text stays on screen even if the voice is unavailable
        raise HTTPException(502, "Voice is unavailable right now.") from exc
    return Response(audio, media_type="audio/mpeg")


@app.get("/api/match/stream")
async def stream(request: Request, seed: int = Query(7, ge=0, le=MAX_SEED), speed: float = Query(30, gt=0, le=1000), start: int = Query(0, ge=0),
                 mode: str = Query("casual", pattern="^(casual|analyst)$"),
                 favorite: str = Query("", pattern="^(home|away)?$"),
                 language: str = Query("English", pattern="^[A-Za-z ]{2,30}$"),
                 player: str = "", metric: str = Query("", pattern=METRIC_PATTERN)):
    """SSE messages: {event, stats, moments, focus?} per match event, and {commentary} (with
    its timed overlay) when the agents finish a moment. `start` resumes after a pause:
    events up to that id are replayed instantly so the stats stay correct."""
    if player and player not in ROSTER:
        raise HTTPException(422, "player must be a name from /api/teams")
    ip = client_ip(request)
    if OPEN_STREAMS[ip] >= MAX_STREAMS_PER_IP:
        raise HTTPException(429, "Too many matches open at once from this address.")
    OPEN_STREAMS[ip] += 1
    prefs = {"mode": mode, "favorite": favorite, "language": language, "player": player, "metric": metric}

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

        async def narrate(m):
            c = await commentate(m, NAMES, prefs)
            await voiced.put({"commentary": {"moment_id": m["id"], **c, **speakable(c["text"], c["source"], language)}})

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
                task = asyncio.create_task(narrate(m))
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
