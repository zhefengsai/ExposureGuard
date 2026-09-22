#!/usr/bin/env python3
"""Sensitivity experiments for demand drift, pricing error, and adaptive abuse.

All dynamic experiments freeze the first 60 days of Ethereum as the controller's
training window and replay the held-out suffix.  This avoids selecting and
evaluating a configuration on the same traffic.  The capital-recycling and
history-poisoning rows are explicit analytical stress cases, not observations.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from oos_calibrate import (ALPHA, COLLATERAL, D, HEADROOM, TRAIN_DAYS,
                           load_eth, peak_daily, simulate_fixed, usage_weights)

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"


def fixed_split():
    ev = load_eth()
    cut = ev[0]["ts"] + TRAIN_DAYS * D
    train = [e for e in ev if e["ts"] < cut]
    test = [e for e in ev if e["ts"] >= cut]
    peak, _, _ = peak_daily(train)
    budget = round(peak * HEADROOM, -3)
    routes = {e["route"] for e in ev}
    return train, test, budget, usage_weights(train, routes)


def replay_scaled(test, budget, weights, scale):
    rows = [dict(e, usd=float(e["usd"]) * scale) for e in test]
    out, blocked, n = simulate_fixed(rows, budget, ALPHA, [], weights)
    delayed = [d for _, d in out if d > 0]
    delivered = len(out)
    return {
        "scale": scale,
        "messages": n,
        "delivered_within_7d": delivered,
        "deferred": len(delayed),
        "deferral_rate": len(delayed) / max(n, 1),
        "undelivered_7d": blocked,
        "delivery_fraction_7d": delivered / max(n, 1),
    }


def main():
    train, test, budget, weights = fixed_split()
    growth = [replay_scaled(test, budget, weights, x)
              for x in (1.0, 1.25, 1.5, 2.0, 3.0)]

    # Conservative prices over-account by q and therefore have exactly the same
    # admission effect as multiplying demand by q.  Under-pricing by P preserves
    # the accounting invariant but multiplies its real-value interpretation.
    price_over = [replay_scaled(test, budget, weights, q)
                  for q in (1.0, 1.25, 1.5, 2.0)]
    envelope_6h = budget * 1.25
    price_under = [
        {"max_underpricing_factor_P": p,
         "accounted_envelope_usd": envelope_6h,
         "real_value_envelope_usd": envelope_6h * p,
         "reduction_vs_collateral": COLLATERAL / (envelope_6h * p)}
        for p in (1.0, 1.25, 2.0, 5.0, 10.0)
    ]

    # An attacker can earn history before root failure. x is adversarial usage
    # as a fraction of legitimate training usage. Its normalized floor weight is
    # x/(1+x); after failure it can take that floor and all shared surplus.
    poison = []
    for x in (0.0, 0.001, 0.01, 0.05, 0.1, 0.5, 1.0):
        weight = x / (1.0 + x)
        capture = (1.0 - ALPHA) + ALPHA * weight
        poison.append({"adversarial_training_usage_fraction": x,
                       "earned_floor_weight": weight,
                       "post_failure_budget_capture_fraction": capture})

    # Delivery checks convert free fabricated calls into real transfers, but a
    # self-sender/receiver may recycle capital. Perfect recycling needs only the
    # refill-rate flow times the cross-chain round-trip latency in flight.
    refill_per_day = (1.0 - ALPHA) * budget
    recycling = []
    for latency_h in (0.25, 1, 6, 24):
        capital = refill_per_day * latency_h / 24.0
        recycling.append({"round_trip_hours": latency_h,
                          "sustainable_drain_usd_per_day": refill_per_day,
                          "minimum_capital_in_flight_usd": capital,
                          "capital_as_fraction_of_budget": capital / budget})

    out = {
        "mode": "held_out_trace_and_analytical_stress",
        "train_days": TRAIN_DAYS,
        "train_messages": len(train),
        "test_messages": len(test),
        "budget_usd_per_day": budget,
        "alpha": ALPHA,
        "demand_growth": growth,
        "conservative_price_overestimate": price_over,
        "price_underestimate_real_envelope": price_under,
        "history_poisoning": poison,
        "perfect_capital_recycling": recycling,
        "notes": [
            "Demand and conservative-price rows replay held-out traffic with frozen train parameters.",
            "Underpricing, history poisoning, and recycling rows are analytical stress cases.",
            "Capital in flight is not capital lost; fees and latency are omitted, favoring the attacker."
        ],
    }
    path = DATA / "robustness_sensitivity.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
