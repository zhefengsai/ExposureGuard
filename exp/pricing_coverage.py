#!/usr/bin/env python3
"""Export the pricing denominator used by the evaluation."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CHAINS = ("ethereum", "base", "arbitrum", "bsc")
XVAL = {
    "ethereum": "chainlog_xval.json",
    "base": "chainlog_xval_base.json",
    "arbitrum": "chainlog_xval_arbitrum.json",
    "bsc": "chainlog_xval_bsc.json",
}


def main() -> None:
    per_chain = {}
    transfers = directly_priced = 0
    for chain in CHAINS:
        xval = json.loads((DATA / XVAL[chain]).read_text())
        n = int(xval["totals"]["chain_msgs_default_routes"])
        final = json.loads((DATA / f"chainlog_priced_{chain}.json").read_text())
        assert not final.get("routes_uncovered"), f"{chain}: uncovered active routes"
        p = int(final["n"])
        assert 0 <= p <= n
        transfers += n
        directly_priced += p
        per_chain[chain] = {
            "onchain_default_route_deliveries": n,
            "aggregator_priced_events": p,
            "aggregator_price_coverage": p / n if n else 1.0,
        }

    trace = json.loads((DATA / "events_multi.json").read_text())
    provenance = {}
    value_by_family = defaultdict(float)
    daily_by_chain = defaultdict(lambda: defaultdict(float))
    stable_daily_by_chain = defaultdict(lambda: defaultdict(float))
    for row in trace:
        key = row.get("provenance", "unknown")
        provenance[key] = provenance.get(key, 0) + 1
        family = str(row.get("family", "")).upper()
        usd = float(row["usd"])
        value_by_family[family] += usd
        day = str(row["t"])[:10]
        chain = str(row["chain"])
        daily_by_chain[chain][day] += usd
        if family in {"USDC", "USDT"}:
            stable_daily_by_chain[chain][day] += usd
    assert provenance == {"chain": directly_priced}

    omitted_by_route_type = Counter()
    omitted_by_reason = Counter()
    omitted_routes_by_type = Counter()
    for chain in CHAINS:
        xval = json.loads((DATA / XVAL[chain]).read_text())
        delivered = {
            str(row["route"]).lower().removeprefix("0x"): int(row["chain_msgs"])
            for row in xval["per_route"]
        }
        priced_rows = json.loads(
            (DATA / f"chainlog_priced_{chain}.json").read_text())["events"]
        priced_by_route = Counter(
            str(row["route"]).lower().removeprefix("0x") for row in priced_rows)
        metadata = {
            str(row["router"]).lower().removeprefix("0x"): row
            for row in json.loads((DATA / f"hl_ism_{chain}.json").read_text())
        }
        for route, count in delivered.items():
            missing = count - priced_by_route[route]
            assert missing >= 0
            if not missing:
                continue
            standard = str(metadata.get(route, {}).get("standard", ""))
            if "Synthetic" in standard:
                route_type = "synthetic"
            elif "Native" in standard:
                route_type = "native"
            elif "Collateral" in standard or metadata.get(route, {}).get("collateral"):
                route_type = "collateral"
            else:
                route_type = "other"
            reason = ("route_without_priced_observation" if priced_by_route[route] == 0
                      else "event_excluded_on_route_with_priced_observations")
            omitted_by_route_type[route_type] += missing
            omitted_by_reason[reason] += missing
            omitted_routes_by_type[route_type] += 1

    assert sum(omitted_by_route_type.values()) == transfers - directly_priced

    total_value = sum(value_by_family.values())
    stable_value = value_by_family["USDC"] + value_by_family["USDT"]
    peak_stable_share = {}
    for chain in CHAINS:
        peak_day = max(daily_by_chain[chain], key=daily_by_chain[chain].get)
        peak_total = daily_by_chain[chain][peak_day]
        peak_stable_share[chain] = {
            "peak_day": peak_day,
            "peak_usd": peak_total,
            "usdc_usdt_share": stable_daily_by_chain[chain][peak_day] / peak_total,
        }

    out = {
        "scope": "deliveries to default-module token routes on four chains",
        "per_chain": per_chain,
        "onchain_default_route_deliveries": transfers,
        "aggregator_priced_events": directly_priced,
        "aggregator_price_coverage": directly_priced / transfers,
        "final_priced_trace_events": len(trace),
        "final_trace_provenance": provenance,
        "lineage_note": "final all-route chainlog_priced files supersede earlier partial stage2 summaries",
        "budget_deferral_denominator": len(trace),
        "priced_value_usd": total_value,
        "usdc_usdt_value_share": stable_value / total_value,
        "per_chain_peak_usdc_usdt_share": peak_stable_share,
        "omitted_deliveries": transfers - directly_priced,
        "omitted_deliveries_by_route_type": dict(sorted(omitted_by_route_type.items())),
        "omitted_routes_by_type": dict(sorted(omitted_routes_by_type.items())),
        "omitted_deliveries_by_observable_reason": dict(sorted(omitted_by_reason.items())),
        "omitted_classification_warning": "route type is not an exemption decision; every omitted delivery remains reject pending an audited price or explicit governance classification",
        "unpriced_policy": "reject until governance supplies an audited price; not simulated as zero value or as exempt",
        "interpretation": "reported budget deferral is conditional on the priced trace, not all deliveries",
    }
    path = DATA / "pricing_coverage.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
