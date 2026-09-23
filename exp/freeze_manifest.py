#!/usr/bin/env python3
"""Create/verify SHA-256 inventory for paper-critical frozen artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
MANIFEST = DATA / "experiment_manifest.json"
FILES = [
    "events_multi.json", "sim3.json", "sim_results.json", "oos_calibrate.json",
    "controller_config.json", "shadow_controller.json",
    "robustness_sensitivity.json", "calibration_compare.json",
    "pricing_coverage.json",
    "gas_deployed.json", "scaling.json",
    "fault_matrix.json", "baselines.json", "partition_cost.json",
    "owner_independence.json", "audit.json",
    "layerzero_feasibility.json", "testnet_deployment.json",
    "testnet_fault_sequence.json",
    "detector_baseline.json", "synth_generalization.json",
    "bootstrap_policy.json", "capacity_recovery.json",
    "peak_bootstrap.json", "census_evidence.json",
    "price_coverage_curve.json", "burst_envelope.json",
    "pertoken_baseline.json", "matched_availability.json",
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot() -> dict:
    missing = [name for name in FILES if not (DATA / name).exists()]
    if missing:
        raise SystemExit(f"missing manifest inputs: {', '.join(missing)}")
    return {"schema": 1, "files": {name: digest(DATA / name) for name in FILES}}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--verify", action="store_true")
    args = p.parse_args()
    current = snapshot()
    if args.verify:
        expected = json.loads(MANIFEST.read_text())
        if current != expected:
            for name, got in current["files"].items():
                want = expected.get("files", {}).get(name)
                if got != want:
                    print(f"MISMATCH {name}: expected={want} got={got}")
            raise SystemExit(1)
        print(f"OK: {len(current['files'])} frozen artifacts")
    else:
        MANIFEST.write_text(json.dumps(current, indent=2) + "\n")
        print(f"wrote {MANIFEST} ({len(current['files'])} files)")


if __name__ == "__main__":
    main()
