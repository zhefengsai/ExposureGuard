#!/usr/bin/env python3
"""How often must the operator recompute route weights?

The data plane is O(1) per message, but provisioning floors is linear in the
batch (~26.7k gas per route, Section 5).  That makes refresh frequency a real
operating cost, so this sweeps it: weights are recomputed from a trailing
window at each period boundary and then held fixed, rather than being derived
once from the whole trace as in the alpha sweep.

Capacity is never minted at a boundary: each bucket is refilled to the boundary
instant, its capacity replaced, and its level clamped down to the new capacity.

Regenerate with:  python3 refresh_sweep.py
"""
from __future__ import annotations

import json, os
from collections import defaultdict
import sim, sim2

HERE = os.path.dirname(os.path.abspath(__file__))
ZERO = "0x" + "0" * 40
DAY = 86400.0
TRAIL = 60 * DAY          # trailing window used by the deployed 60-day policy
GAS_PER_ROUTE = 26_700    # Section 5, measured
ALPHA = 0.8
MATERIAL = 1e-4           # weight delta below this needs no on-chain write

PERIODS = [("1 d", 1), ("3 d", 3), ("7 d", 7), ("14 d", 14),
           ("30 d", 30), ("never", None)]


def run(events, B, alpha, period_days, adversary, all_ids):
    """Replay with weights refreshed every `period_days` (None = fixed at t0)."""
    t0, tend = events[0]["ts"], events[-1]["ts"]
    adv = sim2.adversary_stream(adversary, t0, tend, B, 0)
    stream = sorted(list(events) + adv, key=lambda e: e["ts"])
    adv_names = {e["route"] for e in adv}
    universe = set(all_ids) | {e["route"] for e in stream}

    def weights_at(t):
        seen = [e for e in events if t - TRAIL <= e["ts"] < t]
        if not seen:
            seen = [e for e in events if e["ts"] < t]
        w = sim.weights(seen, universe, "usage", len(universe))
        s = sum(w.values()) or 1.0
        return {r: v / s for r, v in w.items()}

    w = weights_at(t0 + 1e-9)
    floors = {r: alpha * B * w.get(r, 0.0) for r in universe}
    fb = {r: sim.Bucket(floors[r], t0) for r in universe}
    surplus = sim.Bucket((1 - alpha) * B, t0)

    next_refresh = t0 + period_days * DAY if period_days else float("inf")
    writes = refreshes = 0
    adm = defaultdict(float)
    for e in stream:
        while e["ts"] >= next_refresh:
            nw = weights_at(next_refresh)
            changed = sum(1 for r in universe
                          if abs(nw.get(r, 0.0) - w.get(r, 0.0)) > MATERIAL)
            for r in universe:                      # re-cap without minting
                b = fb[r]
                b._refill(next_refresh)
                b.cap = alpha * B * nw.get(r, 0.0)
                b.level = min(b.level, b.cap)
            w = nw
            writes += changed
            refreshes += 1
            next_refresh += period_days * DAY
        r, need = e["route"], e["usd"]
        got = fb[r].take(e["ts"], need)
        if got < need:
            got += surplus.take(e["ts"], need - got)
        adm[r] += got
    return adm, adv_names, writes, refreshes


def main() -> None:
    ev = sim.load()
    demand = defaultdict(float)
    for e in ev:
        demand[e["route"]] += e["usd"]
    all_ids = [r["router"][2:].lower()
               for r in json.load(open(f"{HERE}/data/hl_ism_ethereum.json"))
               if r.get("ism") == ZERO]
    B = sim.B_DEFAULT
    days = (ev[-1]["ts"] - ev[0]["ts"]) / DAY

    def legit(adm, adv):
        return 100 * sim.summarize(adm, demand,
                                   exclude=tuple(adv) + ("__dormant__",))[0]

    rows = []
    for label, p in PERIODS:
        adm, _, writes, n = run(ev, B, ALPHA, p, "none", all_ids)
        clean = legit(adm, set())
        adm, advn, _, _ = run(ev, B, ALPHA, p, "drain", all_ids)
        drain = legit(adm, advn)
        adm, advn, _, _ = run(ev, B, ALPHA, p, "flood", all_ids)
        took = sum(adm.get(a, 0.0) for a in advn)
        capture = 100 * took / days / B
        rows.append({"period": label, "period_days": p, "refreshes": n,
                     "route_writes": writes,
                     "gas_total": writes * GAS_PER_ROUTE,
                     "gas_per_day": writes * GAS_PER_ROUTE / days,
                     "clean_pct": clean, "drain_pct": drain,
                     "flood_capture_pct": capture})

    print(f"trace {days:.0f} d, B=${B:,.0f}, alpha={ALPHA}, "
          f"{GAS_PER_ROUTE//1000}k gas/route write\n")
    hdr = "%-8s %9s %9s %11s %9s %9s %9s" % (
        "refresh", "refreshes", "writes", "gas/day", "clean%", "drain%", "capture%")
    print(hdr); print("-" * len(hdr))
    for r in rows:
        print("%-8s %9s %9d %11s %9.1f %9.1f %9.1f" % (
            r["period"], r["refreshes"] or "-", r["route_writes"],
            f"{r['gas_per_day']:,.0f}", r["clean_pct"], r["drain_pct"],
            r["flood_capture_pct"]))
    json.dump({"trace_days": days, "B": B, "alpha": ALPHA,
               "gas_per_route_write": GAS_PER_ROUTE,
               "material_weight_delta": MATERIAL,
               "trailing_window_days": TRAIL / DAY, "rows": rows},
              open(f"{HERE}/data/refresh_sweep.json", "w"), indent=2)
    print("\nwrote data/refresh_sweep.json")


if __name__ == "__main__":
    main()
