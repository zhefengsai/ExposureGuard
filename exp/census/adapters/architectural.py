"""Architectural census rows for stacks without on-chain ISM-style census."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

PILOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_sep() -> dict[str, Any]:
    with open(os.path.join(PILOT, "separability.json"), encoding="utf-8") as f:
        return json.load(f)


CURATED: dict[str, dict[str, Any]] = {
    "axelar": {
        "population": {"type": "gateway_gmp", "count": 1, "source": "architecture"},
        "shared_verification_root": {
            "present": True,
            "mode": "gateway_inline",
            "roots": [{"id": "axelar_validator_set", "label": "Gateway inline validator multisig", "members": 1, "share": 1.0}],
        },
        "silent_inheritance": {
            "applicable": False,
            "predicate": "N/A — inline gateway verification",
            "count": None,
            "share": None,
        },
        "controls": [
            {
                "name": "GatewayPause",
                "placement": "destination",
                "scope": "stack_wide",
                "automatic": False,
                "on_path_under_Fs": True,
                "active": True,
            }
        ],
        "detection_gap": {
            "bound": "unbounded",
            "dest_auto_per_root_bound": False,
            "pause_only": True,
            "formula": "Manual pause only; no per-root automatic meter",
        },
        "value_at_risk_usd": None,
    },
    "across": {
        "population": {"type": "spoke_pool_intent", "count": 1, "source": "architecture"},
        "shared_verification_root": {
            "present": True,
            "mode": "don_shared",
            "roots": [{"id": "hubpool_merkle", "label": "HubPool merkle root + relayer network", "members": 1, "share": 1.0}],
        },
        "silent_inheritance": {"applicable": False, "predicate": "N/A", "count": None, "share": None},
        "controls": [
            {
                "name": "UMA/dispute_window",
                "placement": "destination",
                "scope": "stack_wide",
                "automatic": True,
                "on_path_under_Fs": False,
                "active": True,
                "note": "Optimistic model — not ISM-class per-root bound",
            }
        ],
        "detection_gap": {
            "bound": "partial",
            "dest_auto_per_root_bound": False,
            "pause_only": False,
            "note": "NO-POSITION for ExposureGuard hook",
        },
        "value_at_risk_usd": None,
    },
    "connext": {
        "population": {"type": "connext_diamond", "count": 1, "source": "architecture"},
        "shared_verification_root": {
            "present": True,
            "mode": "don_shared",
            "roots": [{"id": "router_watcher_set", "label": "Router network + watchers", "members": 1, "share": 1.0}],
        },
        "silent_inheritance": {"applicable": False, "predicate": "N/A", "count": None, "share": None},
        "controls": [],
        "detection_gap": {
            "bound": "unbounded",
            "dest_auto_per_root_bound": False,
            "pause_only": True,
            "note": "NO-POSITION for ExposureGuard hook",
        },
        "value_at_risk_usd": None,
    },
}


def run_stack(name: str) -> dict[str, Any]:
    sep = _load_sep()
    stack = next(s for s in sep["stacks"] if s["name"] == name)
    base = CURATED[name]
    record: dict[str, Any] = {
        "stack": name,
        "chain": "ethereum",
        "chain_id": 1,
        "block": sep.get("block"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "measurement_depth": "architectural",
        "population": base["population"],
        "shared_verification_root": base["shared_verification_root"],
        "silent_inheritance": base["silent_inheritance"],
        "controls": base["controls"],
        "detection_gap": base["detection_gap"],
        "separability": stack["verdict"],
        "value_at_risk_usd": base.get("value_at_risk_usd"),
        "evidence": stack.get("evidence", []),
        "coverage": {"queried": 1, "resolved": 1, "failed": 0, "failed_share": 0.0},
        "extras": {"contract": stack["contract_addr"], "contract_label": stack["contract_label"]},
    }
    sr = record["shared_verification_root"]["present"]
    mandatory = not record["silent_inheritance"]["applicable"]
    record["pattern_match"] = {
        "SR": sr,
        "SI_or_mandatory": mandatory,
        "no_dest_auto_per_root_bound": not record["detection_gap"]["dest_auto_per_root_bound"],
    }
    return record


def run_all() -> list[dict[str, Any]]:
    return [run_stack(n) for n in ("axelar", "across", "connext")]


if __name__ == "__main__":
    print(json.dumps(run_all(), indent=2))
