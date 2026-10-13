import unittest

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
