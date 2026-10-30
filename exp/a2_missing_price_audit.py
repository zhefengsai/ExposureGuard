#!/usr/bin/env python3
"""Reconcile the A2 pricing denominator without inventing missing USD values.

This audit uses frozen delivery counts and priced events.  A route with no
priced observation is not necessarily missing *only* a quote: the stage-2
collector can also exclude malformed or unmatched transfer events.  The
later-quote classes in unpriced_triage.json are therefore reported as
observations, not retrospective causes or historical prices.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" if (HERE / "data").is_dir() else HERE.parent / "data"
XVAL = {
    "ethereum": "chainlog_xval_ethereum.json",
    "base": "chainlog_xval_base.json",
    "arbitrum": "chainlog_xval_arbitrum.json",
    "bsc": "chainlog_xval_bsc.json",
}


def read(name: str) -> object:
    return json.loads((DATA / name).read_text())


def norm(address: str) -> str:
    return str(address).lower().removeprefix("0x")


def route_type(standard: str, collateral: str | None) -> str:
    if "Synthetic" in standard:
        return "synthetic"
    if "Native" in standard:
        return "native"
    if "Collateral" in standard or collateral:
        return "collateral"
    return "other"


def main() -> None:
    later = {(row["chain"], norm(row["route"])): row
             for row in read("unpriced_triage.json")["routes"]}
    all_priced = read("events_multi.json")
    routes: list[dict] = []
    by_chain: dict[str, dict] = {}
    by_type = Counter()
    by_observation = Counter()
    n_delivered = n_priced = 0

    for chain, xval_name in XVAL.items():
        xval = read(xval_name)
        priced_path = DATA / f"chainlog_priced_{chain}.json"
        priced_events = (read(priced_path.name)["events"] if priced_path.exists() else
                         [event for event in all_priced if event["chain"] == chain])
        metadata = {norm(row["router"]): row
                    for row in read(f"hl_ism_{chain}.json")}
        priced_counts = Counter(norm(e["route"]) for e in priced_events)
        delivered = sum(int(row["chain_msgs"]) for row in xval["per_route"])
        assert delivered == int(xval["totals"]["chain_msgs_default_routes"])
        assert sum(priced_counts.values()) == len(priced_events)
        chain_missing = 0
        for row in xval["per_route"]:
            route = norm(row["route"])
            count = int(row["chain_msgs"])
            missing = count - priced_counts[route]
            assert missing >= 0, (chain, route)
            if not missing:
                continue
            md = metadata.get(route, {})
            coll = str(md.get("collateral") or "").lower() or None
            kind = route_type(str(md.get("standard") or row.get("standard") or ""), coll)
            subsequent = later.get((chain, route), {})
            assert int(subsequent.get("missing", -1)) == missing
            observation = ("no_local_collateral_address" if not coll else
                           "local_collateral_later_quoted" if subsequent.get("source_quote") is not None else
                           "local_collateral_no_later_quote")
            by_type[kind] += missing
            by_observation[observation] += missing
            chain_missing += missing
            routes.append({
                "chain": chain, "route": route,
                "symbol": row.get("symbol") or md.get("symbol"),
                "type": kind, "standard": md.get("standard"),
                "collateral": coll, "delivered": count,
                "priced": priced_counts[route], "omitted": missing,
                "later_quote_observation": observation,
                "stage2_filter_unresolved": priced_counts[route] > 0,
            })
        by_chain[chain] = {"delivered": delivered, "priced": len(priced_events),
                           "omitted": chain_missing,
                           "omitted_fraction": chain_missing / delivered}
        assert chain_missing == delivered - len(priced_events)
        n_delivered += delivered
        n_priced += len(priced_events)

    n_missing = n_delivered - n_priced
    assert n_missing == 13036
    assert sum(by_type.values()) == n_missing
    assert sum(by_observation.values()) == n_missing
    out = {
        "scope": "default-module token-route deliveries on four chains",
        "delivered": n_delivered, "priced": n_priced,
        "omitted": n_missing, "priced_message_fraction": n_priced / n_delivered,
        "by_chain": by_chain, "by_route_type": dict(sorted(by_type.items())),
        "by_later_observation": dict(sorted(by_observation.items())),
        "routes": sorted(routes, key=lambda r: (-r["omitted"], r["chain"], r["route"])),
        "interpretation": [
            "No-local-collateral includes both synthetic and native routes.",
            "A later current quote is not a historical quote at delivery time.",
            "Route-level subtraction cannot establish the exact event-level exclusion cause.",
            "Unknown USD value remains unknown; message-count coverage is not value coverage.",
        ],
    }
    path = DATA / "a2_missing_price_audit.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in
                      ("delivered", "priced", "omitted", "by_chain", "by_route_type",
                       "by_later_observation")}, indent=2))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
