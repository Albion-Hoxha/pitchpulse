"""Key-moment detection: decides WHICH moments matter, with the evidence behind each.

Detection is rule-based and deterministic; the Analyst and Narrator agents only
explain and narrate moments found here, and the Fact-Checker verifies their text
against each moment's `evidence`.
"""
BIG_CHANCE_XG = 0.30
SWING_LEVEL = 50  # momentum must cross from one side's +50 to the other's


class MomentDetector:
    def __init__(self, team_names):
        self.names = team_names  # {"home": "...", "away": "..."}
        self.dominant = None  # side currently on top of momentum, if any

    def check(self, e, stats):
        """Return the moments triggered by event `e` (after `stats` has seen it)."""
        found = []
        snap = None
        kind, side = e["type"], e["team"]

        def moment(kind_, headline, **evidence):
            nonlocal snap
            snap = snap or stats.snapshot()
            found.append({"id": f"{e['id']}:{kind_}", "kind": kind_, "clock": e["clock"], "team": side,
                          "headline": headline,
                          "evidence": {"event_id": e["id"], **evidence, "teams": snap["teams"]}})

        if kind == "shot" and e["outcome"] == "goal":
            score = stats.team["home"]["goals"], stats.team["away"]["goals"]
            moment("goal", f"GOAL – {e['player']} ({self.names[side]})",
                   player=e["player"], xg=e["xg"], speed_kmh=e["speed_kmh"],
                   score={"home": score[0], "away": score[1]})
        elif kind == "shot" and e["xg"] >= BIG_CHANCE_XG:
            moment("big_chance", f"Big chance – {e['player']}", player=e["player"],
                   xg=e["xg"], outcome=e["outcome"])
        elif kind in ("half_time", "full_time"):
            side = "home"
            moment(kind, "Half-time" if kind == "half_time" else "Full-time", score=e["score"])

        m = stats.momentum()
        top = "home" if m >= SWING_LEVEL else "away" if m <= -SWING_LEVEL else None
        if top and top != self.dominant:
            if self.dominant is not None:
                side = top
                moment("momentum_shift", f"Momentum swings to {self.names[top]}", momentum=m)
            self.dominant = top
        return found
