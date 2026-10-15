"""AI agents for each key moment, coordinated over the moment's shared evidence:

- Analyst explains why the moment matters,
- Director picks the on-screen graphic and how long it stays (machine-readable overlay),
- Narrator voices it for this viewer (mode, club, favourite player, language),
  or writes the match recap at full-time,
- Fact-Checker rejects any number or player the evidence does not support and
  sends the Narrator back for one rewrite.

Without Azure credentials (or if a call fails) every step falls back to a
deterministic default, so the broadcast never stops.
"""
import asyncio
import json
import logging
import os
import re
from pathlib import Path

from .limits import RateLimiter
from .simulator import AWAY, HOME

LOG = logging.getLogger("pitchpulse.agents")
ENV_FILE = Path(__file__).parent.parent / ".env"
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
ROSTER = {name: team.name for team in (HOME, AWAY) for _, name, _ in team.players}

MODES = {
    "casual": "a casual fan: simple, emotional language, no jargon (say 'chance quality' instead of xG)",
    "analyst": "a data-hungry analyst: precise and tactical, cite stats such as xG, possession and pass accuracy",
}

ANALYST = (
    "You are the Analyst in a football TV control room. Given a key moment and its evidence (live stats), "
    "explain in ONE sentence (max 30 words, English) why it matters right now: scoreline, momentum, chance "
    "quality, control vs chaos (flow.chaos: share of possessions lost), pressing (flow.press_per_min) and tempo "
    "(flow.tempo_ppm, passes per minute). Use only numbers that appear in the evidence."
)

GRAPHICS = ("score_update", "stat_compare", "player_card", "momentum", "flow")
STAT_KEYS = ("possession_pct", "xg", "shots", "on_target", "pass_accuracy_pct", "final_third_passes",
             "pressures", "tackles")
DIRECTOR = (
    "You are the Director in a football TV control room. Pick the ONE on-screen graphic that best supports "
    "this moment. Reply with JSON only: {\"graphic\": one of " + "|".join(GRAPHICS) + ", \"stat\": one of "
    + "|".join(STAT_KEYS) + " (used by stat_compare), \"title\": max 40 characters, \"duration_s\": 4-12}. "
    "Choose the stat that best explains why the moment matters. Use player_card only when the moment names a "
    "player. Put no numbers in the title."
)


def _load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


def _client():
    _load_env()
    endpoint, key = os.getenv("AZURE_OPENAI_ENDPOINT"), os.getenv("AZURE_OPENAI_API_KEY")
    if not endpoint:
        return None
    from openai import AsyncOpenAI  # Azure OpenAI v1 API: no api-version needed

    if not key:  # deployed: Entra ID via the app's managed identity, so no key exists anywhere in the cloud
        from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

        key = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
    return AsyncOpenAI(base_url=endpoint.rstrip("/") + "/openai/v1/", api_key=key)


CLIENT = _client()
MODEL = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
# Global cap on model calls per hour; past it every agent falls back to its template.
LLM_BUDGET = RateLimiter(int(os.getenv("LLM_CALLS_PER_HOUR", "900")), 3600)
ASK_COST = 4  # an Ask-the-Match run makes several model calls (tool choice, answer, maybe a rewrite)


class BudgetExceeded(Exception):
    pass


def _audience(prefs, names):
    fav, player = prefs.get("favorite"), prefs.get("player")
    text = f"Audience: {MODES.get(prefs.get('mode'), MODES['casual'])}."
    if fav in names:
        text += f" The viewer supports {names[fav]}: be warmer about them, honest about the rival."
    if player:
        text += (f" The viewer follows {player} of {ROSTER.get(player, 'their club')}: mention them only when "
                 "this moment's evidence names them.")
    return text


def narrator_prompt(prefs, names):
    return (
        f"You are the live TV commentator. Write the on-air line for this moment in {prefs.get('language', 'English')}: "
        f"one or two short sentences, max 35 words. {_audience(prefs, names)} "
        "Use only numbers from the evidence; never invent players, minutes or stats. Output the line only."
    )


def recap_prompt(prefs, names):
    return (
        f"You are the TV presenter. Write the full-time match recap in {prefs.get('language', 'English')}: "
        f"3 or 4 sentences, max 90 words, covering the result, the key moments in order (from the timeline) and "
        f"why the match went that way (possession vs chances, momentum, how the halves differed – see by_half). "
        f"{_audience(prefs, names)} "
        "Use only numbers from the evidence; never invent players, minutes or stats. Output the recap only."
    )


