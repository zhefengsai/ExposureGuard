#!/usr/bin/env python3
"""Compare simple controller calibration policies on common shadow days.

The experiment is intentionally not a forecasting contest.  It asks whether
the current 60-day rolling maximum buys fewer overload/deferral events at the
cost of a larger exposure budget and how much churn it induces relative to
shorter-memory or percentile policies.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import egctl

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DAY = egctl.DAY

POLICIES = {
    "max60_1.07": {"window": 60, "kind": "max", "headroom": 1.07},
    "max30_1.07": {"window": 30, "kind": "max", "headroom": 1.07},
    "p99_60_1.07": {"window": 60, "kind": "p99", "headroom": 1.07},
    "decayed_max60_1.07": {"window": 60, "kind": "decayed", "headroom": 1.07},
}


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    pos = q * (len(xs) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] if lo == hi else xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def daily(rows: list[dict]) -> list[tuple[str, float]]:
    totals: dict[str, float] = defaultdict(float)
    for r in rows:
        totals[r["t"][:10]] += float(r["usd"])
    return sorted(totals.items())


def size(rows: list[dict], policy: dict, as_of: float) -> float:
    values = daily(rows)
    if not values:
        return 0.0
    if policy["kind"] == "max":
        base = max(v for _, v in values)
    elif policy["kind"] == "p99":
        base = percentile([v for _, v in values], 0.99)
    else:
        # A 30-day half-life lets old spikes decay while retaining a hard,
        # easily audited statistic rather than fitting a predictor.
        base = max(v * 0.5 ** (max(0.0, (as_of - datetime.strptime(
            d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) / DAY) / 30.0)
                   for d, v in values)
    return math.ceil(base * policy["headroom"] / 1000.0) * 1000.0


def evaluate(name: str, policy: dict, rows: list[dict], first: float,
             last: float) -> dict:
    results = []
    previous = None
    day = first
    while day < last:
        train = [r for r in rows
                 if day - policy["window"] * DAY <= r["ts"] < day]
        today = [r for r in rows if day <= r["ts"] < day + DAY]
        base_cfg = egctl.recommend(rows, alpha=0.8, headroom=1.0,
                                   lookback_days=policy["window"], as_of=day)
        for chain in egctl.CHAINS:
            cr = [r for r in train if r["chain"] == chain]
            b = size(cr, policy, day)
            base_cfg["chains"][chain]["budget_usd_per_day"] = b
        base_cfg["total_budget_usd_per_day"] = sum(
            c["budget_usd_per_day"] for c in base_cfg["chains"].values())
        deferred, value = egctl.replay_day(today, base_cfg)
        budget = base_cfg["total_budget_usd_per_day"]
        churn = None if previous is None or previous == 0 else abs(budget - previous) / previous
        results.append({"date": datetime.fromtimestamp(day, timezone.utc).date().isoformat(),
                        "messages": len(today), "deferred": deferred,
                        "deferred_value_usd": value, "budget_usd_per_day": budget,
                        "abs_budget_churn": churn})
        previous = budget
        day += DAY
    churn = [x["abs_budget_churn"] for x in results if x["abs_budget_churn"] is not None]
    return {
        "policy": name, **policy, "evaluation_days": len(results),
        "messages": sum(x["messages"] for x in results),
        "deferred": sum(x["deferred"] for x in results),
        "deferral_rate": sum(x["deferred"] for x in results) /
                         max(1, sum(x["messages"] for x in results)),
        "mean_budget_usd_per_day": sum(x["budget_usd_per_day"] for x in results) /
                                   max(1, len(results)),
        "max_budget_usd_per_day": max((x["budget_usd_per_day"] for x in results), default=0),
        "mean_abs_budget_churn": sum(churn) / max(1, len(churn)),
        "max_abs_budget_churn": max(churn, default=0),
        "daily": results,
    }


def main() -> None:
    rows = egctl.load_events()
    first = min(r["ts"] for r in rows) + 60 * DAY
    last = max(r["ts"] for r in rows) + 1
    out = {
        "mode": "historical_common_day_calibration_policy_comparison",
        "live_deployment": False,
        "common_start": datetime.fromtimestamp(first, timezone.utc).date().isoformat(),
        "common_end": datetime.fromtimestamp(last, timezone.utc).date().isoformat(),
        "policies": {name: evaluate(name, p, rows, first, last)
                     for name, p in POLICIES.items()},
        "note": "Lower budget strengthens containment; lower deferral and churn improve operability.",
    }
    path = DATA / "calibration_compare.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: {x: v for x, v in r.items() if x != "daily"}
                      for k, r in out["policies"].items()}, indent=2))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
