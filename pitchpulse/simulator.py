"""Synthetic, football-realistic match event generator.

Coordinates follow the common event-data convention: metres on a 105x68 pitch,
normalised so the team performing the action always attacks towards x=105.
"""
import argparse
import json
import math
import random
from dataclasses import dataclass

PITCH_X, PITCH_Y = 105.0, 68.0
GOAL_Y, POST_HALF = PITCH_Y / 2, 3.66
HALF_SECONDS = 45 * 60


@dataclass(frozen=True)
class Team:
    name: str
    short: str
    players: tuple  # (shirt number, name, position) — GK, DF, MF, FW
    press: float  # 0..1, how aggressively they pressure the ball
    directness: float  # 0..1, preference for long forward passes
    finishing: float  # multiplier on scoring chance


# Fictional clubs and players: no real teams, people or trademarks.
HOME = Team("Northbridge FC", "NOR", (
    (1, "Tomas Brandt", "GK"), (2, "Elio Marchetti", "DF"), (4, "Kofi Mensah", "DF"),
    (5, "Arnav Desai", "DF"), (3, "Luka Petrov", "DF"), (6, "Samir Haddad", "MF"),
    (8, "Jonah Whitley", "MF"), (10, "Mateo Ruiz", "MF"), (7, "Dren Krasniqi", "FW"),
    (9, "Oscar Lindqvist", "FW"), (11, "Yusuf Okafor", "FW"),
), press=0.65, directness=0.35, finishing=1.05)

AWAY = Team("Riverside Athletic", "RIV", (
    (1, "Pavel Novak", "GK"), (2, "Callum Reid", "DF"), (5, "Ibrahim Diallo", "DF"),
    (4, "Nikolai Berg", "DF"), (3, "Rafael Costa", "DF"), (6, "Hiroshi Tanaka", "MF"),
    (8, "Liam O'Connell", "MF"), (10, "Ardit Gashi", "MF"), (7, "Sebastien Moreau", "FW"),
    (9, "Marcus Hale", "FW"), (11, "Diego Alvarez", "FW"),
), press=0.40, directness=0.60, finishing=0.95)

# Which positions tend to act in each third (own, middle, final).
ZONE_WEIGHTS = {"GK": (3, 0, 0), "DF": (6, 3, 1), "MF": (3, 6, 4), "FW": (0, 2, 6)}


def shot_xg(x, y):
    """Expected-goals for a shot from (x, y): logistic on distance and goal-mouth angle."""
    dist = math.hypot(PITCH_X - x, GOAL_Y - y)
    dx = max(PITCH_X - x, 0.5)
    angle = abs(math.atan2(GOAL_Y + POST_HALF - y, dx) - math.atan2(GOAL_Y - POST_HALF - y, dx))
    return 1 / (1 + math.exp(-(-0.1 - 0.16 * dist + 1.1 * angle)))


def _clock(t, period):
    base = 0 if period == 1 else HALF_SECONDS
    over = t - base - HALF_SECONDS
    m, s = divmod(int(min(t, base + HALF_SECONDS)), 60)
    return f"{m}:{s:02d}" + (f"+{int(over // 60) + 1}" if over > 0 else "")


