import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import prospective_shadow as ps


class ProspectiveShadowStateTests(unittest.TestCase):
    def test_merge_preserves_other_chain_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "state.json"
            lock_path = root / "state.lock"
            state_path.write_text(json.dumps({
                "schema": 1,
                "first_config_date": None,
                "chains": {c: {"last_finalized_block": i}
                           for i, c in enumerate(ps.egctl.CHAINS)},
            }))
            with patch.object(ps, "STATE", state_path), \
                    patch.object(ps, "STATE_LOCK", lock_path):
                ps.merge_chain_state("bsc", {"last_finalized_block": 99})
            merged = json.loads(state_path.read_text())
            self.assertEqual(merged["chains"]["bsc"]["last_finalized_block"], 99)
            self.assertEqual(merged["chains"]["ethereum"]["last_finalized_block"], 0)

    def test_freshness_rejects_one_stale_chain(self):
        fresh = ps.utc_now().isoformat()
        stale = (ps.utc_now() - timedelta(hours=4)).isoformat()
        state = {"chains": {c: {"updated_at": fresh} for c in ps.egctl.CHAINS}}
        state["chains"]["bsc"]["updated_at"] = stale
        with self.assertRaisesRegex(RuntimeError, "bsc"):
            ps.assert_fresh_chains(state, 3.0)

    def test_freshness_accepts_complete_recent_cursors(self):
        updated = ps.utc_now().isoformat()
        state = {"chains": {c: {"updated_at": updated} for c in ps.egctl.CHAINS}}
        ages = ps.assert_fresh_chains(state, 3.0)
        self.assertEqual(set(ages), set(ps.egctl.CHAINS))


if __name__ == "__main__":
    unittest.main()
