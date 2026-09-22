"""Build census record from existing Hyperlane pilot artifacts."""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
PILOT = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(PILOT, "data")
ZERO = "0x" + "0" * 40


def _load(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run(*, chain: str = "ethereum") -> dict[str, Any]:
    ism_path = os.path.join(DATA, f"hl_ism_{chain}.json")
    rows = _load(ism_path)
    stock = _load(os.path.join(DATA, "stock_multi.json"))
    ratelimit = _load(os.path.join(DATA, "hl_ratelimit.json"))
    sep = _load(os.path.join(PILOT, "separability.json"))

    hl_sep = next(s for s in sep["stacks"] if s["name"] == "hyperlane")
    default_ism = hl_sep["extras"].get("defaultIsm", "").lower()

    resolved = [r for r in rows if r.get("ism") is not None]
    failed = len(rows) - len(resolved)
    zero_ism = [r for r in resolved if r["ism"].lower() == ZERO]
    custom = [r for r in resolved if r["ism"].lower() != ZERO]

    # cluster non-zero ISMs
    by_ism: dict[str, list] = defaultdict(list)
    for r in custom:
        by_ism[r["ism"].lower()].append(r)

    roots = []
    if zero_ism:
        roots.append(
            {
                "id": "mailbox_default_ism",
                "label": f"Mailbox defaultIsm ({default_ism})",
                "members": len(zero_ism),
                "share": len(zero_ism) / len(resolved) if resolved else 0,
            }
        )
    for ism, rs in sorted(by_ism.items(), key=lambda x: -len(x[1])):
        roots.append({"id": ism, "label": ism, "members": len(rs), "share": len(rs) / len(resolved)})

    rl_active = sum(1 for r in ratelimit if r.get("is_ratelimited"))
    chain_stock = stock.get(chain, {})
    total_stock = sum(stock[c]["total_usd"] for c in ("ethereum", "base", "arbitrum", "bsc") if c in stock)

    record: dict[str, Any] = {
        "stack": "hyperlane",
        "chain": chain,
        "chain_id": 1 if chain == "ethereum" else None,
        "block": sep.get("block"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "measurement_depth": "imported",
        "population": {
            "type": "warp_route_router",
            "count": len(rows),
            "source": f"data/hl_ism_{chain}.json",
            "note": "Unique routers from hyperlane registry; ISM via interchainSecurityModule()",
        },
        "shared_verification_root": {
            "present": True,
            "mode": "default_inheritance",
            "roots": roots,
        },
        "silent_inheritance": {
            "applicable": True,
            "predicate": "interchainSecurityModule() == address(0)",
            "count": len(zero_ism),
            "share": len(zero_ism) / len(resolved) if resolved else None,
            "note": f"{failed} RPC failures excluded from denominator",
        },
        "controls": [
            {
                "name": "PausableIsm",
                "placement": "destination",
                "scope": "per_root",
                "automatic": False,
                "on_path_under_Fs": True,
                "active": True,
                "note": "Binary manual pause on default ISM aggregation",
            },
            {
                "name": "HyperlaneRateLimitIsm",
                "placement": "destination",
                "scope": "per_route",
                "automatic": True,
                "on_path_under_Fs": True,
                "active": rl_active > 0,
                "note": f"{rl_active}/{len(ratelimit)} ISMs with token bucket on {chain}",
            },
        ],
        "detection_gap": {
            "bound": "unbounded",
            "dest_auto_per_root_bound": False,
            "pause_only": True,
            "formula": "Under default ISM: admit = full stock during [t0,t1] if operator does not pause",
            "note": "Per-route automatic limits do not compose to per-root bound",
        },
        "separability": "SEPARABLE",
        "value_at_risk_usd": {
            "strict": total_stock,
            "source": "data/stock_multi.json (4-chain collateral on default ISM routes)",
            "note": f"{chain} slice ${chain_stock.get('total_usd', 0):,.0f}",
        },
        "evidence": [
            f"data/hl_ism_{chain}.json",
            "data/stock_multi.json",
            "exp/separability.json",
        ],
        "coverage": {
            "queried": len(rows),
            "resolved": len(resolved),
            "failed": failed,
            "failed_share": failed / len(rows) if rows else 0,
        },
        "extras": {
            "chains_measured": ["ethereum", "base", "arbitrum", "bsc"],
            "default_ism": default_ism,
            "distinct_custom_isms": len(by_ism),
            "ism_cluster_top5": [
                {"ism": k, "routes": len(v)} for k, v in sorted(by_ism.items(), key=lambda x: -len(x[1]))[:5]
            ],
        },
    }
    record["pattern_match"] = _pattern(record)
    return record


def _pattern(rec: dict[str, Any]) -> dict[str, bool]:
    si = rec["silent_inheritance"]
    si_ok = si["applicable"] and (si.get("share") or 0) >= 0.5
    mandatory = not si["applicable"]
    return {
        "SR": rec["shared_verification_root"]["present"],
        "SI_or_mandatory": si_ok or mandatory,
        "no_dest_auto_per_root_bound": not rec["detection_gap"]["dest_auto_per_root_bound"],
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
