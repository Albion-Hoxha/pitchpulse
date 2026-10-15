import os
import unittest
from unittest.mock import patch

from pitchpulse import agents

from pitchpulse.agents import build_overlay, default_pick, fact_check, template, validate_pick
from pitchpulse.mcp_server import key_moments, match_snapshot, player_stats, top_players
from pitchpulse.moments import MomentDetector
from pitchpulse.simulator import shot_xg, simulate
from pitchpulse.stats import MatchStats


def play(seed):
    stats = MatchStats()
    events = [stats.update(e) for e in simulate(seed=seed)]
    return events, stats.snapshot()


class SimulatorTest(unittest.TestCase):
    def test_same_seed_same_match(self):
        self.assertEqual(list(simulate(seed=1)), list(simulate(seed=1)))

    def test_match_shape(self):
        events, snap = play(7)
        self.assertEqual(events[0]["type"], "kickoff")
        self.assertEqual(events[-1]["type"], "full_time")
        order = [(e["period"], e["t"]) for e in events]  # 2nd half clock restarts at 45:00
        self.assertEqual(order, sorted(order))
        final = events[-1]["score"]
        self.assertEqual(final, {s: snap["teams"][s]["goals"] for s in ("home", "away")})
        self.assertEqual(sum(t["possession_pct"] for t in snap["teams"].values()), 100)
        for e in events:
            for k in ("x", "end_x"):
                if k in e:
                    self.assertTrue(0 <= e[k] <= 105, e)

    def test_xg_is_sane(self):
        self.assertGreater(shot_xg(99, 34), shot_xg(88, 34))  # closer is better
        self.assertGreater(shot_xg(94, 34), shot_xg(94, 10))  # central is better
        self.assertLess(shot_xg(75, 34), 0.05)

    def test_moments_match_the_facts(self):
        stats, det, moments = MatchStats(), MomentDetector({"home": "H", "away": "A"}), []
        for e in simulate(seed=7):
            stats.update(e)
            moments += det.check(e, stats)
        goals = [m for m in moments if m["kind"] == "goal"]
        snap = stats.snapshot()["teams"]
        self.assertEqual(len(goals), snap["home"]["goals"] + snap["away"]["goals"])
        self.assertEqual(goals[-1]["evidence"]["score"], {s: snap[s]["goals"] for s in ("home", "away")})
        self.assertEqual(moments[-1]["kind"], "full_time")
        for m in moments:
            if m["kind"] == "big_chance":
                self.assertGreaterEqual(m["evidence"]["xg"], 0.30)

    def test_fact_checker(self):
        names = {"home": "H", "away": "A"}
        stats, det, moments = MatchStats(), MomentDetector(names), []
        for e in simulate(seed=7):
            stats.update(e)
            moments += det.check(e, stats)
        goal = next(m for m in moments if m["kind"] == "goal")  # Hale, 33:14, xG 0.573, 0-1
        self.assertEqual(fact_check("Hale scores in the 34th minute, a 57% chance, 0-1!", goal), [])
        self.assertEqual(fact_check("Hale's 12th goal of the season, 0-1.", goal), ["12"])
        swing = next(m for m in moments if m["kind"] == "momentum_shift")
        self.assertEqual(fact_check("Mateo Ruiz and Riverside are dominating now!", swing), ["Mateo Ruiz"])
        for m in moments:  # the fallback templates must always pass the Fact-Checker
            self.assertEqual(fact_check(template(m, names), m), [], template(m, names))

    def test_focus_recap_and_director(self):
        names = {"home": "H", "away": "A"}
        stats, det, moments = MatchStats(), MomentDetector(names, focus="Mateo Ruiz"), []
        for e in simulate(seed=7):
            stats.update(e)
            moments += det.check(e, stats)
        focus = [m for m in moments if m["kind"] == "player_focus"]
        self.assertTrue(focus and all(m["evidence"]["player"] == "Mateo Ruiz" for m in focus))
        full = moments[-1]
        self.assertEqual(full["kind"], "full_time")
        self.assertEqual(sum(t["kind"] == "goal" for t in full["evidence"]["timeline"]), sum(full["evidence"]["score"].values()))
        self.assertEqual(fact_check("Hale opened the scoring in the 34th minute.", full), [])
        flow = [m for m in moments if m["kind"] == "flow_shift"]
        self.assertTrue(1 <= len(flow) <= 12, len(flow))
        self.assertTrue(all(m["evidence"]["state"] in ("control", "chaos") for m in flow))
        for m in moments:
            self.assertEqual(fact_check(template(m, names), m), [], template(m, names))
            self.assertTrue(0 <= m["evidence"]["flow"]["chaos"] <= 100)
            for graphic in ("score_update", "stat_compare", "momentum", "flow"):  # any Director choice renders
                overlay = build_overlay({**default_pick(m), "graphic": graphic}, m)
                self.assertEqual((overlay["t"], overlay["clock"]), (m["t"], m["clock"]))
        goal = next(m for m in moments if m["kind"] == "goal")
        self.assertIsNone(validate_pick({"graphic": "fireworks"}, goal))
        self.assertIsNone(validate_pick("not json", goal))
        pick = validate_pick({"graphic": "stat_compare", "stat": "bogus", "title": "Hale nets his 99th", "duration_s": 60}, goal)
        self.assertEqual((pick["stat"], pick["duration_s"]), ("xg", 12))
        self.assertNotIn("99", pick["title"])  # invented number in the title -> headline instead

    def test_mcp_tools(self):
        snap = match_snapshot(7, 0)
        self.assertEqual(snap["teams"]["Northbridge FC"]["goals"], 2)  # seed 7 ends 2-2
        top = top_players("goals", 4, 7, 0)
        self.assertEqual({p["player"] for p in top}, {"Marcus Hale", "Yusuf Okafor", "Jonah Whitley", "Diego Alvarez"})
        early, early_snap = key_moments(7, 700), match_snapshot(7, 700)  # the match as it stood at event 700
        self.assertEqual(sum(m["kind"] == "goal" for m in early), sum(t["goals"] for t in early_snap["teams"].values()))
        self.assertNotIn("full_time", [m["kind"] for m in early])
        acc = top_players("pass_accuracy_pct", 11, 7, 0)
        self.assertEqual([p["pass_accuracy_pct"] for p in acc], sorted((p["pass_accuracy_pct"] for p in acc), reverse=True))
        self.assertTrue(all(p["passes"] >= 10 for p in acc))
        self.assertIn("error", player_stats("Lionel Nobody", 7, 0))
        self.assertIn("error", top_players("salary")[0])
        with self.assertRaises(ValueError):
            match_snapshot(-1, 0)

    def test_keyless_client_builds(self):
        # The deployed app has no key and signs in with its managed identity; that path must import cleanly.
        with patch.dict(os.environ, {"AZURE_OPENAI_ENDPOINT": "https://x.services.ai.azure.com", "AZURE_OPENAI_API_KEY": ""}):
            self.assertIsNotNone(agents._client())

    def test_realistic_over_many_matches(self):
        n, goals, shots, passes, acc = 60, 0, 0, 0, 0
        for seed in range(n):
            _, snap = play(seed)
            for t in snap["teams"].values():
                goals += t["goals"]
                shots += t["shots"]
                passes += t["passes"]
                acc += t["pass_accuracy_pct"]
        # Real top-flight averages: ~2.8 goals, ~25 shots, ~900 passes per match, ~80% accuracy.
        self.assertTrue(1.8 <= goals / n <= 4.0, goals / n)
        self.assertTrue(16 <= shots / n <= 34, shots / n)
        self.assertTrue(600 <= passes / n <= 1200, passes / n)
        self.assertTrue(70 <= acc / (2 * n) <= 88, acc / (2 * n))


if __name__ == "__main__":
    unittest.main()
