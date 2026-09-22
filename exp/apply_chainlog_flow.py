"""Union-merge indexer flow with chain-log Stage-2 priced events.

For each route:
  chain stage-2 has the route  -> use chain events (provenance=chain)
  only indexer has the route   -> keep indexer events (provenance=indexer)

Hard assert: route count after merge >= before (superset, never replace).

Does NOT touch sim3.py / fig / incidents — peak will change again after
multi-chain; that is a separate calibration step.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CHAINS = ["ethereum", "base", "arbitrum", "bsc"]


def _norm_route(r: str) -> str:
    return r.lower().removeprefix("0x")


def _load_indexer_events(chain: str) -> list[dict]:
    """Prefer pristine indexer backup; else per-chain file; else multi slice."""
    bak = os.path.join(DATA, f"events_{chain}.indexer.bak.json")
    cur = os.path.join(DATA, f"events_{chain}.json")
    multi_bak = os.path.join(DATA, "events_multi.indexer.bak.json")
    if os.path.exists(bak):
        return json.load(open(bak))
    if chain == "ethereum" and os.path.exists(multi_bak):
        return [e for e in json.load(open(multi_bak)) if e.get("chain") == chain]
    if os.path.exists(cur):
        ev = json.load(open(cur))
        # Drop prior provenance-less chain-only replace if bak missing
        return [e for e in ev if e.get("provenance") != "chain"]
    raise SystemExit(f"no indexer events for {chain}")


def _load_chain_priced(chain: str) -> list[dict]:
    path = os.path.join(DATA, f"chainlog_priced_{chain}.json")
    if not os.path.exists(path):
        return []
    blob = json.load(open(path))
    ev = blob.get("events") or []
    out = []
    for e in ev:
        out.append({
            "t": e["t"][:19],
            "route": _norm_route(e["route"]),
            "family": e.get("family") or e.get("symbol") or "UNK",
            "symbol": e.get("symbol") or e.get("family") or "UNK",
            "usd": float(e["usd"]),
            "chain": chain,
            "provenance": "chain",
        })
    return out


def merge_chain(chain: str, indexer: list[dict], chain_ev: list[dict]) -> tuple[list[dict], dict]:
    routes_before = {_norm_route(e["route"]) for e in indexer}
    by_chain: dict[str, list] = defaultdict(list)
    for e in chain_ev:
        by_chain[_norm_route(e["route"])].append(e)
    by_idx: dict[str, list] = defaultdict(list)
    for e in indexer:
        by_idx[_norm_route(e["route"])].append({
            "t": e["t"][:19],
            "route": _norm_route(e["route"]),
            "family": e.get("family") or e.get("symbol") or "UNK",
            "symbol": e.get("symbol") or e.get("family") or "UNK",
            "usd": float(e["usd"]),
            "chain": chain,
            "provenance": "indexer",
        })

    merged: list[dict] = []
    for route in sorted(set(by_chain) | set(by_idx)):
        if route in by_chain:
            merged.extend(by_chain[route])
        else:
            merged.extend(by_idx[route])
    merged.sort(key=lambda e: e["t"])

    routes_after = {e["route"] for e in merged}
    # Set containment, not just a count. A count check passes when the chain
    # side happens to find MORE routes than the indexer had while still losing
    # some the indexer covered -- which is exactly the failure this guard exists
    # to catch. Ethereum passed the count check by luck (39 >= 32); the three
    # remaining chains may not.
    dropped = routes_before - routes_after
    assert not dropped, (
        f"merge dropped {len(dropped)} route(s) the indexer covered: "
        f"{sorted(dropped)[:5]}{' …' if len(dropped) > 5 else ''} "
        f"({len(routes_before)} -> {len(routes_after)})")

    usd_chain = sum(e["usd"] for e in merged if e["provenance"] == "chain")
    usd_idx = sum(e["usd"] for e in merged if e["provenance"] == "indexer")
    daily: dict[str, float] = defaultdict(float)
    for e in merged:
        daily[e["t"][:10]] += e["usd"]
    peak = max(daily.values()) if daily else 0.0
    mean = (sum(daily.values()) / len(daily)) if daily else 0.0
    stats = {
        "routes_before": len(routes_before),
        "routes_after": len(routes_after),
        "routes_chain": len(set(by_chain)),
        "routes_indexer_only": len(set(by_idx) - set(by_chain)),
        "routes_chain_only": len(set(by_chain) - set(by_idx)),
        "n_events": len(merged),
        "n_chain": sum(1 for e in merged if e["provenance"] == "chain"),
        "n_indexer": sum(1 for e in merged if e["provenance"] == "indexer"),
        "usd_chain": usd_chain,
        "usd_indexer": usd_idx,
        "total_usd": usd_chain + usd_idx,
        "peak_daily": peak,
        "mean_daily": mean,
        "days": len(daily),
    }
    return merged, stats


def main() -> None:
    all_merged: list[dict] = []
    summary = {}
    compare = {}

    for chain in CHAINS:
        indexer = _load_indexer_events(chain)
        # Ensure chain field
        for e in indexer:
            e["chain"] = chain
        chain_ev = _load_chain_priced(chain)
        merged, stats = merge_chain(chain, indexer, chain_ev)
        path = os.path.join(DATA, f"events_{chain}.json")
        json.dump(merged, open(path, "w"), indent=1)
        print(f"{chain}: routes {stats['routes_before']} → {stats['routes_after']}  "
              f"events={stats['n_events']}  "
              f"usd_chain=${stats['usd_chain']:,.0f}  "
              f"usd_indexer=${stats['usd_indexer']:,.0f}  "
              f"peak=${stats['peak_daily']:,.0f}")
        assert stats["routes_after"] >= stats["routes_before"]
        all_merged.extend(merged)
        compare[chain] = {
            "indexer_routes": stats["routes_before"],
            "indexer_usd": sum(float(e["usd"]) for e in indexer),
            "indexer_events": len(indexer),
            "merged": stats,
            "chain_priced_file": bool(chain_ev),
        }
        # Preserve prior summary fields where possible
        summary[chain] = {
            "priced_events": stats["n_events"],
            "active_routes": stats["routes_after"],
            "total_usd": stats["total_usd"],
            "mean_daily": stats["mean_daily"],
            "peak_daily": stats["peak_daily"],
            "days": stats["days"],
            "provenance": {
                "routes_chain": stats["routes_chain"],
                "routes_indexer": stats["routes_indexer_only"],
                "usd_chain": stats["usd_chain"],
                "usd_indexer": stats["usd_indexer"],
                "n_events_chain": stats["n_chain"],
                "n_events_indexer": stats["n_indexer"],
            },
            "source": "union(chainlog stage2, indexer)",
        }

    all_merged.sort(key=lambda e: (e["t"], e["chain"], e["route"]))
    multi_path = os.path.join(DATA, "events_multi.json")
    json.dump(all_merged, open(multi_path, "w"), indent=1)
    print(f"wrote {multi_path} n={len(all_merged)}")

    # Merge into existing flow_multi_summary shells (keep failed_routes etc.)
    for sp in (os.path.join(HERE, "flow_multi_summary.json"),
               os.path.join(DATA, "flow_multi_summary.json")):
        if os.path.exists(sp):
            sm = json.load(open(sp))
        else:
            sm = {c: {} for c in CHAINS}
        for c in CHAINS:
            sm.setdefault(c, {})
            sm[c].update(summary[c])
        json.dump(sm, open(sp, "w"), indent=1)
        print(f"updated {sp}")

    report = {
        "merge": "union",
        "assert": "routes_after >= routes_before",
        "chains": compare,
        "totals": {
            "routes_before": sum(compare[c]["indexer_routes"] for c in CHAINS),
            "routes_after": sum(compare[c]["merged"]["routes_after"] for c in CHAINS),
            "usd_before": sum(compare[c]["indexer_usd"] for c in CHAINS),
            "usd_after": sum(compare[c]["merged"]["total_usd"] for c in CHAINS),
        },
    }
    out = os.path.join(DATA, "chainlog_merge.json")
    json.dump(report, open(out, "w"), indent=2)
    print(f"wrote {out}")
    print(f"TOTAL routes {report['totals']['routes_before']} → "
          f"{report['totals']['routes_after']}  "
          f"usd ${report['totals']['usd_before']:,.0f} → "
          f"${report['totals']['usd_after']:,.0f}")


if __name__ == "__main__":
    main()
