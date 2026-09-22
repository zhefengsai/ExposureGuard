#!/usr/bin/env python3
"""Per-token caps on the same trace: the Celer-style discipline, measured.

Related Work argues that per-token caps do not compose into a root-wide bound.
That argument was previously only qualitative here, and the per-route baseline
does not stand in for it: Ethereum carries 152 default routes but only 72
distinct symbols, so one token spans many routes (USDC 34, USDT 24).  A
per-token cap therefore binds routes together and is strictly tighter than
per-route caps, which makes it the honest comparison to draw.

Sizing follows baselines.py exactly: each bucket is 2x its own measured peak
day, the configuration under which no legitimate message is deferred.
"""
from __future__ import annotations

import json, os
from collections import defaultdict
from datetime import datetime

import sim

HERE = os.path.dirname(os.path.abspath(__file__))
STOCK = 9_857_090.0                # audited Ethereum collateral, as baselines.py
GAPS = [("30 min", .5 / 24), ("1 h", 1 / 24), ("6 h", .25), ("1 day", 1.), ("7 days", 7.)]


def deferral(events, key, mult):
    """Independent buckets, one per group at `mult` x its own peak, replayed."""
    caps = cap_sum(events, key, mult)
    b = {k: sim.Bucket(c, events[0]["ts"]) for k, c in caps.items()}
    n_def = 0
    for e in events:
        if b[key(e)].take(e["ts"], e["usd"]) < e["usd"] - 1e-9:
            n_def += 1
    return sum(caps.values()), n_def / len(events)


def peak_day_spread(events, key):
    """How many distinct days the group peaks fall on, and the sum/aggregate ratio."""
    daily = defaultdict(lambda: defaultdict(float))
    agg = defaultdict(float)
    for e in events:
        d = int(e["ts"] // sim.D)
        daily[key(e)][d] += e["usd"]
        agg[d] += e["usd"]
    peaks = {k: max(v.values()) for k, v in daily.items()}
    days = {k: max(v, key=v.get) for k, v in daily.items()}
    return (len(peaks), len(set(days.values())),
            sum(peaks.values()), max(agg.values()))


def cap_sum(events, key, mult=2.0):
    """Caps exactly as sim.run's per-route policy: max(2x own peak day, 1% of B),
    with days bucketed on the refill window so the two baselines are comparable."""
    daily = defaultdict(lambda: defaultdict(float))
    for e in events:
        daily[key(e)][int(e["ts"] // sim.D)] += e["usd"]
    caps = {k: max(mult * max(v.values()), sim.B_DEFAULT * 0.01)
            for k, v in daily.items()}
    return caps


def envelope(cap, d):
    return min(STOCK, cap * (1 + d))


def main() -> None:
    ev = sim.load()
    meta = {r["router"][2:].lower(): r
            for r in json.load(open(f"{HERE}/data/hl_ism_ethereum.json"))}
    for e in ev:
        e["symbol"] = str(meta.get(e["route"], {}).get("symbol")
                          or e.get("symbol") or "UNK")

    route_caps = cap_sum(ev, lambda e: e["route"])
    token_caps = cap_sum(ev, lambda e: e["symbol"])
    route_peaks, token_peaks = route_caps, token_caps
    per_route_sum = sum(route_caps.values())
    per_token_sum = sum(token_caps.values())
    B = sim.B_DEFAULT

    routes_per_token = defaultdict(set)
    for e in ev:
        routes_per_token[e["symbol"]].add(e["route"])
    fan = {k: len(v) for k, v in sorted(routes_per_token.items(),
                                        key=lambda kv: -len(kv[1]))}

    print(f"active routes {len(route_peaks)}, distinct tokens {len(token_peaks)}")
    print(f"  routes per token (top): {list(fan.items())[:6]}")
    print(f"\nper-route cap sum  ${per_route_sum:,.0f}  ({per_route_sum/B:.2f}x B)")
    print(f"per-token cap sum  ${per_token_sum:,.0f}  ({per_token_sum/B:.2f}x B)")
    print(f"per-root budget B  ${B:,.0f}\n")

    hdr = "%8s %14s %14s %14s %12s" % ("gap", "per-route", "per-token", "ExposureGuard", "EG vs token")
    print(hdr); print("-" * len(hdr))
    rows = []
    for lbl, d in GAPS:
        pr, pt, eg = envelope(per_route_sum, d), envelope(per_token_sum, d), envelope(B, d)
        rows.append({"gap": lbl, "delta_days": d, "per_route": pr, "per_token": pt,
                     "exposureguard": eg, "eg_vs_per_token": pt / eg,
                     "eg_vs_per_route": pr / eg, "eg_vs_pause": STOCK / eg})
        print("%8s %14s %14s %14s %10.1fx" % (
            lbl, f"{pr:,.0f}", f"{pt:,.0f}", f"{eg:,.0f}", pt / eg))

    HEAD = 1.07
    matched_sum, matched_def = deferral(ev, lambda e: e["symbol"], HEAD)
    n_tok, n_days, sum_peaks, agg_peak = peak_day_spread(ev, lambda e: e["symbol"])
    matched_6h = envelope(matched_sum, .25)
    eg_6h = envelope(B, .25)
    print(f"\nmatched headroom ({HEAD}x own peak, as B is {HEAD}x the aggregate peak):")
    print(f"  per-token cap sum ${matched_sum:,.0f}   6h ${matched_6h:,.0f}"
          f"   deferral {100*matched_def:.2f}%   -> {matched_6h/eg_6h:.2f}x")
    print(f"\nmultiplexing: {n_tok} tokens peak on {n_days} distinct days; "
          f"sum of peaks ${sum_peaks:,.0f} = {sum_peaks/agg_peak:.2f}x the "
          f"aggregate peak ${agg_peak:,.0f}")

    json.dump({"stock": STOCK, "B_per_root": B,
               "matched_headroom": {
                   "multiple_of_own_peak": HEAD,
                   "per_token_cap_sum": matched_sum,
                   "per_token_6h": matched_6h,
                   "per_token_deferral_rate": matched_def,
                   "eg_advantage_6h": matched_6h / eg_6h},
               "multiplexing": {
                   "tokens": n_tok, "distinct_peak_days": n_days,
                   "sum_of_token_peaks": sum_peaks, "aggregate_peak": agg_peak,
                   "ratio": sum_peaks / agg_peak},
               "active_routes": len(route_peaks), "distinct_tokens": len(token_peaks),
               "routes_per_token_top": dict(list(fan.items())[:10]),
               "per_route_cap_sum": per_route_sum, "per_token_cap_sum": per_token_sum,
               "per_token_over_B": per_token_sum / B,
               "per_token_over_per_route": per_token_sum / per_route_sum,
               "sizing": "each bucket at 2x its own measured peak day; no legitimate "
                         "message is deferred under either baseline",
               "rows": rows},
              open(f"{HERE}/data/pertoken_baseline.json", "w"), indent=2)
    print("\nwrote data/pertoken_baseline.json")


if __name__ == "__main__":
    main()
