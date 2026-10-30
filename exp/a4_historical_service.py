#!/usr/bin/env python3
"""Historical-cohort reservation ablation and common-budget clean service.

Reuse the atomic ledger, frozen cutoff, and 600-second/seven-day retry rule.
All budgets, shares, and token partitions are derived from training only.
The new attack is finite-rate, zero-history contention, not production traffic
or the earlier divisible-value full-trace sweep. Every alpha sees the same
attacker requests. Failed requests and retries never partially debit a bucket.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from matched_availability import D, Ledger, replay

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ATTACK_ROUTE = "__zero_history_attacker__"
STEP = 600.0
CHUNKS = 10
MULTIPLES = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


def resized(config, budget, alpha=None):
    """Retain train-only shares; change the total budget without suffix tuning."""
    cfg = copy.deepcopy(config)
    ratio = budget / cfg["budget"]
    cfg["caps"] = {group: cap * ratio for group, cap in cfg["caps"].items()}
    cfg["budget"] = budget
    if alpha is not None:
        assert cfg["policy"] == "guard"
        cfg["alpha"] = alpha
    cfg.pop("optimization", None)
    return cfg


def drain_replay(events, config, step=STEP, chunks=CHUNKS):
    """Offer B initially and B/D thereafter, in common atomic requests.

    The attacker has no reservation. An initial burst offers B, then each
    600-second tick offers B/144 in ten equal messages. Attacker arrivals run
    before retries and legitimate arrivals at a tie. Attack continues during
    the retry tail. Rejected attacker messages are dropped, not queued.
    """
    assert events and step > 0 and chunks > 0
    assert config["policy"] == "guard"
    assert ATTACK_ROUTE not in config["weights"]
    t0, end = events[0]["ts"], events[-1]["ts"] + 7 * D
    ledger = Ledger(config, t0)
    quantum = config["budget"] * step / D / chunks
    initial_chunks = round(config["budget"] / quantum)
    assert math.isclose(initial_chunks * quantum, config["budget"], rel_tol=1e-12)
    attack = {"route": ATTACK_ROUTE, "symbol": "__new__", "usd": quantum}
    pending, delivered, attack_arrivals = [], [], []
    index = immediate_n = impossible = 0
    immediate_value = attacker_value = offered_value = 0.0
    retry_tick, attack_tick = math.inf, t0
    last_t = t0
    while index < len(events) or pending:
        arrival = events[index]["ts"] if index < len(events) else math.inf
        t = min(arrival, retry_tick, attack_tick)
        if t > end:
            break
        last_t = t
        if attack_tick <= t:
            offered = initial_chunks if t == t0 else chunks
            for _ in range(offered):
                offered_value += quantum
                if ledger.admit(attack, t):
                    attacker_value += quantum
                    attack_arrivals.append((t, quantum))
            attack_tick = t + step
        if retry_tick <= t:
            still = []
            for event in pending:
                if ledger.admit(event, t):
                    delivered.append((event, t - event["ts"]))
                else:
                    still.append(event)
            pending = still
            retry_tick = t + STEP if pending else math.inf
        while index < len(events) and events[index]["ts"] == t:
            event = events[index]
            index += 1
            if ledger.admit(event, t):
                immediate_n += 1
                immediate_value += event["usd"]
                delivered.append((event, 0.0))
            elif ledger.possible(event):
                pending.append(event)
                if math.isinf(retry_tick):
                    retry_tick = t + STEP
            else:
                impossible += 1
    value = sum(event["usd"] for event in events)
    delayed = sum(delay > 0 for _, delay in delivered)
    admitted = attacker_value + sum(event["usd"] for event, _ in delivered)
    envelope = config["budget"] * (1 + (last_t - t0) / D)
    assert admitted <= envelope + 1e-6 * max(envelope, 1)
    assert immediate_n + delayed + len(pending) + impossible == len(events)
    # Also check the zero-history attacker's surplus-only envelope on every
    # arrival interval, independently of the legitimate-value admission bound.
    surplus = (1 - config.get("alpha", .8)) * config["budget"]
    attack_level, attack_last = surplus, t0
    for t, amount in attack_arrivals:
        attack_level = min(surplus, attack_level + (t - attack_last) * surplus / D)
        assert attack_level + 1e-6 >= amount
        attack_level -= amount
        attack_last = t
    return {"messages": len(events), "value_usd": value,
            "immediate_messages": immediate_n,
            "immediate_value_fraction": immediate_value / value,
            "immediate_message_fraction": immediate_n / len(events),
            "delayed_then_delivered": delayed,
            "undelivered_tail": len(pending) + impossible,
            "delivered_value_fraction": sum(event["usd"] for event, _ in delivered) / value,
            "attacker_admitted_usd": attacker_value,
            "attacker_offered_usd": offered_value,
            "elapsed_s": last_t - t0, "total_admitted_usd": admitted,
            "accounting_envelope_usd": envelope}


def experiment():
    audit_path = DATA / "a2_ethereum_event_audit.json"
    sensitivity_path = DATA / "a2_recovery_sensitivity.json"
    audit = json.loads(audit_path.read_text())
    sensitivity = json.loads(sensitivity_path.read_text())
    from datetime import datetime
    cutoff = datetime.fromisoformat(sensitivity["fixed_cutoff_utc"]).timestamp()
    events = sorted((e for e in audit["events"] if e["status"] == "recovered"),
                    key=lambda e: e["ts"])
    train = [e for e in events if e["ts"] < cutoff]
    test = [e for e in events if e["ts"] >= cutoff]
    original = sensitivity["variants"]["historical_expanded"]["optimized_policies"]
    guard = original["guard"]["config"]
    budget = guard["budget"]
    # The anchor was optimized on training only, not chosen from this sweep.
    common = []
    for multiple in MULTIPLES:
        configs = {name: resized(original[name]["config"], budget * multiple)
                   for name in ("guard", "optimized_static_token", "pooled")}
        common.append({"multiple": multiple, "budget_usd": budget * multiple,
                       "six_hour_envelope_usd": budget * multiple * 1.25,
                       "rows": {name: {"training": replay(train, cfg),
                                       "test": replay(test, cfg)}
                                for name, cfg in configs.items()}})
    ablation = []
    for alpha in (0., .4, .8, 1.):
        cfg = resized(guard, budget, alpha)
        clean = replay(test, cfg)
        drain = drain_replay(test, cfg)
        ablation.append({"alpha": alpha, "budget_usd": budget,
                         "clean": clean, "drain": drain,
                         "drain_immediate_over_clean_immediate":
                         drain["immediate_value_fraction"] / clean["immediate_value_fraction"]})
    return {"schema": 1, "input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                           for p in (audit_path, sensitivity_path)},
            "cohort": "historical Ethereum reconstruction; unresolved values excluded",
            "fixed_cutoff_utc": sensitivity["fixed_cutoff_utc"],
            "train_messages": len(train), "test_messages": len(test),
            "anchor_budget_usd": budget, "anchor_rule": "minimum guard training budget at alpha=0.8",
            "common_budget_rule": "predeclared multipliers; frozen train-only weights and static-token capacity proportions; no test tuning",
            "attack": {"zero_history_route": ATTACK_ROUTE, "step_s": STEP,
                       "chunks_per_step": CHUNKS, "initial_offered_usd": budget,
                       "offered_refill_usd_per_day": budget,
                       "tie_order": "attacker, pending retry, legitimate arrival",
                       "retry_step_s": STEP, "retry_tail_days": 7},
            "limits": "offline atomic replay, not EVM execution or production attack; historical daily quotes are not a causal governance-price-update policy; static proportions follow training minima, not all possible partitions; no static-token drain comparison; attacker is assumed explicitly metered with a valid price policy and no paid training history; original full-trace divisible-value sweep is separate",
            "common_budget": common, "reservation_ablation": ablation}


def main():
    result = experiment()
    dest = DATA / "a4_historical_service.json"
    dest.write_text(json.dumps(result, indent=2) + "\n")
    for row in result["common_budget"]:
        print("budget multiple", row["multiple"],
              {name: round(r["test"]["immediate_value_fraction"] * 100, 3)
               for name, r in row["rows"].items()})
    for row in result["reservation_ablation"]:
        print("alpha", row["alpha"], "clean", row["clean"]["immediate_value_fraction"],
              "drain", row["drain"]["immediate_value_fraction"],
              "retained", row["drain_immediate_over_clean_immediate"])
    print("wrote", dest)


if __name__ == "__main__":
    main()
