"""Chainlink CCIP census — shared DON + per-lane TokenPool rate limiters on Ethereum."""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
CENSUS = os.path.dirname(HERE)
sys.path.insert(0, CENSUS)

from rpc import DEFAULT_RPC, call_rate_limiter, eth_block_number, get_json  # noqa: E402

CCIP_CHAINS = "https://docs.chain.link/api/ccip/v1/chains?environment=mainnet&chainId=1"
CCIP_TOKENS = "https://docs.chain.link/api/ccip/v1/tokens?environment=mainnet&chainId=1"
CCIP_LANES_IN = "https://docs.chain.link/api/ccip/v1/lanes?environment=mainnet&destinationChainId=1"
CCIP_LANES_OUT = "https://docs.chain.link/api/ccip/v1/lanes?environment=mainnet&sourceChainId=1"

# Build chainId -> selector map from lanes
CHAIN_SELECTORS: dict[str, int] = {}
MAX_POOLS = int(os.environ.get("CCIP_CENSUS_LIMIT", "0"))  # 0 = all ethereum pools


def _selector_for_dest(dest: str, lane_data: dict) -> int | None:
    """Resolve CCIP chain selector for a destination chain id string."""
    if dest in CHAIN_SELECTORS:
        return CHAIN_SELECTORS[dest]
    # aptos/solana style ids — skip non-numeric for eth_call pools
    if not dest.isdigit() and not dest.startswith("aptos"):
        return None
    return None


def _build_selector_map() -> None:
    lanes = get_json(CCIP_LANES_OUT)["data"]
    for lane in lanes.values():
        src = lane["sourceChain"]
        dst = lane["destinationChain"]
        CHAIN_SELECTORS[str(src["chainId"])] = int(src["selector"])
        CHAIN_SELECTORS[str(dst["chainId"])] = int(dst["selector"])


def _probe_pool(args: tuple[str, str, int, str]) -> dict[str, Any]:
    symbol, pool, remote_sel, rpc = args
    row: dict[str, Any] = {"symbol": symbol, "pool": pool, "remote_selector": remote_sel}
    try:
        tokens, _lu, enabled, capacity, rate = call_rate_limiter(pool, remote_sel, inbound=True, rpc=rpc)
        row.update(
            {
                "ok": True,
                "inbound_enabled": enabled,
                "inbound_capacity": capacity,
                "inbound_rate": rate,
                "inbound_tokens": tokens,
            }
        )
    except Exception as exc:  # noqa: BLE001
        row["ok"] = False
        row["error"] = str(exc)
    return row


def run(*, rpc: str = DEFAULT_RPC, workers: int = 8) -> dict[str, Any]:
    _build_selector_map()
    chains = get_json(CCIP_CHAINS)["data"]["evm"]["1"]
    tokens_resp = get_json(CCIP_TOKENS)["data"]
    lanes_in = get_json(CCIP_LANES_IN)["data"]
    lanes_out = get_json(CCIP_LANES_OUT)["data"]

    pools: list[tuple[str, str, list[str]]] = []
    for symbol, by_chain in tokens_resp.items():
        eth = by_chain.get("1") or by_chain.get(1)
        if not eth:
            continue
        pool = eth.get("poolAddress")
        dests = eth.get("destinations") or []
        if pool and isinstance(pool, str) and pool.startswith("0x"):
            pools.append((symbol, pool.lower(), [str(d) for d in dests]))

    if MAX_POOLS:
        pools = pools[:MAX_POOLS]

    block = eth_block_number(rpc)
    jobs: list[tuple[str, str, int, str]] = []
    for symbol, pool, dests in pools:
        for dest in dests[:3]:  # sample up to 3 remote chains per pool
            sel = CHAIN_SELECTORS.get(dest)
            if sel is not None:
                jobs.append((symbol, pool, sel, rpc))

    results: list[dict[str, Any]] = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for row in ex.map(_probe_pool, jobs):
            results.append(row)
            time.sleep(0.02)

    ok = [r for r in results if r.get("ok")]
    enabled = [r for r in ok if r.get("inbound_enabled")]
    disabled = [r for r in ok if not r.get("inbound_enabled")]

    record: dict[str, Any] = {
        "stack": "ccip",
        "chain": "ethereum",
        "chain_id": 1,
        "block": block,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "measurement_depth": "on_chain_census",
        "population": {
            "type": "token_pool",
            "count": len(pools),
            "source": CCIP_TOKENS,
            "note": f"{len(lanes_in)} inbound lanes + {len(lanes_out)} outbound lanes (directory v1)",
        },
        "shared_verification_root": {
            "present": True,
            "mode": "don_shared",
            "roots": [
                {
                    "id": "ccip_don",
                    "label": "CCIP DON message attestation (all lanes)",
                    "members": len(pools),
                    "share": 1.0,
                }
            ],
        },
        "silent_inheritance": {
            "applicable": False,
            "predicate": "N/A — verification infrastructure is stack-mandatory",
            "count": None,
            "share": None,
            "note": "Apps inherit DON trust by using CCIP Router",
        },
        "controls": [
            {
                "name": "TokenPoolInboundRateLimiter",
                "placement": "destination",
                "scope": "per_lane",
                "automatic": True,
                "on_path_under_Fs": True,
                "active": len(enabled) > 0,
                "note": f"{len(enabled)}/{len(ok)} probed (pool×remote) pairs enabled",
            },
            {
                "name": "RMN/TimelockPause",
                "placement": "destination",
                "scope": "stack_wide",
                "automatic": False,
                "on_path_under_Fs": True,
                "active": True,
                "note": f"Router owner timelock {chains.get('router')}",
            },
        ],
        "detection_gap": {
            "bound": "partial",
            "dest_auto_per_root_bound": False,
            "pause_only": False,
            "formula": "Bound per (token pool, remote chain); Σ lane caps not root-coordinated",
            "note": "Compromised DON: attacker can fan out across lanes/pools; no single per-root meter",
        },
        "separability": "SEPARABLE",
        "value_at_risk_usd": None,
        "evidence": [CCIP_CHAINS, CCIP_TOKENS, CCIP_LANES_IN, "exp/separability.json"],
        "coverage": {
            "queried": len(jobs),
            "resolved": len(ok),
            "failed": len(jobs) - len(ok),
            "failed_share": (len(jobs) - len(ok)) / len(jobs) if jobs else 0,
        },
        "extras": {
            "router": chains["router"],
            "rmn": chains["rmn"],
            "inbound_lanes_to_eth": len(lanes_in),
            "outbound_lanes_from_eth": len(lanes_out),
            "pools_on_eth": len(pools),
            "rate_limit_probes": len(jobs),
            "inbound_enabled_pairs": len(enabled),
            "inbound_disabled_pairs": len(disabled),
            "sample_enabled": enabled[:5],
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
