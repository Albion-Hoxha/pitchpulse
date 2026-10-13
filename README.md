# PitchPulse – AI Broadcast Director

AI broadcast control room for football – built for the Microsoft + Premier League *Inside the Game* hackathon.
PitchPulse turns live **synthetic** match events into verified insights, multilingual commentary and broadcast-ready graphics.

## Pipeline

| Stage | Component | Status |
| --- | --- | --- |
| Ingest | `pitchpulse/simulator.py` – synthetic, football-realistic event stream (passes, carries, shots with xG and speed, pressures, tackles, interceptions, goals) | done |
| Interpret | `pitchpulse/stats.py` – **Statistician**: live possession, xG, pass accuracy and difficulty, momentum | done |
| Explain | `pitchpulse/moments.py` – rule-based key-moment detection with evidence; Analyst + Fact-Checker agents (Microsoft Foundry) | detection done, agents next |
| Render | `static/` – live broadcast screen (scorebug, pitch, momentum, lower-thirds, stats); Director agent | screen done, agent next |
| Personalize | Narrator agent: analyst / casual mode, favourite club and player, language | next |

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

The live screen streams `GET /api/match/stream?seed=7&speed=30` (Server-Sent Events: one `{event, stats, moments}` message per match event).
