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
import os
import re
from pathlib import Path

from .simulator import AWAY, HOME

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
        f"why the match went that way (possession vs chances, momentum). {_audience(prefs, names)} "
        "Use only numbers from the evidence; never invent players, minutes or stats. Output the recap only."
    )


def _allowed_numbers(moment):
    allowed = set()

    def walk(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            for x in (v, abs(v), round(v), round(v, 1), round(v, 2), round(v * 100)):
                allowed.add(round(float(x), 2))
        elif isinstance(v, (dict, list)):
            for x in v.values() if isinstance(v, dict) else v:
                walk(x)

    walk(moment["evidence"])
    minutes = [int(p) for p in re.findall(r"\d+", moment["clock"])]
    minutes += [t["minute"] for t in moment["evidence"].get("timeline", [])]
    for m in minutes:
        allowed.update({float(m), float(m + 1)})  # "33:14" may be called the 34th minute
    return allowed


def fact_check(text, moment):
    """Return the numbers and player names in `text` that the moment's evidence does not support."""
    allowed = _allowed_numbers(moment)
    bad = [n for n in NUMBER.findall(text) if round(float(n.replace(",", ".")), 2) not in allowed]
    evidence = json.dumps(moment["evidence"])
    for name in ROSTER:  # a real player mentioned outside the evidence is how wrong attributions creep in
        if name not in evidence and re.search(rf"\b{re.escape(name.split()[-1])}\b", text):
            bad.append(name)
    return bad


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
    "momentum), otherwise the match totals. Before answering, check every comparison: the higher number must be "
    "the one you call higher. If the tools cannot answer it, say so briefly. Quote numbers exactly as the tools "
    "return them; never invent numbers or players."
)


def _tools_used(response):
    return sorted({c.name for m in getattr(response, "messages", []) for c in getattr(m, "contents", [])
                   if getattr(c, "type", "") == "function_call" and getattr(c, "name", None)})


async def ask_match(question, seed, until_event, prefs, names, mcp_url):
    """Ask-the-Match agent (Microsoft Agent Framework) that queries the match MCP server, then the
    Fact-Checker verifies its answer against the same match data."""
    from .mcp_server import key_moments, match_snapshot, player_stats

    if CLIENT is None:
        return {"answer": "The AI agents are not configured on this server.", "source": "unavailable", "tools": []}
    snapshot = match_snapshot(seed, until_event)
    evidence = {"snapshot": snapshot, "moments": key_moments(seed, until_event),
                "players": [player_stats(p, seed, until_event) for p in ROSTER]}
    check = {"evidence": evidence, "clock": snapshot["clock"]}
    try:
        from agent_framework import Agent, MCPStreamableHTTPTool
        from agent_framework.openai import OpenAIChatCompletionClient

        async with MCPStreamableHTTPTool(name="match", url=mcp_url) as match_tools:
            agent = Agent(OpenAIChatCompletionClient(model=MODEL, async_client=CLIENT), name="AskTheMatch",
                          instructions=ASK.format(seed=seed, until=until_event, audience=_audience(prefs, names),
                                                  language=prefs.get("language", "English")),
                          tools=match_tools)
            response = await agent.run(question)
            answer, tools = response.text.strip(), _tools_used(response)
            bad = fact_check(answer, check)
            if bad:  # one rewrite, as for the Narrator
                response = await agent.run(f"{question}\n\n(Fact-Checker: your previous answer used unsupported "
                                           f"facts {bad}. Answer again using only tool results.)")
                answer, tools = response.text.strip(), sorted(set(tools) | set(_tools_used(response)))
                bad = fact_check(answer, check)
    except Exception as exc:  # the question goes unanswered, the broadcast carries on
        return {"answer": "Sorry, the data desk could not answer that right now.", "source": "error",
                "error": type(exc).__name__, "tools": []}
    if bad or not answer:
        return {"answer": "That answer could not be verified against the match data.", "source": "rejected",
                "rejected": bad, "tools": tools}
    return {"answer": answer, "source": "ai", "tools": tools}


async def commentate(moment, names, prefs):
    """Run the agents for one moment: Analyst and Director in parallel, then Narrator + Fact-Checker."""
    fallback = {"text": template(moment, names), "source": "template",
                "overlay": build_overlay(default_pick(moment), moment)}
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
        return {"text": text, "analysis": analysis, "source": "ai", "overlay": build_overlay(pick, moment)}
    except Exception as exc:  # network, quota or content filter: the broadcast must go on
        return {**fallback, "error": type(exc).__name__}
