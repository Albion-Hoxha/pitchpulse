"""Match-data MCP server: any MCP client (our Ask-the-Match agent, GitHub Copilot,
a Foundry agent) can query a synthetic match as it stood at any event.

Matches are deterministic per seed, so each call replays the match up to
`until_event` (an event id from the live stream; 0 = the whole match).
"""
import os
from functools import lru_cache

from mcp.server.fastmcp import FastMCP  # mcp 1.x: Agent Framework's MCP client does not speak 2.x yet
from mcp.server.transport_security import TransportSecuritySettings

from .moments import MomentDetector
from .simulator import AWAY, HOME, simulate
from .stats import MatchStats

NAMES = {"home": HOME.name, "away": AWAY.name}
ROSTER = {name: (side, pos) for side, team in (("home", HOME), ("away", AWAY)) for _, name, pos in team.players}
METRICS = ("passes", "passes_completed", "pass_accuracy_pct", "shots", "xg", "goals")
MIN_PASSES_FOR_ACCURACY = 10  # so a 2/2 passer doesn't top the accuracy table
MAX_SEED = 1_000_000
# DNS-rebinding protection: localhost always, plus the public host(s) the app is deployed on.
PUBLIC_HOSTS = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
SECURITY = TransportSecuritySettings(
    allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", *PUBLIC_HOSTS],
    allowed_origins=["http://127.0.0.1:*", "http://localhost:*", *(f"https://{h}" for h in PUBLIC_HOSTS)],
)

mcp = FastMCP(
    "pitchpulse-match",
    instructions="Live statistics for PitchPulse synthetic football matches. Every tool takes the match `seed` "
                 "and `until_event` (event id to replay up to; 0 for the full match).",
    streamable_http_path="/", stateless_http=True, json_response=True, transport_security=SECURITY,
)


@lru_cache(maxsize=64)
def replay(seed, until_event):
    """Rebuild stats and key moments as they stood after event `until_event`."""
    if not (0 <= seed <= MAX_SEED and until_event >= 0):
        raise ValueError("seed must be 0..1,000,000 and until_event >= 0")
    stats, detector, moments = MatchStats(), MomentDetector(NAMES), []
    for e in simulate(seed=seed):
        if until_event and e["id"] > until_event:
            break
        stats.update(e)
        moments += detector.check(e, stats)
    return stats, moments


def _player_row(name, record):
    side, pos = ROSTER[name]
    accuracy = round(100 * record["passes_completed"] / record["passes"]) if record["passes"] else 0
    return {"player": name, "team": NAMES[side], "position": pos, **record, "xg": round(record["xg"], 2),
            "pass_accuracy_pct": accuracy}


@mcp.tool()
def match_snapshot(seed: int = 7, until_event: int = 0) -> dict:
    """Score, clock and live team stats: goals, shots, on target, xG, possession %, passes, pass accuracy %,
    final-third passes, pressures, tackles, fastest shot, momentum (-100 away .. +100 home over the last
    5 minutes) and flow (control vs chaos index, tempo in passes/min, pressing per minute)."""
    snap = replay(seed, until_event)[0].snapshot()
    snap["teams"] = {NAMES[side]: {"side": side, **t} for side, t in snap["teams"].items()}
    return snap


@mcp.tool()
def player_stats(player: str, seed: int = 7, until_event: int = 0) -> dict:
    """One player's numbers so far: passes, passes completed, shots, xG, goals."""
    if player not in ROSTER:
        return {"error": f"unknown player; choose one of: {', '.join(sorted(ROSTER))}"}
    record = replay(seed, until_event)[0].players.get(
        player, {"team": ROSTER[player][0], "passes": 0, "passes_completed": 0, "shots": 0, "xg": 0.0, "goals": 0})
    return _player_row(player, record)


@mcp.tool()
def top_players(metric: str = "xg", limit: int = 3, seed: int = 7, until_event: int = 0) -> list[dict]:
    """Leading players by one metric: passes, passes_completed, pass_accuracy_pct (min. 10 passes), shots, xg
    or goals."""
    if metric not in METRICS:
        return [{"error": f"metric must be one of {', '.join(METRICS)}"}]
    rows = [_player_row(name, record) for name, record in replay(seed, until_event)[0].players.items()]
    if metric == "pass_accuracy_pct":
        rows = [r for r in rows if r["passes"] >= MIN_PASSES_FOR_ACCURACY]
    return sorted(rows, key=lambda r: r[metric], reverse=True)[:max(1, min(limit, 11))]


@mcp.tool()
def key_moments(seed: int = 7, until_event: int = 0) -> list[dict]:
    """Key moments so far, in order: goals, big chances, momentum swings, control/chaos swings, half-time."""
    out = []
    for m in replay(seed, until_event)[1]:
        ev = m["evidence"]
        out.append({"clock": m["clock"], "kind": m["kind"], "team": NAMES[m["team"]], "headline": m["headline"],
                    **{k: ev[k] for k in ("player", "xg", "score", "state", "momentum") if k in ev}})
    return out
