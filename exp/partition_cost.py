#!/usr/bin/env python3
"""Capital cost of partitioning the budget across chains in advance.

Section IV argues the split is forced: no aggregation channel survives F_S.
This measures what it costs. Partitioned = each chain enforces its own B_c on
its own traffic. Oracle = one global bucket of B_total over the merged trace,
which no deployable system can realise under F_S but which bounds the loss.

Reads data/events_multi.json and data/sim3.json; writes data/partition_cost.json.
"""
import json, os, collections
from datetime import datetime
from sim2 import run2

HERE = os.path.dirname(os.path.abspath(__file__))
ev = json.load(open(f"{HERE}/data/events_multi.json"))
for _e in ev:                      # run2 wants epoch seconds, the file has ISO
    _e["ts"] = datetime.strptime(_e["t"], "%Y-%m-%dT%H:%M:%S").timestamp()
ev.sort(key=lambda e: e["ts"])
cfg = json.load(open(f"{HERE}/data/sim3.json"))

by_chain = collections.defaultdict(list)
for e in ev:
    by_chain[e["chain"]].append(e)

def admitted(events, B, alpha):
    ids = sorted({e["route"] for e in events})
    adm, adv = run2(events, B, alpha, "usage", "none", n_sybil=0, all_ids=ids)
    return sum(v for r, v in adm.items() if r not in adv)

out = {"B_total": cfg["B_total"], "Bc": cfg["Bc"], "alpha": {}}
for alpha in (0.8, 1.0):
    part = sum(admitted(sorted(evs, key=lambda e: e["ts"]), cfg["Bc"][c], alpha)
               for c, evs in by_chain.items())
    glob = admitted(ev, cfg["B_total"], alpha)
    loss = 0.0 if glob <= 0 else 100 * (1 - part / glob)
    out["alpha"][str(alpha)] = {"partitioned_usd": part, "oracle_usd": glob,
                                "cost_pct": loss}
    print(f"  alpha={alpha}: partitioned ${part:,.0f}  oracle ${glob:,.0f}  "
          f"-> {loss:.1f}% less admitted")
json.dump(out, open(f"{HERE}/data/partition_cost.json", "w"), indent=1)
print("wrote data/partition_cost.json")
