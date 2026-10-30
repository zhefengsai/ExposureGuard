import copy
import unittest

from a4_historical_service import drain_replay, resized
from matched_availability import D, Ledger, replay


def event(t, value, route="a"):
    return {"ts": t, "usd": value, "symbol": "T", "route": route}


def config(alpha=.8):
    return {"policy": "guard", "budget": 100., "caps": {"root": 100.},
            "weights": {"a": 1.}, "alpha": alpha}


class HistoricalServiceTests(unittest.TestCase):
    def test_resizing_does_not_change_source_or_partition(self):
        source = {"policy": "per_token", "budget": 100., "caps": {"T": 25., "U": 75.}}
        before = copy.deepcopy(source)
        resized_cfg = resized(source, 80.)
        self.assertEqual(source, before)
        self.assertEqual(resized_cfg["caps"], {"T": 20., "U": 60.})

    def test_alpha_zero_matches_pooled_clean_service(self):
        rows = [event(0, 90), event(600, 5, "new"), event(D, 60)]
        self.assertEqual(replay(rows, config(0.)),
                         replay(rows, {"policy": "pooled", "caps": {"root": 100.}}))

    def test_explicit_default_matches_legacy_default(self):
        cfg = config()
        old = copy.deepcopy(cfg)
        old.pop("alpha")
        rows = [event(0, 95), event(600, 10), event(D, 80)]
        self.assertEqual(replay(rows, cfg), replay(rows, old))

    def test_invalid_alpha_is_rejected(self):
        with self.assertRaises(ValueError):
            Ledger(config(1.1), 0)

    def test_attacker_cannot_spend_reserved_capacity(self):
        rows = [event(0, 70), event(D, 70)]
        reserved, pooled = drain_replay(rows, config(.8)), drain_replay(rows, config(0.))
        self.assertEqual(reserved["immediate_messages"], 2)
        self.assertEqual(pooled["immediate_messages"], 0)
        self.assertLessEqual(reserved["attacker_admitted_usd"], 40.000001)

    def test_rejected_message_has_no_partial_debit(self):
        cfg = config(.4)
        ledger = Ledger(cfg, 0)
        before = copy.deepcopy(ledger.level)
        self.assertFalse(ledger.admit(event(0, 101), 0))
        self.assertEqual(before, ledger.level)
        self.assertTrue(ledger.admit(event(0, 100), 0))

    def test_full_reservation_blocks_zero_history_attacker(self):
        result = drain_replay([event(0, 80), event(D, 80)], config(1.))
        self.assertEqual(result["attacker_admitted_usd"], 0)
        self.assertEqual(result["immediate_messages"], 2)

    def test_oversized_legitimate_message_is_pending(self):
        result = drain_replay([event(0, 101)], config(.8))
        self.assertEqual(result["undelivered_tail"], 1)
        self.assertEqual(result["immediate_messages"], 0)


if __name__ == "__main__":
    unittest.main()
