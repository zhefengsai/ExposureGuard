#!/usr/bin/env python3
"""Merge stack census records, compute prevalence + TVL weighting, emit Table X."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
PILOT = os.path.dirname(HERE)


def load_records() -> list[dict[str, Any]]:
    merged_path = os.path.join(DATA, "census_merged.json")
    with open(merged_path, encoding="utf-8") as f:
        blob = json.load(f)
    return blob["stacks"]


def compute_prevalence(stacks: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(stacks)
    pattern = [s for s in stacks if all(s.get("pattern_match", {}).values())]
    sr = [s for s in stacks if s.get("shared_verification_root", {}).get("present")]
    no_ab = [s for s in stacks if not s["detection_gap"]["dest_auto_per_root_bound"]]

    tvl_rows = []
    total_tvl = 0.0
    pattern_tvl = 0.0
    for s in stacks:
        var = s.get("value_at_risk_usd")
        if not var:
            continue
        strict = var.get("strict") or 0
        tvl_rows.append({"stack": s["stack"], "usd_strict": strict})
        total_tvl += strict
        if all(s.get("pattern_match", {}).values()):
            pattern_tvl += strict

    return {
        "stacks_surveyed": n,
        "pattern_P_count": len(pattern),
        "pattern_P_share": len(pattern) / n if n else 0,
        "SR_count": len(sr),
        "no_dest_auto_per_root_bound_count": len(no_ab),
        "pattern_stacks": [s["stack"] for s in pattern],
        "tvl_usd_strict_total": total_tvl,
        "tvl_usd_strict_pattern_P": pattern_tvl,
        "tvl_pattern_share": pattern_tvl / total_tvl if total_tvl else None,
        "tvl_by_stack": tvl_rows,
    }


def markdown_table(stacks: list[dict[str, Any]], prev: dict[str, Any]) -> str:
    lines = [
        "# Cross-stack verification-root census",
        "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
        "## Prevalence (pattern P)",
        "",
        f"- **{prev['pattern_P_count']}/{prev['stacks_surveyed']} stacks** match shared root + (silent inherit OR mandatory) + no destination automatic per-root bound",
        f"- Pattern stacks: {', '.join(prev['pattern_stacks'])}",
        f"- **TVL-weighted (strict, measured only): ${prev['tvl_usd_strict_pattern_P']:,.0f} / ${prev['tvl_usd_strict_total']:,.0f}**"
        + (f" ({prev['tvl_pattern_share']*100:.1f}%)" if prev.get("tvl_pattern_share") else ""),
        "",
        "## Table X",
        "",
        "| Stack | Population | Shared root | Silent inherit | Dest. auto per-root bound | Gap bound | Separability | TVL (strict) |",
        "|---|---|---:|---|---:|---|---|---:|",
    ]
    for s in stacks:
        pop = s["population"]
        pop_s = f"{pop['count']} {pop['type']}"
        sr = s["shared_verification_root"]
        sr_s = sr["roots"][0]["label"][:48] if sr.get("roots") else sr["mode"]
        si = s["silent_inheritance"]
        if not si["applicable"]:
            si_s = "N/A (mandatory)"
        elif s["stack"] == "layerzero":
            pairs = s.get("extras", {}).get("default_path_pairs", si.get("count"))
            si_s = f"{pairs} paths ({(si.get('share') or 0)*100:.0f}%)"
        else:
            si_s = f"{si.get('count', '?')} ({(si.get('share') or 0)*100:.0f}%)"
        ab = "Yes" if s["detection_gap"]["dest_auto_per_root_bound"] else "No"
        gap = s["detection_gap"]["bound"]
        sep = s["separability"]
        tvl = s.get("value_at_risk_usd") or {}
        tvl_s = f"${tvl.get('strict', 0):,.0f}" if tvl.get("strict") else "—"
        lines.append(f"| **{s['stack']}** | {pop_s} | {sr_s} | {si_s} | {ab} | {gap} | {sep} | {tvl_s} |")
    lines.extend(
        [
            "",
            "## Claim template (paper)",
            "",
            "> "
            + f"{prev['pattern_P_count']}/{prev['stacks_surveyed']} surveyed interoperability stacks exhibit recurring pattern P "
            + "(shared verification, no destination-side automatic per-root bound during the detection gap), "
            + f"spanning ISM-, DVN-, guardian-, and DON-based designs; "
            + f"measured TVL behind pattern stacks ≥ ${prev['tvl_usd_strict_pattern_P']:,.0f}.",
            "",
        ]
    )
    return "\n".join(lines)


def aggregate(stacks: list[dict[str, Any]]) -> dict[str, Any]:
    prev = compute_prevalence(stacks)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prevalence": prev,
        "stacks": stacks,
    }


def main() -> None:
    stacks = load_records()
    out = aggregate(stacks)
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "census_aggregate.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    md = markdown_table(stacks, out["prevalence"])
    with open(os.path.join(HERE, "CENSUS-RESULTS.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)


if __name__ == "__main__":
    main()
