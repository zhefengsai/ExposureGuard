import unittest
import random

from a4_optimized_static import arrival_feasible, interval_capacity, optimized_shared, optimized_static
from matched_availability import D


def event(t, value, token="T", route="a"):
    return {"ts": t, "usd": value, "symbol": token, "route": route}


class OptimalStaticTests(unittest.TestCase):
    def test_interval_certificate_for_adjacent_bursts(self):
        rows = [event(0, 10), event(D / 2, 10)]
        cap, witness = interval_capacity(rows)
        self.assertAlmostEqual(cap, 20 / 1.5)
        self.assertEqual((witness["start"], witness["end"]), (0, 1))
        self.assertFalse(arrival_feasible(rows, {"policy": "per_token", "caps": {"T": cap - .001}}))

    def test_coincident_messages_must_sum(self):
        cap, _ = interval_capacity([event(0, 3), event(0, 7)])
        self.assertEqual(cap, 10)

    def test_long_idle_interval_cannot_hide_later_burst(self):
        cap, witness = interval_capacity([event(0, 1), event(10 * D, 12)])
        self.assertEqual(cap, 12)
        self.assertEqual(witness["start"], 1)

    def test_local_minima_form_aggregate_lower_bound(self):
        rows = [event(0, 10), event(D / 2, 10), event(D, 20, "U", "b")]
        cfg = optimized_static(rows)
        self.assertTrue(arrival_feasible(rows, cfg))
        self.assertAlmostEqual(cfg["optimization"]["lower_bound_budget_usd"], 20 / 1.5 + 20)
        self.assertLessEqual(cfg["budget"] - cfg["optimization"]["lower_bound_budget_usd"], .02)

    def test_shared_search_serves_training_and_brackets_boundary(self):
        rows = [event(0, 10), event(D / 2, 10), event(D, 20, "U", "b")]
        cfg = optimized_shared(rows)
        self.assertTrue(arrival_feasible(rows, cfg))
        opt = cfg["optimization"]
        self.assertLess(opt["feasible_upper_budget_usd"] - opt["infeasible_lower_budget_usd"], .02)

    def test_interval_formula_against_independent_bucket_recurrence(self):
        rng = random.Random(724)
        for _ in range(40):
            times = sorted(rng.randrange(0, 4 * int(D)) for _ in range(12))
            rows = [event(t, rng.uniform(.1, 100)) for t in times]
            cap, _ = interval_capacity(rows)
            self.assertTrue(arrival_feasible(rows, {"policy": "per_token", "caps": {"T": cap + .0001}}))
            self.assertFalse(arrival_feasible(rows, {"policy": "per_token", "caps": {"T": cap - .0001}}))


if __name__ == "__main__":
    unittest.main()