def _number_sources(moment):
    """Every number the evidence supports (with the roundings a commentator would say), mapped to where it
    comes from: a path into the evidence such as ['teams', 'home', 'possession_pct']."""
    sources = {}  # value -> [(path, how)], how = "exact" | "rounded" | "percent"

    def add(value, path, how="exact"):
        found = sources.setdefault(round(float(value), 2), [])
        if path not in (p for p, _ in found):
            found.append((path, how))

    def walk(v, path):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            for x, how in ((v, "exact"), (abs(v), "exact"), (round(v), "rounded"), (round(v, 1), "rounded"),
                           (round(v, 2), "rounded"), (round(v * 100), "percent")):
                add(x, path, how)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k != "event_id":  # internal id, never a fact for viewers
                    walk(x, path + [k])
        elif isinstance(v, list):
            for i, x in enumerate(v):  # name list items by their player or kind, not their index
                tag = (x.get("player") or f"{x.get('kind', '#')} {x.get('minute', x.get('clock', i))}"
                       if isinstance(x, dict) else str(i))
                walk(x, path + [tag])

    walk(moment["evidence"], [])
    for part in re.findall(r"\d+", moment["clock"]):  # "33:14" may also be called the 34th minute
        add(int(part), ["match clock"])
        add(int(part) + 1, ["match clock"])
    for t in moment["evidence"].get("timeline", []):
        add(t["minute"] + 1, [t.get("player") or t["kind"], "minute"])
    return sources


def _mentioned_players(text):
    return [name for name in ROSTER if re.search(rf"\b{re.escape(name.split()[-1])}\b", text)]


def fact_check(text, moment):
    """Return the numbers and player names in `text` that the moment's evidence does not support."""
    sources = _number_sources(moment)
    bad = [n for n in NUMBER.findall(text) if round(float(n.replace(",", ".")), 2) not in sources]
    evidence = json.dumps(moment["evidence"])
    # a real player mentioned outside the evidence is how wrong attributions creep in
    return bad + [name for name in _mentioned_players(text) if name not in evidence]


PATH_LABELS = {"possession_pct": "possession %", "pass_accuracy_pct": "pass accuracy %", "xg": "xG",
               "speed_kmh": "shot speed (km/h)", "top_shot_kmh": "fastest shot (km/h)", "on_target": "shots on target",
               "final_third_passes": "final-third passes", "press_per_min": "pressing per minute",
               "tempo_ppm": "tempo (passes per minute)", "chaos": "chaos index", "passes_completed": "passes completed",
               "momentum": "momentum (last 5 min)", "teams": "", "snapshot": "", "flow": "", "players": "",
               "moments": "", "player_stats": "", "timeline": "", "by_half": "",
               "first_half": "1st half", "second_half": "2nd half"}


def cite(text, moment, names):
    """The evidence behind each number and player in `text`, for the on-screen 'show evidence' panel.
    A number can match several facts; the likeliest are shown: percentages for "57%", exact values
    before rounded ones."""
    sources = _number_sources(moment)
    labels_for = {**PATH_LABELS, "player_stats": moment["evidence"].get("player", "")}
    percents = set(re.findall(r"(\d+(?:[.,]\d+)?)\s*%", text))
    out = []
    for claim in dict.fromkeys(NUMBER.findall(text)):
        def rank(found):
            path, how = found
            if claim in percents:
                return 0 if how == "percent" or path[-1].endswith("_pct") else 2
            return {"exact": 0, "rounded": 1, "percent": 2}[how]

        found = sorted(sources.get(round(float(claim.replace(",", ".")), 2), []), key=rank)
        best = [f for f in found if rank(f) == rank(found[0])] if found else []
        labels = []
        for path, how in best:
            parts = (labels_for.get(p, names.get(p, p).replace("_", " ")) for p in path)
            label = " · ".join(p for p in parts if p) + {"exact": "", "rounded": " (rounded)", "percent": " (as %)"}[how]
            if label not in labels:
                labels.append(label)
        out += [{"claim": claim, "source": label} for label in labels[:2]]
    out += [{"claim": name, "source": "named in the match data for this moment"}
            for name in _mentioned_players(text)]
    return out


