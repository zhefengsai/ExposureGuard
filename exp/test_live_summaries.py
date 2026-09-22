import json
import tempfile
import unittest
from pathlib import Path

import egctl
import summarize_live_runs as slr


class LiveSummaryTests(unittest.TestCase):
    def test_trace_hash_covers_actual_training_rows(self):
        rows = [
            {"chain": "ethereum", "route": "a", "t": "2026-01-01T00:00:00",
             "ts": 1.0, "usd": 2.0},
            {"chain": "base", "route": "b", "t": "2026-01-01T00:00:01",
             "ts": 2.0, "usd": 3.0},
        ]
        first = egctl.trace_provenance(rows)
        changed = egctl.trace_provenance(rows + [{
            "chain": "bsc", "route": "c", "t": "2026-01-01T00:00:02",
            "ts": 3.0, "usd": 4.0,
        }])
        reordered = egctl.trace_provenance(list(reversed(rows)))
        self.assertNotEqual(first["trace_sha256"], changed["trace_sha256"])
        self.assertEqual(first["trace_sha256"], reordered["trace_sha256"])
        self.assertEqual(first["trace_records_by_chain"]["ethereum"], 1)

    def test_testnet_summary_detects_duplicate_message(self):
        row = {
            "timestamp": "2026-01-01T00:00:00+00:00", "sequence": 1,
            "message_id": "0x01", "dispatch_tx": "0x02", "process_tx": "0x03",
            "end_to_end_seconds": 1.0, "process_gas": 10,
            "delivered": True, "metered": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
            result = slr.summarize_testnet(path)
        self.assertEqual(result["duplicate_message_ids"], 1)
        self.assertEqual(result["unique_sequences"], 1)


if __name__ == "__main__":
    unittest.main()
