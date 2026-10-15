# PitchPulse – AI Broadcast Director

AI broadcast control room for football – built for the Microsoft + Premier League *Inside the Game* hackathon.
PitchPulse turns live **synthetic** match events into verified insights, multilingual commentary and broadcast-ready graphics.

## Pipeline

| Stage | Component | Status |
| --- | --- | --- |
| Ingest | `pitchpulse/simulator.py` – synthetic, football-realistic event stream (passes, carries, shots with xG and speed, pressures, tackles, interceptions, goals) | done |
| Interpret | `pitchpulse/stats.py` – **Statistician**: live possession, xG, pass accuracy and difficulty, momentum, and **control vs chaos** (share of possessions lost, tempo, pressing per minute over the last 5 minutes) | done |
| Explain | `pitchpulse/moments.py` – rule-based key-moment detection with evidence; `pitchpulse/agents.py` – **Analyst** explains why it matters, **Fact-Checker** rejects any number or player not in the evidence | done |
| Render | **Director** agent picks the graphic and its on-screen time → timed, machine-readable overlay JSON; `static/` – live broadcast screen (scorebug, pitch, momentum, lower-thirds, graphics, stats) | done |
| Personalize | **Narrator** agent: casual / analyst mode, club supported, followed player (player cam), 7 languages; full-time **match recap** | done |

Agents run on Azure OpenAI in Microsoft Foundry (`gpt-4.1-mini`). Without credentials, or if a call fails, every step
falls back to a deterministic template, so the broadcast never stops.

### Cost and abuse protection

The public demo caps model usage: 10 questions per viewer IP per 10 minutes, 5 open matches per IP, and a global
budget of model calls per hour (`LLM_CALLS_PER_HOUR`, default 900). Past the budget the agents fall back to their
templates, so the broadcast keeps running without spending more credit.

### Ask the Match (Microsoft Agent Framework + MCP)

Viewers can ask questions such as *"Which team is pressing harder right now?"*. An **Agent Framework** agent answers
by calling the **match MCP server** (`pitchpulse/mcp_server.py`, streamable HTTP at `/mcp/`), and the Fact-Checker
verifies the answer against the same data. MCP tools: `match_snapshot`, `player_stats`, `top_players`, `key_moments` –
each takes the match `seed` and `until_event`, so any MCP client (GitHub Copilot, a Foundry agent) can query the
match exactly as it stood at any moment.

All clubs and players are fictional. The simulator is calibrated against typical top-flight averages
(~2.6 goals, ~20 shots, ~1,100 passes and ~77% pass accuracy per match).

## Run

Requires Python 3.11+.

```bash
python -m venv .venv && .venv/Scripts/activate   # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn pitchpulse.server:app          # open http://localhost:8000 and press Kick off
python -m pitchpulse.simulator --seed 7          # print one match as JSON lines
python -m unittest discover tests                # run the tests
```

To enable the AI agents, copy `.env.example` to `.env` and fill in your Foundry endpoint, key and deployment.

The live screen streams `GET /api/match/stream?seed=7&speed=30&mode=casual&favorite=home&player=Mateo%20Ruiz&language=Albanian`
(Server-Sent Events): one `{event, stats, moments, focus?}` message per match event, and a `{commentary}` message
when the agents finish a moment. Each commentary carries an `overlay` – `{t, period, clock, graphic, title, duration_s, data}` –
that a rendering partner can sync to the match feed.
