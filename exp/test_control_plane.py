import copy
import unittest
from datetime import datetime, timezone

import egctl


class ControlPlaneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = egctl.load_events()
        cls.cfg = egctl.recommend(cls.rows, alpha=0.8, headroom=1.07,
                                  lookback_days=60)
        cls.now = datetime.fromisoformat(cls.cfg["generated_at"]).timestamp()

    def test_recommendation_audits_cleanly(self):
        self.assertTrue(egctl.audit(self.cfg, max_age_days=30,
                                    now=self.now)["ok"])

    def test_overweight_configuration_rejected(self):
        bad = copy.deepcopy(self.cfg)
        c = bad["chains"]["ethereum"]
        first = next(iter(c["floor_weights"]))
        c["floor_weights"][first] += 0.1
        self.assertFalse(egctl.audit(bad, max_age_days=30,
                                     now=self.now)["ok"])

    def test_budget_below_peak_rejected(self):
        bad = copy.deepcopy(self.cfg)
        bad["chains"]["ethereum"]["budget_usd_per_day"] = 0
        bad["total_budget_usd_per_day"] = sum(
            x["budget_usd_per_day"] for x in bad["chains"].values())
        self.assertFalse(egctl.audit(bad, max_age_days=30,
                                     now=self.now)["ok"])

    def test_stale_configuration_rejected(self):
        self.assertFalse(egctl.audit(self.cfg, max_age_days=30,
                                     now=self.now + 31 * egctl.DAY)["ok"])

    def test_shadow_is_explicitly_not_live(self):
        out = egctl.shadow(self.rows, lookback=60, alpha=0.8, headroom=1.07)
        self.assertEqual(out["mode"], "historical_daily_shadow_replay")
        self.assertFalse(out["live_deployment"])
        self.assertGreater(out["messages"], 0)

    def deployment_fixture(self):
        stamp = datetime.fromtimestamp(self.now, timezone.utc).isoformat()
        chains = {}
        for chain, cfg in self.cfg["chains"].items():
            routes = list(cfg["floor_weights"])
            chains[chain] = {
                "guard_installed": True,
                "aggregation_threshold": 2,
                "aggregation_module_count": 2,
                "covered_routes": routes,
                "recipient_policy": {
                    r: {"classification": "metered", "price_usd": 1.25,
                        "reference_upper_usd": 1.0,
                        "price_observed_at": stamp} for r in routes
                },
            }
        return {"observed_at": stamp, "chains": chains}

    def test_operational_snapshot_audits_cleanly(self):
        self.assertTrue(egctl.audit(
            self.cfg, max_age_days=30, now=self.now,
            deployment=self.deployment_fixture(), require_operational=True)["ok"])

    def test_missing_operational_snapshot_rejected_when_required(self):
        self.assertFalse(egctl.audit(
            self.cfg, max_age_days=30, now=self.now,
            require_operational=True)["ok"])

    def test_coverage_bypass_rejected(self):
        snap = self.deployment_fixture()
        snap["chains"]["ethereum"]["covered_routes"].pop()
        self.assertFalse(egctl.audit(
            self.cfg, max_age_days=30, now=self.now,
            deployment=snap, require_operational=True)["ok"])

    def test_unsafe_aggregation_threshold_rejected(self):
        snap = self.deployment_fixture()
        snap["chains"]["base"]["aggregation_threshold"] = 1
        self.assertFalse(egctl.audit(
            self.cfg, max_age_days=30, now=self.now,
            deployment=snap, require_operational=True)["ok"])

    def test_stale_or_underpriced_asset_rejected(self):
        snap = self.deployment_fixture()
        route = next(iter(snap["chains"]["arbitrum"]["recipient_policy"]))
        snap["chains"]["arbitrum"]["recipient_policy"][route]["price_usd"] = 0.5
        self.assertFalse(egctl.audit(
            self.cfg, max_age_days=30, now=self.now,
            deployment=snap, require_operational=True)["ok"])


if __name__ == "__main__":
    unittest.main()