def template(moment, names):
    ev, kind = moment["evidence"], moment["kind"]
    if kind == "goal":
        s = ev["score"]
        return f"GOAL! {ev['player']} scores ({ev['xg']:.2f} xG). {names['home']} {s['home']}–{s['away']} {names['away']}."
    if kind == "big_chance":
        return f"Big chance for {ev['player']} ({ev['xg']:.2f} xG) – {ev['outcome'].replace('_', ' ')}."
    if kind == "momentum_shift":
        return f"{names[moment['team']]} are on top: momentum {abs(ev['momentum'])} in their favour over the last five minutes."
    if kind == "flow_shift":
        press = ev["press_per_min"]
        lead = (f"{names[moment['team']]} have control" if ev["state"] == "control"
                else "Chaos: the ball keeps changing hands")
        return (f"{lead} – chaos index {ev['chaos']}, {ev['tempo_ppm']} passes a minute; pressing "
                f"{names['home']} {press['home']}/min, {names['away']} {press['away']}/min.")
    if kind == "player_focus":
        p = ev["player_stats"]
        return (f"{ev['player']}: {ev['action']}. {p['passes_completed']}/{p['passes']} passes, "
                f"{p['shots']} shots, {p['xg']:.2f} xG so far.")
    s = ev["score"]
    return f"{moment['headline']}: {names['home']} {s['home']}–{s['away']} {names['away']}."


def default_pick(moment):
    graphic = {"goal": "score_update", "big_chance": "player_card", "player_focus": "player_card",
               "momentum_shift": "momentum", "flow_shift": "flow"}.get(moment["kind"], "stat_compare")
    return {"graphic": graphic, "stat": "xg", "title": moment["headline"][:40], "duration_s": 8}


def validate_pick(pick, moment):
    """Director output is untrusted model text: keep only a well-formed, renderable choice."""
    if not isinstance(pick, dict) or pick.get("graphic") not in GRAPHICS:
        return None
    if pick["graphic"] == "player_card" and "player_stats" not in moment["evidence"]:
        return None
    title = str(pick.get("title") or "")[:40]
    duration = pick.get("duration_s")
    return {
        "graphic": pick["graphic"],
        "stat": pick.get("stat") if pick.get("stat") in STAT_KEYS else "xg",
        "title": title if title and not fact_check(title, moment) else moment["headline"][:40],
        "duration_s": min(max(int(duration), 4), 12) if isinstance(duration, (int, float)) else 8,
    }


def build_overlay(pick, moment):
    """A timed, machine-readable overlay a rendering partner can sync to the match feed."""
    ev, teams = moment["evidence"], moment["evidence"]["teams"]
    data = {"score": {s: teams[s]["goals"] for s in ("home", "away")}}
    if pick["graphic"] == "stat_compare":
        data["values"] = {s: teams[s][pick["stat"]] for s in ("home", "away")}
    elif pick["graphic"] == "player_card":
        data["player"] = {"name": ev["player"], "team": moment["team"], **ev["player_stats"]}
    elif pick["graphic"] == "momentum":
        data["momentum"] = ev["momentum"]
    elif pick["graphic"] == "flow":
        data["flow"] = ev["flow"]
    elif pick["graphic"] == "score_update":
        data.update({k: ev[k] for k in ("player", "xg", "speed_kmh") if k in ev})
    return {"moment_id": moment["id"], "t": moment["t"], "period": moment["period"], "clock": moment["clock"],
            **pick, "data": data}


