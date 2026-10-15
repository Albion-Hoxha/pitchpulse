"""Key-moment detection: decides WHICH moments matter, with the evidence behind each.

Detection is rule-based and deterministic; the agents only explain, narrate and
stage moments found here, and the Fact-Checker verifies their text against each
moment's `evidence`.
"""
BIG_CHANCE_XG = 0.30
SWING_LEVEL = 50  # momentum must cross from one side's +50 to the other's
KEY_PASS_DIFFICULTY = 7.0
FOCUS_GAP = 240  # match seconds between minor player-focus moments, so they don't flood the screen
FLOW_GAP = 600  # match seconds between control/chaos calls
FLOW_WARMUP = 300  # the 5-minute window needs filling after each kick-off


class MomentDetector:
    def __init__(self, team_names, focus=None):
        self.names = team_names  # {"home": "...", "away": "..."}
        self.focus = focus  # player followed in player-focus mode, if any
        self.dominant = None  # side currently on top of momentum, if any
        self.timeline = []  # compact log of earlier moments, the evidence for the full-time recap
        self.last_focus_t = -FOCUS_GAP
        self.announced_flow, self.last_flow_t = None, -FLOW_GAP

    def check(self, e, stats):
        """Return the moments triggered by event `e` (after `stats` has seen it)."""
        found = []
        snap = None
        kind, side, who = e["type"], e["team"], e.get("player")

        def moment(kind_, headline, **evidence):
            nonlocal snap
            snap = snap or stats.snapshot()
            if evidence.get("player") in stats.players:
                evidence["player_stats"] = dict(stats.players[evidence["player"]], xg=round(stats.players[evidence["player"]]["xg"], 2))
            found.append({"id": f"{e['id']}:{kind_}", "kind": kind_, "t": e["t"], "period": e["period"],
                          "clock": e["clock"], "team": side, "headline": headline,
                          "evidence": {"event_id": e["id"], "momentum": snap["momentum"], **evidence,
                                       "teams": snap["teams"], "flow": snap["flow"]}})

        if kind == "shot" and e["outcome"] == "goal":
            score = {"home": stats.team["home"]["goals"], "away": stats.team["away"]["goals"]}
            moment("goal", f"GOAL – {who} ({self.names[side]})",
                   player=who, xg=e["xg"], speed_kmh=e["speed_kmh"], score=score)
        elif kind == "shot" and e["xg"] >= BIG_CHANCE_XG:
            moment("big_chance", f"Big chance – {who}", player=who, xg=e["xg"], outcome=e["outcome"])
        elif kind == "half_time":
            side = "home"
            moment(kind, "Half-time", score=e["score"])
        elif kind == "full_time":
            side = "home"
            moment(kind, "Full-time", score=e["score"], timeline=self.timeline, by_half=stats.by_half())
        elif self.focus and who == self.focus:
            label = self._focus_label(e)
            if label and (kind == "shot" or e["t"] - self.last_focus_t >= FOCUS_GAP):
                self.last_focus_t = e["t"]
                moment("player_focus", f"{who} – {label}", player=who, action=label,
                       **{k: e[k] for k in ("xg", "difficulty", "length", "speed_kmh") if k in e})

        m = stats.momentum()
        top = "home" if m >= SWING_LEVEL else "away" if m <= -SWING_LEVEL else None
        if top and top != self.dominant:
            if self.dominant is not None:
                side = top
                moment("momentum_shift", f"Momentum swings to {self.names[top]}", momentum=m)
            self.dominant = top

        if e["t"] - stats.period_start >= FLOW_WARMUP:
            fl = stats.flow()
            # announce only real swings (control <-> chaos), not dips back into "balanced" and out again
            if fl["state"] not in ("balanced", self.announced_flow) and e["t"] - self.last_flow_t >= FLOW_GAP:
                passes = fl["completed_passes"]
                side = max(passes, key=passes.get)  # the side passing more is the one in control
                headline = (f"{self.names[side]} take control" if fl["state"] == "control"
                            else "Chaos – the ball keeps changing hands")
                moment("flow_shift", headline, state=fl["state"], chaos=fl["chaos"],
                       tempo_ppm=fl["tempo_ppm"], press_per_min=fl["press_per_min"])
                self.last_flow_t, self.announced_flow = e["t"], fl["state"]

        for f in found:
            if f["kind"] in ("goal", "big_chance", "momentum_shift", "flow_shift", "half_time"):
                ev = f["evidence"]
                self.timeline.append({"minute": int(f["clock"].split(":")[0]), "kind": f["kind"], "team": f["team"],
                                      **{k: ev[k] for k in ("player", "xg", "score", "state") if k in ev}})
        return found

    @staticmethod
    def _focus_label(e):
        if e["type"] == "shot":
            return "shot"
        if e["type"] == "tackle":
            return "wins the ball back"
        if e["type"] == "pass" and e["outcome"] == "complete" and e.get("difficulty", 0) >= KEY_PASS_DIFFICULTY:
            return "key pass"
        return None
