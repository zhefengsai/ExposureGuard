#!/usr/bin/env python3
"""Four predeclared 60-day-fit / 14-day-test rolling-origin checks.

This is a temporal robustness check of the matched-availability comparison,
not an independent sample and not a population confidence interval.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from matched_availability import D, GRID, HERE, fit, load, replay

START_DAYS = (0, 14, 28, 42)
POLICIES = ("guard", "per_token", "coordinated_token_peak", "pooled")
TARGET = 0.99


def main():
    events = load("ethereum")
    origin = events[0]["ts"]
    rows = []
    for start_day in START_DAYS:
        start = origin + start_day * D
        split = start + 60 * D
        stop = split + 14 * D
        train = [e for e in events if start <= e["ts"] < split]
        test = [e for e in events if split <= e["ts"] < stop]
        assert train and test
        per_policy = {}
        for policy in POLICIES:
            candidates = []
            for multiple in GRID:
                frozen = fit(train, policy, multiple)
                calibrated = replay(train, frozen)
                if min(calibrated["immediate_message_fraction"],
                       calibrated["immediate_value_fraction"]) >= TARGET - 1e-12:
                    candidates.append((multiple, frozen))
            if not candidates:
                per_policy[policy] = {"feasible_on_grid": False}
                continue
            multiple, frozen = candidates[0]
            per_policy[policy] = {
                "feasible_on_grid": True,
                "multiple": multiple,
                "six_hour_envelope_usd": frozen["budget"] * 1.25,
                "heldout": replay(test, frozen),
            }
        rows.append({
            "start_day": start_day,
            "train_start_utc": datetime.fromtimestamp(start, timezone.utc).isoformat(),
            "test_start_utc": datetime.fromtimestamp(split, timezone.utc).isoformat(),
            "train_messages": len(train),
            "test_messages": len(test),
            "policies": per_policy,
        })
    result = {
        "schema": 1,
        "input_sha256": hashlib.sha256((HERE / "data/events_ethereum.json").read_bytes()).hexdigest(),
        "windows": rows,
        "policy": "smallest fixed-grid multiple attaining 99% immediate message and value service on each training prefix; no held-out selection",
        "limits": "overlapping rolling windows on one chain; historical, not prospective; token symbol groups; 7-day retry tail; no causal or population interval",
    }
    (HERE / "data/rolling_matched.json").write_text(json.dumps(result, indent=2) + "\n")
    for row in rows:
        print("origin", row["start_day"], "test_messages", row["test_messages"])
        for policy, value in row["policies"].items():
            if value["feasible_on_grid"]:
                print(" ", policy, value["multiple"],
                      round(value["six_hour_envelope_usd"]),
                      round(100 * value["heldout"]["immediate_value_fraction"], 2),
                      value["heldout"]["undelivered_tail"])
            else:
                print(" ", policy, "infeasible")


if __name__ == "__main__":
    main()
