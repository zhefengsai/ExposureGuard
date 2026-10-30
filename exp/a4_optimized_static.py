#!/usr/bin/env python3
"""Train-only capacity minimization against an optimized static partition.

The legacy fixed-grid artifact remains unchanged. At 100% on-arrival training
service, each independent token bucket can be minimized separately. With a
fixed refill window D, its minimum capacity is max interval_value/(1+dt/D).
This supplies an interval certificate and an optimal static aggregate budget
within this policy class. Guard and pooled budgets use bracketed bisection.
All configurations are frozen before the held-out replay.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

from matched_availability import D, Ledger, fit, load, replay

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" if (HERE / "data").is_dir() else HERE.parent / "data"
CENT = .01


def arrival_feasible(events, config):
    ledger = Ledger(config, events[0]["ts"])
    return all(ledger.admit(e, e["ts"]) for e in events)


def interval_capacity(events):
    """Exact finite-trace interval maximum, up to floating-point arithmetic."""
    best, witness = 0., None
    for end, event in enumerate(events):
        value = 0.
        for start in range(end, -1, -1):
            value += events[start]["usd"]
            required = value / (1 + (event["ts"] - events[start]["ts"]) / D)
            if required > best:
                best, witness = required, {"start": start, "end": end,
                                           "value_usd": value,
                                           "duration_s": event["ts"] - events[start]["ts"]}
    return best, witness


def optimized_static(events, key="symbol"):
    groups = defaultdict(list)
    for event in events:
        groups[event[key]].append(event)
    caps, certificates = {}, {}
    for group, rows in sorted(groups.items()):
        lower, witness = interval_capacity(rows)
        caps[group] = math.ceil(lower / CENT) * CENT
        certificates[group] = {"lower_bound_usd": lower, "interval": witness}
    cfg = {"policy": "per_token" if key == "symbol" else "per_route",
           "caps": caps, "budget": sum(caps.values()), "weights": {}}
    assert arrival_feasible(events, cfg)
    cfg["optimization"] = {"method": "per-group interval maximum; upward cent rounding",
                            "lower_bound_budget_usd": sum(r["lower_bound_usd"] for r in certificates.values()),
                            "max_rounding_gap_usd": len(caps) * CENT,
                            "certificates": certificates}
    return cfg


def optimized_shared(events, policy="guard"):
    base = fit(events, policy, 1.)["budget"]
    lo, hi = 0., base
    while not arrival_feasible(events, fit(events, policy, hi / base)):
        hi *= 2
    while hi - lo > CENT / 4:
        mid = (lo + hi) / 2
        if arrival_feasible(events, fit(events, policy, mid / base)):
            hi = mid
        else:
            lo = mid
    budget = math.ceil(hi / CENT) * CENT
    cfg = fit(events, policy, budget / base)
    assert arrival_feasible(events, cfg)
    cfg["optimization"] = {"method": "bisection on all-arrival training feasibility; upward cent rounding",
                            "infeasible_lower_budget_usd": lo,
                            "feasible_upper_budget_usd": cfg["budget"],
                            "absolute_search_tolerance_usd": CENT}
    return cfg


def configurations(train):
    return {"guard": optimized_shared(train),
            "optimized_static_token": optimized_static(train),
            "optimized_static_route": optimized_static(train, "route"),
            "pooled": optimized_shared(train, "pooled")}


def measured(train, test, configs):
    rows = {}
    known_symbols = {e["symbol"] for e in train}
    known_test = [e for e in test if e["symbol"] in known_symbols]
    for name, cfg in configs.items():
        rows[name] = {"config": cfg, "budget_usd": cfg["budget"],
                      "six_hour_envelope_usd": cfg["budget"] * 1.25,
                      "training": replay(train, cfg), "test": replay(test, cfg),
                      "known_symbol_only_test": replay(known_test, cfg)}
        assert rows[name]["training"]["immediate_messages"] == len(train)
    return rows


def changes(old, new):
    """Configuration differences, not measured human time or transaction gas."""
    if new["policy"] == "guard":
        before = {"B": old["budget"], **{r: round(w * 10000) for r, w in old["weights"].items()}}
        after = {"B": new["budget"], **{r: round(w * 10000) for r, w in new["weights"].items()}}
    else:
        before, after = old["caps"], new["caps"]
    keys = set(before) | set(after)
    changed = [k for k in keys if before.get(k) != after.get(k)]
    substantial = [k for k in keys if abs(after.get(k, 0) - before.get(k, 0)) >
                   .01 * max(abs(before.get(k, 0)), .01)]
    return {"parameters_after": len(after), "changed_parameters": len(changed),
            "changed_over_one_percent": len(substantial),
            "added_parameters": len(set(after) - set(before)),
            "removed_parameters": len(set(before) - set(after))}


def experiment(events):
    start = events[0]["ts"]
    train = [e for e in events if e["ts"] < start + 60 * D]
    test = [e for e in events if e["ts"] >= start + 60 * D]
    configs = configurations(train)
    rows = measured(train, test, configs)
    ratio = rows["optimized_static_token"]["six_hour_envelope_usd"] / rows["guard"]["six_hour_envelope_usd"]
    windows, previous = [], None
    for offset in (0, 14, 28, 42):
        prefix = [e for e in events if start + offset * D <= e["ts"] < start + (offset + 60) * D]
        suffix = [e for e in events if start + (offset + 60) * D <= e["ts"] < start + (offset + 74) * D]
        if not prefix or not suffix:
            continue
        frozen = {"guard": optimized_shared(prefix), "optimized_static_token": optimized_static(prefix)}
        result = measured(prefix, suffix, frozen)
        delta = {name: changes(previous[name], cfg) for name, cfg in frozen.items()} if previous else None
        windows.append({"start_day": offset, "train_messages": len(prefix), "test_messages": len(suffix),
                        "rows": result, "configuration_changes_from_previous": delta})
        previous = frozen
    unseen = [e for e in test if e["symbol"] not in {r["symbol"] for r in train}]
    return {"schema": 1, "target": "100% immediate training messages and value",
            "selection": "first 60 days only; optimized configs frozen before suffix; initially full buckets",
            "policy_class": "static per-group C with refill C/D, D=one day; guard alpha=0.8 with fixed training usage weights",
            "limits": "frozen symbol grouping; no new-group capacity in static policies; floating-point USD rather than bit-exact EVM; rolling windows overlap",
            "train_messages": len(train), "test_messages": len(test), "rows": rows,
            "static_token_over_guard": ratio, "new_symbol_test_messages": len(unseen),
            "new_symbol_test_value_usd": sum(e["usd"] for e in unseen), "rolling_windows": windows}


def main():
    result = experiment(load("ethereum"))
    result["input_sha256"] = hashlib.sha256((DATA / "events_ethereum.json").read_bytes()).hexdigest()
    path = DATA / "a4_optimized_static.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    for name, row in result["rows"].items():
        print(name, f"envelope=${row['six_hour_envelope_usd']:,.2f}",
              f"test messages={row['test']['immediate_message_fraction']:.6%}",
              f"test value={row['test']['immediate_value_fraction']:.6%}",
              f"pending={row['test']['undelivered_tail']}")
    print("static_token_over_guard", result["static_token_over_guard"])
    print("wrote", path)


if __name__ == "__main__":
    main()
