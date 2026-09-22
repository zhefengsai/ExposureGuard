#!/usr/bin/env python3
"""Freeze the reservation sweep so paper numbers have a source to check against.

The alpha numbers in RQ3/RQ4 previously had no frozen artifact, which is how a
wrong equal-share value survived a read-through.  Regenerate with:
    python3 alpha_sweep.py
"""
import json, os
from collections import defaultdict
import sim, sim2

HERE = os.path.dirname(os.path.abspath(__file__))
ZERO = "0x" + "0" * 40
ALPHAS = (0.0, 0.4, 0.6, 0.8, 1.0)

ev = sim.load()
demand = defaultdict(float)
for e in ev:
    demand[e["route"]] += e["usd"]
all_ids = [r["router"][2:].lower()
           for r in json.load(open(f"{HERE}/data/hl_ism_ethereum.json"))
           if r.get("ism") == ZERO]
B = sim.B_DEFAULT


def legit(adm, adv):
    return 100 * sim.summarize(adm, demand,
                               exclude=tuple(adv) + ("__dormant__",))[0]


out = {"B": B, "D_seconds": sim.D, "alphas": list(ALPHAS), "grid": {}}
for adversary, n_sybil in (("none", 0), ("flood", 0), ("drain", 0), ("sybil", 50)):
    for policy in ("usage", "equal", "usage+min"):
        row = []
        for a in ALPHAS:
            adm, adv = sim2.run2(ev, B, a, policy, adversary,
                                 n_sybil=n_sybil, all_ids=all_ids)
            row.append(round(legit(adm, adv), 1))
        out["grid"][f"{adversary}/{policy}"] = row

json.dump(out, open(f"{HERE}/data/alpha_sweep.json", "w"), indent=2)
print(f"B = ${B:,.0f}, D = {sim.D/3600:.0f}h")
print(f"{'scenario':22}" + "".join(f"a={a:<7}" for a in ALPHAS))
for k, v in out["grid"].items():
    print(f"{k:22}" + "".join(f"{x:<9.1f}" for x in v))
print("\nwrote data/alpha_sweep.json")
