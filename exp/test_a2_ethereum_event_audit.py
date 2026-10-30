import unittest
from unittest.mock import patch

from a2_ethereum_event_audit import TRANSFER, match_erc20, native_amount, receipt_collateral_amount

ROUTE = "a" * 40
RECIPIENT = "b" * 40
TOKEN = "c" * 40


def receive():
    return {"transaction_hash": "0x123", "topics": ["topic", "origin", "0x" + "0" * 24 + RECIPIENT],
            "index": 9}


class EventAmountTests(unittest.TestCase):
    def test_actual_transfer_amount_does_not_use_raw_message_decimals(self):
        transfers = [{"transaction_hash": "0x123", "from": {"hash": "0x" + ROUTE},
                      "to": {"hash": "0x" + RECIPIENT}, "log_index": 8,
                      "total": {"decimals": "6", "value": "2500000"},
                      "token": {"address_hash": "0x" + TOKEN}}]
        row = match_erc20(receive(), transfers, ROUTE)
        self.assertEqual(row["amount_status"], "established")
        self.assertEqual(row["raw_local"], "2500000")
        self.assertEqual(row["price_key"], "ethereum:0x" + TOKEN)

    def test_two_candidate_transfers_remain_unknown(self):
        transfer = {"transaction_hash": "0x123", "from": "0x" + ROUTE, "to": "0x" + RECIPIENT}
        self.assertEqual(match_erc20(receive(), [transfer, transfer], ROUTE)["amount_status"],
                         "ambiguous_or_missing_local_transfer")

    @patch("a2_ethereum_event_audit.get")
    def test_native_transfer_under_reverted_parent_is_not_counted(self, get):
        action = {"from": "0x" + ROUTE, "to": "0x" + RECIPIENT, "value": "0x10", "callType": "call"}
        get.return_value = [{"traceAddress": [0], "error": "Reverted"},
                            {"traceAddress": [0, 0], "action": action},
                            {"traceAddress": [1], "action": dict(action, value="0x20")}]
        row = native_amount(receive(), ROUTE, True)
        self.assertEqual(row["raw_local"], "32")
        self.assertEqual(row["price_key"], "coingecko:ethereum")

    @patch("a2_ethereum_event_audit.get")
    def test_registered_asset_can_transfer_from_custodian(self, get):
        get.side_effect = [{"items": [{"address": {"hash": "0x" + TOKEN},
                                       "topics": [TRANSFER, "0x" + "d" * 64, "0x" + "0" * 24 + RECIPIENT],
                                       "data": "0x2625a0", "index": 12}], "next_page_params": None}, {"decimals": "6"}]
        row = receipt_collateral_amount(receive(), ROUTE, {"collateral": "0x" + TOKEN, "decimals": 6}, True)
        self.assertEqual(row["raw_local"], "2500000")
        self.assertEqual(row["price_key"], "ethereum:0x" + TOKEN)

    @patch("a2_ethereum_event_audit.get")
    def test_vault_underlying_uses_actual_token_and_units(self, get):
        vault = "d" * 40
        get.side_effect = [{"items": [{"address": {"hash": "0x" + TOKEN},
                                       "topics": [TRANSFER, "0x" + "0" * 24 + vault, "0x" + "0" * 24 + RECIPIENT],
                                       "data": "0x2625a0", "index": 12}], "next_page_params": None}, {"decimals": "6"}]
        row = receipt_collateral_amount(receive(), ROUTE, {"collateral": "0x" + vault, "decimals": 18}, True)
        self.assertEqual(row["price_key"], "ethereum:0x" + TOKEN)
        self.assertEqual(row["decimals"], 6)


if __name__ == "__main__":
    unittest.main()
