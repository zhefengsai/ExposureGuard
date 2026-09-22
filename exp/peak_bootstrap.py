#!/usr/bin/env python3
"""Is 6.4x a point estimate with two-sided error, or a bound?

A reviewer asked for a confidence interval on the containment ratio. The ratio
is stock/B with B set by the observed peak day, so it is not a sample mean and
a symmetric interval would misdescribe it. Resampling days can only LOSE the
peak, never exceed it, so the ratio's sampling distribution is one-sided and
the reported value sits at its lower end.

The uncertainty that matters runs the other way: if the true demand peak is
higher than anything observed, B is too small and production defers legitimate
traffic. That direction is bounded by the demand-growth sensitivity, not by
this resampling.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

import numpy as np

import sim

HERE = os.path.dirname(os.path.abspath(__file__))
STOCK = 9_857_090.0
HEADROOM = 1.07
GAP_H = 6.0
REPS = 20_000
SEED = 20260921


def main() -> None:
    ev = sim.load()
    daily = defaultdict(float)
    for e in ev:
        daily[int(e["ts"] // sim.D)] += e["usd"]
    days = np.array(list(daily.values()))
    order = np.sort(days)[::-1]

    def ratio(peak):
        return STOCK / (peak * HEADROOM * (1 + GAP_H / 24))

    obs = ratio(days.max())
    rng = np.random.default_rng(SEED)
    peaks = days[rng.integers(0, len(days), (REPS, len(days)))].max(axis=1)
    r = ratio(peaks)

    out = {"schema": 1, "stock_usd": STOCK, "headroom": HEADROOM,
           "gap_hours": GAP_H, "reps": REPS, "seed": SEED,
           "observed_days": int(len(days)),
           "peak_usd": float(days.max()),
           "second_peak_usd": float(order[1]),
           "peak_over_second": float(order[0] / order[1]),
           "observed_ratio": float(obs),
           "resample_min_ratio": float(r.min()),
           "resample_median_ratio": float(np.median(r)),
           "resample_p95_ratio": float(np.percentile(r, 95)),
           "share_resamples_missing_peak": float((peaks < days.max()).mean())}
    json.dump(out, open(f"{HERE}/data/peak_bootstrap.json", "w"), indent=1)

    print(f"observed peak day ${days.max():,.0f} vs second ${order[1]:,.0f} "
          f"({out['peak_over_second']:.2f}x)")
    print(f"observed containment ratio  {obs:.2f}x")
    print(f"day-resampled ratio: min {r.min():.2f}x, median "
          f"{np.median(r):.2f}x, 95th {np.percentile(r, 95):.2f}x")
    print(f"resamples that miss the peak day: "
          f"{100*out['share_resamples_missing_peak']:.0f}%")
    print("\nwrote data/peak_bootstrap.json")


if __name__ == "__main__":
    main()
