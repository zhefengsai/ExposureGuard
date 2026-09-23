import unittest
from matched_availability import Ledger, fit, replay, D


def event(t, value, route="a", symbol="T"):
    return {"ts": t, "usd": value, "route": route, "symbol": symbol}


class AtomicReplayTests(unittest.TestCase):
    def test_failed_debit_rolls_back_both_buckets(self):
        ledger = Ledger({"policy": "guard", "budget": 100, "caps": {}, "weights": {"a": .5}}, 0)
        before = dict(ledger.level)
        self.assertFalse(ledger.admit(event(0, 61), 0))
        self.assertEqual(before, ledger.level)
        self.assertTrue(ledger.admit(event(0, 60), 0))

    def test_retry_conserves_messages_and_value(self):
        cfg = {"policy": "pooled", "caps": {"root": 10}}
        r = replay([event(0, 10), event(1, 5)], cfg)
        self.assertEqual(r["deferred_on_arrival"], 1)
        self.assertEqual(r["delayed_then_delivered"], 1)
        self.assertEqual(r["delivered_value_fraction"], 1)

    def test_oversize_counted_as_deferral_and_pending(self):
        r = replay([event(0, 11)], {"policy": "pooled", "caps": {"root": 10}})
        self.assertEqual(r["deferred_on_arrival"], 1)
        self.assertEqual(r["undelivered_tail"], 1)
        self.assertEqual(r["immediate_value_fraction"], 0)

    def test_unseen_route_receives_no_independent_capacity(self):
        cfg = fit([event(0, 10)], "per_route", 1)
        r = replay([event(D, 1, route="new")], cfg)
        self.assertEqual(r["undelivered_tail"], 1)

    def test_independent_partition_equals_sum_bound(self):
        cfg = fit([event(0, 10), event(D, 20, route="b", symbol="U")], "per_token", 1)
        self.assertEqual(cfg["budget"], 30)
        ledger = Ledger(cfg, 0)
        self.assertTrue(ledger.admit(event(0, 10), 0))
        self.assertTrue(ledger.admit(event(0, 20, route="b", symbol="U"), 0))
        self.assertFalse(ledger.admit(event(0, 1), 0))


if __name__ == "__main__":
    unittest.main()