async def _chat(system, payload, json_mode=False):
    if not LLM_BUDGET.allow("global"):
        raise BudgetExceeded("hourly model-call budget used up")
    extra = {"response_format": {"type": "json_object"}} if json_mode else {}
    r = await CLIENT.chat.completions.create(
        model=MODEL, max_completion_tokens=400, **extra,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload)}])
    text = (r.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("empty completion")
    return text


async def _direct(facts, moment):
    try:
        return validate_pick(json.loads(await _chat(DIRECTOR, facts, json_mode=True)), moment) or default_pick(moment)
    except Exception:  # a bad graphic choice must never block the commentary
        return default_pick(moment)


ASK = (
    "You are 'Ask the Match', the TV studio's data assistant. Answer the viewer's question about the live match "
    "using ONLY the match tools, and pass seed={seed} and until_event={until} to every tool call. Answer in "
    "{language}, max 60 words. {audience} For 'right now' questions use the last-5-minute figures (flow.*, "
    "momentum); for one half use match_snapshot's by_half figures; otherwise the match totals. Never predict "
    "results of this or any other match. Before "
    "answering, check every comparison: the higher number must be the one you call higher. If the tools cannot "
    "answer it, say so briefly. Quote numbers exactly as the tools return them; never invent numbers or players."
)


def _tools_used(response):
    """Tool names an agent called: local function calls carry `name`, Foundry's hosted MCP calls `tool_name`."""
    calls = [c for m in getattr(response, "messages", []) for c in getattr(m, "contents", [])
             if getattr(c, "type", "") in ("function_call", "mcp_server_tool_call")]
    return sorted({getattr(c, "name", None) or getattr(c, "tool_name", None) for c in calls} - {None})


async def ask_match(question, seed, until_event, prefs, names, mcp_url):
    """Ask the Match: the published Microsoft Foundry agent answers through the match MCP server (falling back
    to an in-process Agent Framework agent if Foundry is unreachable); the Fact-Checker verifies the answer."""
    from contextlib import AsyncExitStack

    from . import foundry_agent
    from .mcp_server import key_moments, match_snapshot, player_stats

    if CLIENT is None:
        return {"answer": "The AI agents are not configured on this server.", "source": "unavailable", "tools": []}
    if not LLM_BUDGET.allow("global", ASK_COST):
        return {"answer": "The data desk is very busy right now – please ask again in a few minutes.",
                "source": "busy", "tools": []}
    snapshot = match_snapshot(seed, until_event)
    evidence = {"snapshot": snapshot, "moments": key_moments(seed, until_event),
                "players": [player_stats(p, seed, until_event) for p in ROSTER]}
    check = {"evidence": evidence, "clock": snapshot["clock"]}
    language, audience = prefs.get("language", "English"), _audience(prefs, names)
    engine = "foundry" if foundry_agent.PROJECT_ENDPOINT else "local"
    local_agent = None

    async def ask_once(text, stack):
        nonlocal engine, local_agent
        if engine == "foundry":
            try:
                return await foundry_agent.run(foundry_agent.message(text, seed, until_event, language, audience))
            except Exception as exc:
                LOG.warning("Foundry agent failed, using the local agent: %r", exc)
                engine = "local"  # Foundry unreachable: the in-process agent answers instead
        if local_agent is None:
            from agent_framework import Agent, MCPStreamableHTTPTool
            from agent_framework.openai import OpenAIChatCompletionClient

            match_tools = await stack.enter_async_context(MCPStreamableHTTPTool(name="match", url=mcp_url))
            local_agent = Agent(OpenAIChatCompletionClient(model=MODEL, async_client=CLIENT), name="AskTheMatch",
                                instructions=ASK.format(seed=seed, until=until_event, audience=audience,
                                                        language=language),
                                tools=match_tools)
        return await local_agent.run(text)

    try:
        async with AsyncExitStack() as stack:
            response = await ask_once(question, stack)
            answer, tools = response.text.strip(), _tools_used(response)
            bad = fact_check(answer, check)
            if bad:  # one rewrite, as for the Narrator
                response = await ask_once(f"{question}\n\n(Fact-Checker: your previous answer used unsupported "
                                          f"facts {bad}. Answer again using only tool results.)", stack)
                answer, tools = response.text.strip(), sorted(set(tools) | set(_tools_used(response)))
                bad = fact_check(answer, check)
    except Exception as exc:  # the question goes unanswered, the broadcast carries on
        return {"answer": "Sorry, the data desk could not answer that right now.", "source": "error",
                "error": type(exc).__name__, "tools": []}
    if bad or not answer:
        return {"answer": "That answer could not be verified against the match data.", "source": "rejected",
                "rejected": bad, "tools": tools, "engine": engine}
    return {"answer": answer, "source": "ai", "tools": tools, "engine": engine,
            "evidence": cite(answer, check, names)}


async def commentate(moment, names, prefs):
    """Run the agents for one moment: Analyst and Director in parallel, then Narrator + Fact-Checker."""
    line = template(moment, names)
    fallback = {"text": line, "source": "template", "overlay": build_overlay(default_pick(moment), moment),
                "evidence": cite(line, moment, names)}
    if CLIENT is None:
        return fallback
    try:
        facts = {"moment": moment, "teams": names}
        if moment["kind"] == "full_time":
            system, analysis, pick = recap_prompt(prefs, names), None, default_pick(moment)
        else:
            system = narrator_prompt(prefs, names)
            analysis, pick = await asyncio.gather(_chat(ANALYST, facts), _direct(facts, moment))
            if fact_check(analysis, moment):
                analysis = None  # an unverified explanation is worse than none
        text = await _chat(system, {**facts, "analysis": analysis})
        bad = fact_check(text, moment)
        if bad:
            text = await _chat(system, {**facts, "analysis": analysis, "rejected_line": text,
                                        "fact_checker": f"Unsupported facts {bad}. Rewrite using only numbers and players in the evidence."})
            bad = fact_check(text, moment)
        if bad:
            return {**fallback, "rejected": bad}
        return {"text": text, "analysis": analysis, "source": "ai", "overlay": build_overlay(pick, moment),
                "evidence": cite(f"{text} {analysis or ''}", moment, names)}
    except Exception as exc:  # network, quota or content filter: the broadcast must go on
        return {**fallback, "error": type(exc).__name__}
