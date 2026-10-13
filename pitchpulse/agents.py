"""AI agents for each key moment: the Analyst explains it, the Narrator voices it for
this viewer, and the Fact-Checker rejects any number the evidence does not support.

Without Azure credentials (or if a call fails) the pipeline falls back to a
deterministic template, so the broadcast never stops.
"""
import json
import os
import re
from pathlib import Path

ENV_FILE = Path(__file__).parent.parent / ".env"
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

MODES = {
    "casual": "a casual fan: simple, emotional language, no jargon (say 'chance quality' instead of xG)",
    "analyst": "a data-hungry analyst: precise and tactical, cite stats such as xG, possession and pass accuracy",
}

ANALYST = (
    "You are the Analyst in a football TV control room. Given a key moment and its evidence (live stats), "
    "explain in ONE sentence (max 30 words, English) why it matters right now: scoreline, momentum, chance "
    "quality, territory. Use only numbers that appear in the evidence."
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
    if not (endpoint and key):
        return None
    from openai import AsyncOpenAI  # Azure OpenAI v1 API: no api-version needed

    return AsyncOpenAI(base_url=endpoint.rstrip("/") + "/openai/v1/", api_key=key)


CLIENT = _client()
MODEL = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")


def narrator_prompt(prefs, names):
    fav = prefs.get("favorite")
    loyalty = (f" The viewer supports {names[fav]}: be warmer about them, honest about the rival."
               if fav in names else "")
    return (
        f"You are the live TV commentator. Write the on-air line for this moment in {prefs.get('language', 'English')}: "
        f"one or two short sentences, max 35 words. Audience: {MODES.get(prefs.get('mode'), MODES['casual'])}.{loyalty} "
        "Use only numbers from the evidence; never invent players, minutes or stats. Output the line only."
    )


def _allowed_numbers(moment):
    allowed = set()

    def walk(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            for x in (v, abs(v), round(v), round(v, 1), round(v, 2), round(v * 100)):
                allowed.add(round(float(x), 2))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    walk(moment["evidence"])
    for part in re.findall(r"\d+", moment["clock"]):
        allowed.update({float(part), float(part) + 1})  # "33:14" may be called the 34th minute
    return allowed


def fact_check(text, moment):
    """Return the numbers in `text` that the moment's evidence does not support."""
    allowed = _allowed_numbers(moment)
    return [n for n in NUMBER.findall(text) if round(float(n.replace(",", ".")), 2) not in allowed]


def template(moment, names):
    ev, kind = moment["evidence"], moment["kind"]
    if kind == "goal":
        s = ev["score"]
        return f"GOAL! {ev['player']} scores ({ev['xg']:.2f} xG). {names['home']} {s['home']}–{s['away']} {names['away']}."
    if kind == "big_chance":
        return f"Big chance for {ev['player']} ({ev['xg']:.2f} xG) – {ev['outcome'].replace('_', ' ')}."
    if kind == "momentum_shift":
        return f"{names[moment['team']]} are on top: momentum {abs(ev['momentum'])} in their favour over the last five minutes."
    s = ev["score"]
    return f"{moment['headline']}: {names['home']} {s['home']}–{s['away']} {names['away']}."


async def _chat(system, payload):
    r = await CLIENT.chat.completions.create(
        model=MODEL, max_completion_tokens=300,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload)}])
    text = (r.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("empty completion")
    return text


async def commentate(moment, names, prefs):
    """Run Analyst -> Narrator -> Fact-Checker (one rewrite on failure) for one moment."""
    fallback = {"text": template(moment, names), "source": "template"}
    if CLIENT is None:
        return fallback
    try:
        facts = {"moment": moment, "teams": names}
        analysis = await _chat(ANALYST, facts)
        if fact_check(analysis, moment):
            analysis = None  # an unverified explanation is worse than none
        system = narrator_prompt(prefs, names)
        text = await _chat(system, {**facts, "analysis": analysis})
        bad = fact_check(text, moment)
        if bad:
            text = await _chat(system, {**facts, "analysis": analysis, "rejected_line": text,
                                        "fact_checker": f"Unsupported numbers {bad}. Rewrite using only evidence numbers."})
            bad = fact_check(text, moment)
        if bad:
            return {**fallback, "rejected": bad}
        return {"text": text, "analysis": analysis, "source": "ai"}
    except Exception as exc:  # network, quota or content filter: the broadcast must go on
        return {**fallback, "error": type(exc).__name__}