def simulate(home=HOME, away=AWAY, seed=None):
    """Yield one match of events as dicts, in time order."""
    rng = random.Random(seed)
    teams = {"home": home, "away": away}
    other = {"home": "away", "away": "home"}
    score = {"home": 0, "away": 0}
    seq = 0
    t, period = 0.0, 1
    poss, x, y = "home", PITCH_X / 2, GOAL_Y

    def player(side, zone):
        squad = teams[side].players
        return rng.choices(squad, weights=[ZONE_WEIGHTS[p[2]][zone] + 0.01 for p in squad])[0][1]

    def ev(kind, side, who=None, **extra):
        nonlocal seq
        seq += 1
        e = {"id": seq, "t": round(t, 1), "clock": _clock(t, period), "period": period,
             "type": kind, "team": side, "possession": poss}
        if who:
            e["player"] = who
        e.update(extra)
        return e

    def zone(px):
        return 0 if px < 35 else 1 if px < 70 else 2

    yield ev("kickoff", poss, player(poss, 1), x=x, y=y)
    end_of_period = HALF_SECONDS + rng.uniform(60, 240)
    while True:
        t += rng.uniform(2.0, 6.0)
        if t >= end_of_period:
            if period == 1:
                yield ev("half_time", "home", score=dict(score))
                period, t = 2, float(HALF_SECONDS)
                end_of_period = 2 * HALF_SECONDS + rng.uniform(120, 360)
                poss, x, y = "away", PITCH_X / 2, GOAL_Y
                yield ev("kickoff", poss, player(poss, 1), x=x, y=y)
                continue
            yield ev("full_time", "home", score=dict(score))
            return

        att, dfn = poss, other[poss]
        A, D = teams[att], teams[dfn]

        # Defensive pressure: high-pressing teams hunt the ball higher up the pitch.
        pressured = rng.random() < 0.12 + 0.45 * D.press * (1 - x / PITCH_X) + 0.12 * (x / PITCH_X)
        if pressured:
            presser = player(dfn, 2 - zone(x))
            yield ev("pressure", dfn, presser, x=round(PITCH_X - x, 1), y=round(PITCH_Y - y, 1))
            if rng.random() < 0.22:
                poss, x, y = dfn, PITCH_X - x, PITCH_Y - y
                yield ev("tackle", dfn, presser, x=round(x, 1), y=round(y, 1), outcome="won")
                continue

        p_shot = 0.0 if x < 80 else 0.03 + 0.15 * ((x - 80) / 25) ** 2 * (1 - abs(y - GOAL_Y) / GOAL_Y)
        if rng.random() < p_shot:
            xg = shot_xg(x, y)
            r = rng.random()
            if r < xg * A.finishing:
                outcome = "goal"
            elif r < xg * A.finishing + 0.30:
                outcome = "saved"
            elif r < xg * A.finishing + 0.55:
                outcome = "blocked"
            else:
                outcome = "off_target"
            speed = round(rng.uniform(70, 125) - 0.4 * (PITCH_X - x), 1)
            yield ev("shot", att, player(att, 2), x=round(x, 1), y=round(y, 1),
                     xg=round(xg, 3), outcome=outcome, speed_kmh=speed)
            if outcome == "goal":
                score[att] += 1
                yield ev("goal", att, score=dict(score))
                poss, x, y = dfn, PITCH_X / 2, GOAL_Y
                yield ev("kickoff", poss, player(poss, 1), x=x, y=y)
            else:
                poss, x, y = dfn, 8.0, GOAL_Y  # save, goal kick or cleared block
            continue

        passer = player(att, zone(x))
        if rng.random() < 0.15:  # ball carry
            nx = min(x + rng.uniform(3, 12), PITCH_X - 10)  # defenders stop carries short of goal
            ny = min(max(y + rng.gauss(0, 4), 1), PITCH_Y - 1)
            yield ev("carry", att, passer, x=round(x, 1), y=round(y, 1), end_x=round(nx, 1), end_y=round(ny, 1))
            x, y = nx, ny
            continue

        nx = min(max(x + rng.gauss(4 + 22 * A.directness, 9), 1), PITCH_X - 4)
        ny = min(max(y + rng.gauss(0, 12), 1), PITCH_Y - 1)
        length = math.hypot(nx - x, ny - y)
        crowded_box = 0.35 if nx > 92 else 0.10 if nx > 85 else 0
        p_ok = 1.02 - 0.007 * length - (0.12 if pressured else 0) - crowded_box
        done = rng.random() < p_ok
        receiver = player(att, zone(nx)) if done else None
        yield ev("pass", att, passer, x=round(x, 1), y=round(y, 1), end_x=round(nx, 1), end_y=round(ny, 1),
                 length=round(length, 1), outcome="complete" if done else "incomplete",
                 receiver=receiver, under_pressure=pressured,
                 speed_kmh=round(25 + 1.1 * length + rng.uniform(-5, 5), 1))
        if done:
            x, y = nx, ny
        else:
            poss, x, y = dfn, PITCH_X - nx, PITCH_Y - ny
            yield ev("interception", dfn, player(dfn, zone(x)), x=round(x, 1), y=round(y, 1))


def main():
    ap = argparse.ArgumentParser(description="Print a synthetic match as JSON lines.")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    for e in simulate(seed=args.seed):
        print(json.dumps(e, ensure_ascii=False))


if __name__ == "__main__":
    main()
