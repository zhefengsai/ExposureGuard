#!/usr/bin/env python3
"""Compare frozen, historically repriced, and expanded Ethereum cohorts.

The original trace is retained. Alternative cohorts use one historical daily
price convention and actual local transfer amounts from the event audit.
All variants use the original UTC train/test cutoff. The pause reference is
held fixed for a sizing sensitivity; it is not a historical loss series.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from a4_optimized_static import configurations, measured
from matched_availability import D, fit, load, replay

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" if (HERE / "data").is_dir() else HERE.parent / "data"


def peak(events):
    daily = defaultdict(float)
    for event in events:
        daily[event["t"][:10]] += event["usd"]
    day = max(daily, key=daily.get)
    return {"value_usd": daily[day], "day": day}


def analyze(events, cutoff, stock):
    events = sorted(events, key=lambda e: e["ts"])
    train = [e for e in events if e["ts"] < cutoff]
    test = [e for e in events if e["ts"] >= cutoff]
    configs = configurations(train)
    rows = measured(train, test, configs)
    training_peak = peak(train)
    budget = round(training_peak["value_usd"] * 1.07, -3)
    default = fit(train, "guard", 1.)
    default["budget"] = budget
    default["caps"] = {"root": budget}
    return {"messages": len(events), "train_messages": len(train), "test_messages": len(test),
            "value_usd": sum(e["usd"] for e in events), "peak": peak(events),
            "training_peak": training_peak, "default_budget_usd": budget,
            "default_six_hour_envelope_usd": budget * 1.25,
            "fixed_stock_reference_over_default": stock / (budget * 1.25),
            "default_test": replay(test, default), "optimized_policies": rows,
            "optimized_static_token_over_guard": rows["optimized_static_token"]["budget_usd"] / rows["guard"]["budget_usd"]}


def main():
    audit_path = DATA / "a2_ethereum_event_audit.json"
    audit = json.loads(audit_path.read_text())
    original = load("ethereum")
    cutoff = original[0]["ts"] + 60 * D
    recovered = [dict(e, chain="ethereum", provenance="historical-local-transfer")
                 for e in audit["events"] if e["status"] == "recovered"]
    stock = json.loads((DATA / "detector_baseline.json").read_text())["stock_usd"]
    variants = {"frozen_snapshot": original,
                "historical_original_cohort": [e for e in recovered if e["originally_priced"]],
                "historical_expanded": recovered}
    result = {"schema": 1, "fixed_cutoff_utc": datetime.fromtimestamp(cutoff, timezone.utc).isoformat(),
              "price_conventions": {"frozen_snapshot": "original scan-time quotes",
                                    "historical_original_cohort": audit["historical_pricing"],
                                    "historical_expanded": audit["historical_pricing"]},
              "fixed_stock_reference_usd": stock,
              "limits": "reconstructed cohorts exclude unresolved events; fixed stock reference is a sizing sensitivity, not historical loss; unknown message value is never set to zero",
              "audit_sha256": hashlib.sha256(audit_path.read_bytes()).hexdigest(),
              "variants": {name: analyze(rows, cutoff, stock) for name, rows in variants.items()},
              "unresolved_messages": audit["groups"]["all"]["messages"] - len(recovered)}
    # Compare local-unit reconstruction with the original collector's message
    # amount interpretation at the SAME historical quote, separating unit error
    # from price-time changes.
    comparable = [e for e in recovered if "raw_message" in e and e.get("decimals") is not None]
    scale_different = [e for e in comparable if int(e["raw_message"]) != int(e["raw_local"])]
    result["amount_scale_audit"] = {"comparable_messages": len(comparable),
                                     "different_raw_amounts": len(scale_different),
                                     "different_originally_priced": sum(e["originally_priced"] for e in scale_different),
                                     "original_50m_filter_crossings": sum(int(e["raw_message"]) / 10**e["decimals"] * e["price_usd"] > 50e6 for e in comparable)}
    # Pair complete transaction-route groups so partial batch recovery cannot
    # silently select different messages in the two valuation conventions.
    old_rows = json.loads((DATA / "chainlog_priced_ethereum.json").read_text())["events"]
    historical_old = [e for e in recovered if e["originally_priced"]]
    key = lambda e: (e["route"].lower().removeprefix("0x"), e["tx"].lower())
    old_counts, historical_counts = Counter(map(key, old_rows)), Counter(map(key, historical_old))
    complete = {k for k, n in historical_counts.items() if old_counts[k] == n}
    paired_old = [e for e in old_rows if key(e) in complete]
    paired_new = [e for e in historical_old if key(e) in complete]
    assert Counter(map(key, paired_old)) == Counter(map(key, paired_new))
    result["paired_revaluation"] = {"messages": len(paired_old),
                                    "selection": "only complete matched transaction-route groups",
                                    "snapshot_peak": peak(paired_old), "historical_peak": peak(paired_new),
                                    "snapshot_value_usd": sum(e["usd"] for e in paired_old),
                                    "historical_value_usd": sum(e["usd"] for e in paired_new)}
    path = DATA / "a2_recovery_sensitivity.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    for name, row in result["variants"].items():
        print(name, "messages", row["messages"], "peak", row["peak"],
              "default_ratio", row["fixed_stock_reference_over_default"],
              "optimized_token_over_guard", row["optimized_static_token_over_guard"])
    print("unresolved", result["unresolved_messages"])
    print("wrote", path)


if __name__ == "__main__":
    main()
