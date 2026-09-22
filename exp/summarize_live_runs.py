#!/usr/bin/env python3
"""Summarize prospective shadow and public-testnet runs without hand editing."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] if lo == hi else xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def summarize_testnet(path: Path) -> dict:
    rows = load_jsonl(path)
    if not rows:
        raise SystemExit("empty testnet workload")
    rows.sort(key=lambda r: r["timestamp"])
    first = datetime.fromisoformat(rows[0]["timestamp"])
    last = datetime.fromisoformat(rows[-1]["timestamp"])
    latency = [float(r["end_to_end_seconds"]) for r in rows]
    message_ids = [r.get("message_id") for r in rows]
    sequences = [r.get("sequence") for r in rows]
    invalid_rows = [
        r.get("sequence") for r in rows
        if not r.get("message_id") or not r.get("dispatch_tx") or
        not r.get("process_tx") or float(r.get("end_to_end_seconds", -1)) < 0 or
        int(r.get("process_gas", -1)) <= 0
    ]
    return {
        "schema": 2, "kind": "author_operated_public_testnet",
        "first_at": rows[0]["timestamp"], "last_at": rows[-1]["timestamp"],
        "elapsed_hours": (last - first).total_seconds() / 3600,
        "completed_observations": len(rows),
        "delivered": sum(bool(r.get("delivered")) for r in rows),
        "metered": sum(bool(r.get("metered")) for r in rows),
        "unique_message_ids": len(set(message_ids)),
        "duplicate_message_ids": len(message_ids) - len(set(message_ids)),
        "unique_sequences": len(set(sequences)),
        "invalid_rows": invalid_rows,
        "latency_seconds": {"mean": sum(latency) / len(latency),
                            "p50": percentile(latency, .5),
                            "p95": percentile(latency, .95),
                            "max": max(latency)},
        "process_gas_mean": sum(int(r["process_gas"]) for r in rows) / len(rows),
        "failed_rows": [r.get("sequence") for r in rows
                        if not (r.get("delivered") and r.get("metered"))],
    }


def load_configs(root: Path) -> list[dict]:
    directory = root / "configs" if (root / "configs").is_dir() else root
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]


def summarize_shadow_events(path: Path, records: list[dict]) -> dict:
    rows = load_jsonl(path)
    config_days = {r["effective_date"] for r in records}
    prospective = [r for r in rows if r.get("t", "")[:10] in config_days]
    keys = [(r.get("chain"), r.get("tx"), r.get("route"), r.get("block"))
            for r in rows]
    invalid = [i for i, r in enumerate(rows, 1)
               if r.get("chain") not in ("ethereum", "base", "arbitrum", "bsc")
               or not r.get("tx") or not r.get("route") or not r.get("t")
               or float(r.get("usd", -1)) < 0]
    daily = {}
    for row in prospective:
        key = (row["chain"], row["t"][:10])
        daily[key] = daily.get(key, 0.0) + float(row["usd"])
    reference = records[-1]["configuration"]["chains"]
    peaks = {}
    for chain in reference:
        candidates = [(value, day) for (name, day), value in daily.items()
                      if name == chain]
        value, day = max(candidates, default=(0.0, None))
        frozen = float(reference[chain]["measured_peak_usd_per_day"])
        peaks[chain] = {
            "frozen_60d_peak_usd": frozen,
            "max_prospective_day_usd": value,
            "max_prospective_day": day,
            "fraction_of_frozen_peak": value / frozen if frozen else None,
        }
    return {
        "rows": len(rows),
        "prospective_rows": len(prospective),
        "unique_event_keys": len(set(keys)),
        "duplicate_event_keys": len(keys) - len(set(keys)),
        "invalid_rows": invalid,
        "first_at": min((r["t"] for r in rows), default=None),
        "last_at": max((r["t"] for r in rows), default=None),
        "headroom": peaks,
    }


def summarize_shadow(root: Path, events: Path | None = None) -> dict:
    records = load_configs(root)
    if not records:
        raise SystemExit(f"no daily configurations under {root}")
    days = [r["effective_date"] for r in records]
    budgets = [float(r["configuration"]["total_budget_usd_per_day"]) for r in records]
    changes = [abs(b - a) / a if a else 0.0 for a, b in zip(budgets, budgets[1:])]
    operational = [r.get("audit", {}) for r in records]
    recorded_collection_ages = [max(r["collection_age_hours"].values())
                                for r in records if r.get("collection_age_hours")]
    configurations = [r["configuration"] for r in records]
    canonical_provenance = all("trace_records" in c for c in configurations)
    return {
        "schema": 2, "kind": "prospective_production_data_shadow",
        "first_date": min(days), "last_date": max(days),
        "daily_configurations": len(records),
        "distinct_utc_days": len(set(days)),
        "budget_usd_per_day": {"first": budgets[0], "last": budgets[-1],
                               "min": min(budgets), "max": max(budgets)},
        "mean_day_to_day_budget_change": (sum(changes) / len(changes) if changes else 0.0),
        "audit_passes": sum(bool(a.get("ok")) for a in operational),
        "audit_warning_days": sum(bool(a.get("warnings")) for a in operational),
        "operational_snapshot_days": sum(r.get("operational_snapshot") is not None
                                         for r in records),
        "collection_age_recorded_days": len(recorded_collection_ages),
        "collection_age_unrecorded_days": len(records) - len(recorded_collection_ages),
        "max_recorded_collection_age_hours": max(recorded_collection_ages, default=None),
        "duplicate_configuration_days": len(days) - len(set(days)),
        "input_provenance": ("canonical_training_rows" if canonical_provenance
                             else "legacy_static_source_hash"),
        "live_enforcement": False,
        **({"events": summarize_shadow_events(events, records)} if events else {}),
    }


def compare_shadow(a: Path, b: Path) -> dict:
    left = {r["effective_date"]: r for r in load_configs(a)}
    right = {r["effective_date"]: r for r in load_configs(b)}
    common = sorted(set(left) & set(right))
    differences = []
    chains = {}
    chain_names = sorted(next(iter(left.values()))["configuration"]["chains"])
    for chain in chain_names:
        budget_diffs, peak_diffs, relative_peak_diffs = [], [], []
        for day in common:
            x = left[day]["configuration"]["chains"][chain]
            y = right[day]["configuration"]["chains"][chain]
            budget_diffs.append(abs(float(x["budget_usd_per_day"]) -
                                    float(y["budget_usd_per_day"])))
            peak_diff = abs(float(x["measured_peak_usd_per_day"]) -
                            float(y["measured_peak_usd_per_day"]))
            peak_diffs.append(peak_diff)
            denominator = max(float(x["measured_peak_usd_per_day"]),
                              float(y["measured_peak_usd_per_day"]), 1.0)
            relative_peak_diffs.append(peak_diff / denominator)
        chains[chain] = {
            "identical_budget_days": sum(v == 0 for v in budget_diffs),
            "max_absolute_budget_difference_usd": max(budget_diffs, default=None),
            "max_absolute_peak_difference_usd": max(peak_diffs, default=None),
            "max_relative_peak_difference": max(relative_peak_diffs, default=None),
        }
    for day in common:
        x, y = left[day]["configuration"], right[day]["configuration"]
        differences.append(abs(float(x["total_budget_usd_per_day"]) -
                               float(y["total_budget_usd_per_day"])))
    return {"overlapping_days": len(common), "dates": common,
            "max_absolute_budget_difference_usd": max(differences, default=None),
            "identical_budget_days": sum(v == 0 for v in differences),
            "chains": chains}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="kind", required=True)
    t = sub.add_parser("testnet")
    t.add_argument("path", type=Path)
    s = sub.add_parser("shadow")
    s.add_argument("path", type=Path)
    s.add_argument("--peer", type=Path)
    s.add_argument("--events", type=Path,
                   help="optional immutable live event stream for quality/headroom checks")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--paper-ready", action="store_true",
                        help="fail unless preregistered minimum duration is met")
    args = parser.parse_args()
    if args.kind == "testnet":
        result = summarize_testnet(args.path)
        ready = (result["elapsed_hours"] >= 72 and not result["failed_rows"] and
                 not result["invalid_rows"] and
                 result["duplicate_message_ids"] == 0 and
                 result["unique_sequences"] == result["completed_observations"])
    else:
        result = summarize_shadow(args.path, args.events)
        if args.peer:
            result["independent_replica"] = compare_shadow(args.path, args.peer)
        ready = (result["distinct_utc_days"] >= 14 and
                 result["duplicate_configuration_days"] == 0 and
                 result["audit_passes"] == result["daily_configurations"] and
                 (result["max_recorded_collection_age_hours"] is None or
                  result["max_recorded_collection_age_hours"] <= 3.0) and
                 ("events" not in result or
                  (result["events"]["duplicate_event_keys"] == 0 and
                   not result["events"]["invalid_rows"])))
    result["paper_ready"] = ready
    if args.paper_ready and not ready:
        raise SystemExit("run has not reached its preregistered reporting threshold")
    payload = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload)
    print(payload, end="")


if __name__ == "__main__":
    main()
