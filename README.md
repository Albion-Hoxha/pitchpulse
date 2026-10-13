# PitchPulse – AI Broadcast Director

AI broadcast control room for football – built for the Microsoft + Premier League *Inside the Game* hackathon.
PitchPulse turns live **synthetic** match events into verified insights, multilingual commentary and broadcast-ready graphics.

## Pipeline

| Stage | Component | Status |
| --- | --- | --- |
| Ingest | `pitchpulse/simulator.py` – synthetic, football-realistic event stream (passes, carries, shots with xG and speed, pressures, tackles, interceptions, goals) | done |
| Interpret | `pitchpulse/stats.py` – **Statistician**: live possession, xG, pass accuracy and difficulty, momentum | done |
| Explain | Analyst + Fact-Checker agents (Microsoft Foundry) | next |
| Render | Director agent + broadcast overlay UI | next |
| Personalize | Narrator agent: analyst / casual mode, favourite club and player, language | next |

All clubs and players are fictional. The simulator is calibrated against typical top-flight averages
(~2.6 goals, ~20 shots, ~1,100 passes and ~77% pass accuracy per match).

## Run

Requires Python 3.11+.

```bash
python -m pitchpulse.simulator --seed 7      # print one match as JSON lines
python -m unittest discover tests            # run the tests
```
