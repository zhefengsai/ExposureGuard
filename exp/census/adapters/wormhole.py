"""Wormhole census from guardian-set + custody pilot artifacts."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
PILOT = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(PILOT, "data")


def _load(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run() -> dict[str, Any]:
    custody = _load(os.path.join(DATA, "wh_custody_multi.json"))
    sep = _load(os.path.join(PILOT, "separability.json"))

    verified_strict = custody["verified_usd_strict"]
    verified_loose = custody["verified_usd_loose"]

    record: dict[str, Any] = {
        "stack": "wormhole",
        "chain": "multi",
        "chain_id": 1,
        "block": sep.get("block"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "measurement_depth": "imported",
        "population": {
            "type": "token_bridge_transfer",
            "count": sum(c["holdings"] for c in custody["verified"].values()),
            "source": "data/wh_custody_multi.json (5 EVM chains enumerated)",
            "note": "Population count = distinct ERC-20 holdings across bridge contracts",
        },
        "shared_verification_root": {
            "present": True,
            "mode": "mandatory_stack_wide",
            "roots": [
                {
                    "id": "canonical_guardian_set",
                    "label": "13-of-19 guardian quorum (hash 885b7752ebedd5e3)",
                    "members": 1,
                    "share": 1.0,
                }
            ],
        },
        "silent_inheritance": {
            "applicable": False,
            "predicate": "N/A — all transfers verify same guardian set by architecture",
            "count": None,
            "share": None,
            "note": "Counts as mandatory sharing for pattern P",
        },
        "controls": [
            {
                "name": "GuardianGovernor",
                "placement": "root_side",
                "scope": "per_lane",
                "automatic": True,
                "on_path_under_Fs": False,
                "active": True,
                "note": "Off-path at guardian; destination accepts any quorum VAA",
            },
            {
                "name": "ManualPause",
                "placement": "destination",
                "scope": "stack_wide",
                "automatic": False,
                "on_path_under_Fs": True,
                "active": False,
                "note": "No always-on destination automatic per-root meter in measurement",
            },
        ],
        "detection_gap": {
            "bound": "unbounded",
            "dest_auto_per_root_bound": False,
            "pause_only": True,
            "formula": "Under quorum compromise: Governor bypassed; admit ≤ verified custody",
            "note": "Governor threshold is root-side and is therefore off the destination path after guardian-root compromise",
        },
        "separability": "NON-SEPARABLE",
        "value_at_risk_usd": {
            "strict": verified_strict,
            "loose": verified_loose,
            "source": "data/wh_custody_multi.json",
            "note": f"DefiLlama cross-check total ${custody.get('llama_total', 0):,.0f}",
        },
        "evidence": [
            "data/wh_custody_multi.json",
            "exp/census/data/census_wormhole.json",
        ],
        "coverage": {
            "queried": len(custody["verified"]),
            "resolved": len(custody["verified"]),
            "failed": 0,
            "failed_share": 0.0,
        },
        "extras": {
            "chains_verified": list(custody["verified"].keys()),
            "unverified_usd": custody.get("unverified_usd"),
        },
    }
    record["pattern_match"] = {
        "SR": True,
        "SI_or_mandatory": True,
        "no_dest_auto_per_root_bound": True,
    }
    return record


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
