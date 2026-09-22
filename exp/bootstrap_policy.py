#!/usr/bin/env python3
"""What does it take to admit a cold route, and what does it cost?

The held-out evaluation leaves one $615k transfer unsettled after seven days,
and the manuscript attributes that to the absence of a bootstrap floor.  This
script tests that attribution instead of asserting it, by sweeping the two
levers an operator actually has:

  * the bootstrap policy, which decides what floor a route with little or no
    history receives -- earned (the deployed design, zero), a flat minimum
    granted to every route in the universe, or a share proportional to the
    route's on-chain collateral;
  * the reservation fraction alpha, which decides how much of B is reserved as
    floors at all and how much stays work-conserving surplus.

Each configuration is replayed on the same frozen 60-day Ethereum policy as
oos_calibrate.py, and reported against four quantities that trade off against
one another: whether the cold transfer settles, deferral and tail delay under
clean load, and how much capacity a fresh Sybil route can claim.

The Sybil column is the reason this is not simply a matter of granting floors.
A flat minimum is mintable: a new deployment qualifies for it by existing.  A
collateral-proportional floor is not free to mint, but it is only as good as
the cost of posting the collateral, which we report rather than assume
prohibitive.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

import oos_calibrate as O

HERE = os.path.dirname(os.path.abspath(__file__))
ZERO = "0x" + "0" * 40
COLD_USD = 615_034.0                 # the held-out transfer that does not settle
ALPHAS = (0.0, 0.4, 0.8)
BETAS = (0.05, 0.25)
SYBILS = 10                          # fresh routes an adversary deploys
GAP_H = 6.0


def universe_and_weights(train, ids, test):
    used = defaultdict(float)
    for e in train:
        used[e["route"]] += e["usd"]
    uni = set(ids) | {e["route"] for e in train} | {e["route"] for e in test}
    tot = sum(used.values()) or 1.0
    return uni, {r: used.get(r, 0.0) / tot for r in uni}


def collateral_shares(uni):
    st = json.load(open(f"{HERE}/data/stock_multi.json"))["per_route"]["ethereum"]
    v = {r: float(st.get(r, 0.0)) for r in uni}
    tot = sum(v.values()) or 1.0
    return {r: x / tot for r, x in v.items()}


def mix(usage, other, beta):
    return {r: (1 - beta) * usage.get(r, 0.0) + beta * other.get(r, 0.0)
            for r in usage}


def cold_wait_hours(out, pending_ev):
    for e, d in out:
        if abs(e["usd"] - COLD_USD) < 1.0:
            return d / 3600.0
    for e in pending_ev:
        if abs(e["usd"] - COLD_USD) < 1.0:
            return None                       # never settled inside the horizon
    raise SystemExit("cold-route event not found in the held-out replay")


def sybil_reachable(policy, beta, alpha, B, n_uni, coll_share_fresh=0.0):
    """Value k fresh routes can draw over a six-hour gap, as a fraction of B.

    Surplus is reachable by anyone, so it is the floor of this quantity; what a
    bootstrap policy adds is whatever floor a route with no history receives.
    """
    burst = 1.0 + GAP_H / 24.0
    surplus = (1 - alpha) * B
    if policy == "earned":
        f_fresh = 0.0
    elif policy == "flat":
        f_fresh = beta * alpha * B / max(n_uni + SYBILS, 1)
    else:                                     # collateral-proportional
        f_fresh = beta * alpha * B * coll_share_fresh
    return (surplus + SYBILS * f_fresh) * burst / B


def main() -> None:
    ev = O.load_chain("ethereum")
    t0 = ev[0]["ts"]
    cut = t0 + O.TRAIN_DAYS * O.D
    train = [e for e in ev if e["ts"] < cut]
    test = [e for e in ev if e["ts"] >= cut]
    ids = [r["router"][2:].lower()
           for r in json.load(open(f"{HERE}/data/hl_ism_ethereum.json"))
           if r.get("ism") == ZERO]
    uni, usage = universe_and_weights(train, ids, test)
    coll = collateral_shares(uni)
    B = round(O.peak_daily(train)[0] * O.HEADROOM, -3)
    n_uni = len(uni)

    # the cold route's own position, which is what the manuscript describes
    cold_route = next(e["route"] for e in test if abs(e["usd"] - COLD_USD) < 1.0)
    cold_train = sum(e["usd"] for e in train if e["route"] == cold_route)
    print(f"B ${B:,.0f} | universe {n_uni} routes | cold route {cold_route[:10]}")
    print(f"  cold route train volume ${cold_train:,.0f} "
          f"({usage[cold_route]*100:.4f}% of train) "
          f"-> earned floor at alpha=0.8 is ${0.8*B*usage[cold_route]:,.0f}")
    print(f"  the transfer is ${COLD_USD:,.0f}; full surplus at alpha=0.8 is "
          f"${0.2*B:,.0f}\n")

    configs = [("earned", 0.0, usage)]
    for b in BETAS:
        configs.append((f"flat {b:g}", b,
                        mix(usage, {r: 1.0 / n_uni for r in uni}, b)))
    for b in BETAS:
        configs.append((f"collateral {b:g}", b, mix(usage, coll, b)))

    print(f"{'policy':>16}{'alpha':>7}{'cold settles':>14}{'defer %':>10}"
          f"{'P95 h':>8}{'undel 7d':>10}{'sybil x B':>11}")
    rows = []
    for name, beta, w in configs:
        kind = name.split()[0]
        for a in ALPHAS:
            out, pend, n = O.simulate_fixed(test, B, a, ids, w)
            # recover the still-pending events for the cold-route lookup
            settled = {id(e) for e, _ in out}
            pending_ev = [e for e in test if id(e) not in settled]
            wait = cold_wait_hours(out, pending_ev)
            nz = [d for _, d in out if d > 0]
            p95 = O.pct(nz, 0.95) / 3600 if nz else 0.0
            syb = sybil_reachable(kind, beta, a, B, n_uni,
                                  coll_share_fresh=0.0)
            rows.append({"policy": name, "kind": kind, "beta": beta, "alpha": a,
                         "cold_wait_h": wait, "cold_settles": wait is not None,
                         "deferral_rate": len(nz) / max(n, 1),
                         "p95_delay_h": p95, "undelivered_7d": pend,
                         "sybil_reachable_x_B": syb})
            ws = "no" if wait is None else f"{wait:.1f} h"
            print(f"{name:>16}{a:>7.1f}{ws:>14}{100*len(nz)/max(n,1):>10.2f}"
                  f"{p95:>8.1f}{pend:>10}{syb:>11.2f}")

    # Cost of minting a floor under each policy, and the drain-side price of
    # the alpha that actually settles the transfer.
    st = json.load(open(f"{HERE}/data/stock_multi.json"))["per_route"]["ethereum"]
    total_stock = sum(float(x) for x in st.values())
    sweep = json.load(open(f"{HERE}/data/alpha_sweep.json"))
    ai = {a: i for i, a in enumerate(sweep["alphas"])}
    clean = {a: sweep["grid"]["none/usage"][ai[a]] for a in ALPHAS}
    drain = {a: sweep["grid"]["drain/usage"][ai[a]] for a in ALPHAS}
    print("\nprice of the alpha that settles the transfer (usage floors):")
    for a in ALPHAS:
        print(f"  alpha={a:.1f}  clean {clean[a]:5.1f}%   sustained drain {drain[a]:5.1f}%")

    mint = {}
    for b in BETAS:
        f_flat = b * 0.8 * B / (n_uni + SYBILS)
        need_coll = f_flat / (b * 0.8 * B) * total_stock
        mint[b] = {"flat_floor_usd": f_flat, "collateral_to_match_usd": need_coll}
        print(f"  beta={b:g}: a flat minimum grants ${f_flat:,.0f} per fresh route "
              f"for free; matching it under the collateral policy costs "
              f"${need_coll:,.0f} of posted collateral")

    # What would a collateral-proportional floor actually have to be worth?
    need_share = COLD_USD / (0.8 * B)
    print(f"\nA floor large enough to carry the transfer on its own would need "
          f"{need_share*100:.0f}% of the reserved mass at alpha=0.8,")
    print(f"against the cold route's collateral share of {coll[cold_route]*100:.2f}%.")

    out = {"schema": 1, "B_usd": B, "universe_routes": n_uni,
           "cold_route": cold_route, "cold_usd": COLD_USD,
           "cold_train_usd": cold_train,
           "cold_train_share": usage[cold_route],
           "cold_collateral_share": coll[cold_route],
           "alpha_reserved_needed_share": need_share,
           "sybil_count": SYBILS, "gap_hours": GAP_H,
           "total_collateral_usd": total_stock,
           "clean_admitted_pct": clean, "drain_admitted_pct": drain,
           "mint_cost": mint, "rows": rows}
    json.dump(out, open(f"{HERE}/data/bootstrap_policy.json", "w"), indent=1)
    print("\nwrote data/bootstrap_policy.json")


if __name__ == "__main__":
    main()
