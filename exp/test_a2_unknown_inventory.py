import unittest

from a2_unknown_inventory import inventory


def event(route, status, originally_priced=False, symbol="T", price_key=None):
    return {"route": route, "status": status, "originally_priced": originally_priced,
            "symbol": symbol, "price_key": price_key}


class UnknownInventoryTests(unittest.TestCase):
    def test_unresolved_cohorts_are_separate_and_counted_once(self):
        rows = [event("a", "historical_quote_unavailable"),
                event("a", "historical_quote_unavailable", True),
                event("b", "ambiguous_or_missing_receive"),
                event("c", "recovered")]
        result = inventory(rows)
        self.assertEqual(result["groups"]["all_unresolved"]["messages"], 3)
        self.assertEqual(result["groups"]["originally_omitted"]["messages"], 2)
        self.assertEqual(result["groups"]["originally_priced"]["messages"], 1)
        self.assertEqual(sum(r["messages"] for r in result["routes"]), 3)

    def test_identical_symbols_do_not_merge_different_contracts(self):
        result = inventory([event("a", "historical_quote_unavailable", price_key="ethereum:0x1"),
                            event("b", "historical_quote_unavailable", price_key="ethereum:0x2")])
        self.assertEqual(len(result["routes"]), 2)
        self.assertEqual({r["price_key"] for r in result["routes"]}, {"ethereum:0x1", "ethereum:0x2"})

    def test_unknown_value_is_not_assigned_zero(self):
        result = inventory([event("a", "ambiguous_or_missing_receive")])
        self.assertFalse(any("usd" in k for k in result["routes"][0]))
        self.assertIsNone(result["routes"][0]["price_key"])


if __name__ == "__main__":
    unittest.main()
