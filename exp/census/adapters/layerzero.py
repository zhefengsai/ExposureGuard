"""LayerZero v2 OApp census on Ethereum — default receive library + DVN config clusters."""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
CENSUS = os.path.dirname(HERE)
sys.path.insert(0, CENSUS)

from rpc import (  # noqa: E402
    DEFAULT_RPC,
    call_get_config,
    call_get_receive_library,
    config_fingerprint,
    eth_block_number,
    get_json,
)

ENDPOINT = "0x1a44076050125825900e736c501f859c50fE728c"
LZ_META = "https://metadata.layerzero-api.com/v1/metadata"
# v2 mainnet remote EIDs (representative destination paths from Ethereum OApps)
PROBE_EIDS = {
    30110: "arbitrum",
    30184: "base",
    30111: "optimism",
    30109: "polygon",
}
CONFIG_TYPE_ULN = 2
MAX_OAPPS = int(os.environ.get("LZ_CENSUS_LIMIT", "0"))  # 0 = all


def _pattern(rec: dict[str, Any]) -> dict[str, bool]:
    si = rec["silent_inheritance"]
    share = si.get("share") or 0
    return {
        "SR": rec["shared_verification_root"]["present"],
        "SI_or_mandatory": si["applicable"] and share >= 0.5 or not si["applicable"],
        "no_dest_auto_per_root_bound": not rec["detection_gap"]["dest_auto_per_root_bound"],
    }


def _probe_oapp(args: tuple[str, int, str]) -> dict[str, Any]:
    oapp, eid, rpc = args
    out: dict[str, Any] = {"oapp": oapp, "eid": eid, "ok": False}
    try:
        lib, is_default = call_get_receive_library(ENDPOINT, oapp, eid, rpc)
        cfg_raw = call_get_config(ENDPOINT, oapp, lib, eid, CONFIG_TYPE_ULN, rpc)
        fp = config_fingerprint(cfg_raw)
        out.update({"ok": True, "lib": lib, "is_default": is_default, "config_fp": fp})
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


def run(*, rpc: str = DEFAULT_RPC, workers: int = 10) -> dict[str, Any]:
    meta = get_json(LZ_META)
    eth = meta["ethereum"]
    dep = next(d for d in eth["deployments"] if d.get("version") == 2 and d.get("stage") == "mainnet")
    address_to_oapp: dict[str, dict] = eth.get("addressToOApp") or {}
    oapps = sorted({a.lower() for a in address_to_oapp})
    if MAX_OAPPS:
        oapps = oapps[:MAX_OAPPS]

    block = eth_block_number(rpc)
    jobs = [(oapp, eid, rpc) for oapp in oapps for eid in PROBE_EIDS]
    results: list[dict[str, Any]] = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for row in ex.map(_probe_oapp, jobs):
            results.append(row)
            time.sleep(0.01)

    ok = [r for r in results if r.get("ok")]
    failed = len(results) - len(ok)

    # per-OApp: default if default on ALL probed eids where resolved
    by_oapp: dict[str, list[dict]] = defaultdict(list)
    for r in ok:
        by_oapp[r["oapp"]].append(r)

    default_oapps = []
    custom_oapps = []
    partial_default = []
    for oapp, rows in by_oapp.items():
        defs = [x["is_default"] for x in rows]
        if defs and all(defs):
            default_oapps.append(oapp)
        elif any(defs):
            partial_default.append(oapp)
        else:
            custom_oapps.append(oapp)

    # DVN fingerprint clusters (oapp,eid) granularity
    fp_counter: Counter[str] = Counter()
    for r in ok:
        fp_counter[r["config_fp"]] += 1
    top_fps = fp_counter.most_common(10)
    max_cluster = top_fps[0][1] if top_fps else 0

    oapps_with_any = len(by_oapp)
    default_pairs = [r for r in ok if r["is_default"]]
    pair_default_share = len(default_pairs) / len(ok) if ok else 0
    oapp_all_default_share = len(default_oapps) / oapps_with_any if oapps_with_any else 0

    record: dict[str, Any] = {
        "stack": "layerzero",
        "chain": "ethereum",
        "chain_id": 1,
        "block": block,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "measurement_depth": "on_chain_census",
        "population": {
            "type": "oapp",
            "count": len(oapps),
            "source": "metadata.layerzero-api.com addressToOApp (ethereum v2)",
            "note": f"Probed {len(PROBE_EIDS)} remote EIDs per OApp",
        },
        "shared_verification_root": {
            "present": max_cluster >= 2,
            "mode": "dvn_set_cluster",
            "roots": [
                {
                    "id": fp,
                    "label": f"ULN302 config fingerprint {fp}",
                    "members": n,
                    "share": n / len(ok) if ok else 0,
                }
                for fp, n in top_fps[:5]
            ],
        },
        "silent_inheritance": {
            "applicable": True,
            "predicate": "getReceiveLibrary(oapp,eid).isDefault == true",
            "count": len(default_pairs),
            "share": pair_default_share,
            "note": (
                f"path-level: {len(default_pairs)}/{len(ok)} (oapp,eid) pairs; "
                f"oapp-all-default: {len(default_oapps)}/{oapps_with_any} ({oapp_all_default_share:.0%}); "
                f"{len(partial_default)} mixed"
            ),
        },
        "controls": [
            {
                "name": "OAppOwnerPause/Config",
                "placement": "destination",
                "scope": "per_oapp",
                "automatic": False,
                "on_path_under_Fs": True,
                "active": True,
                "note": "OApp can change DVN set; no protocol-wide automatic per-root meter",
            }
        ],
        "detection_gap": {
            "bound": "unbounded",
            "dest_auto_per_root_bound": False,
            "pause_only": True,
            "formula": "No destination-side automatic cap shared across OApps on same DVN cluster",
            "note": "DVN compromise affects all OApps sharing config fingerprint",
        },
        "separability": "SEPARABLE",
        "value_at_risk_usd": None,
        "evidence": [
            LZ_META,
            f"EndpointV2 {ENDPOINT}",
            f"receiveUln302 {dep['receiveUln302']['address']}",
        ],
        "coverage": {
            "queried": len(jobs),
            "resolved": len(ok),
            "failed": failed,
            "failed_share": failed / len(jobs) if jobs else 0,
        },
        "extras": {
            "endpoint": ENDPOINT,
            "receive_uln302": dep["receiveUln302"]["address"],
            "probe_eids": PROBE_EIDS,
            "default_oapps_all_eids": len(default_oapps),
            "oapp_all_default_share": oapp_all_default_share,
            "default_path_pairs": len(default_pairs),
            "path_default_share": pair_default_share,
            "custom_oapps": len(custom_oapps),
            "partial_default_oapps": len(partial_default),
            "distinct_config_fingerprints": len(fp_counter),
            "largest_cluster_pairs": max_cluster,
        },
    }
    record["pattern_match"] = _pattern(record)
    return record


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
