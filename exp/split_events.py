#!/usr/bin/env python3
"""Derive per-chain event files from the multi-chain collection.

sim.py and fig.py read data/events_<chain>.json, but flow_multi.py only writes
data/events_multi.json. Without this step the alpha sweep and Figure 4 cannot
be regenerated from a clean checkout -- the per-chain file has to already be
lying around. Run this immediately after flow_multi.py.
"""
import json, os, collections

HERE = os.path.dirname(os.path.abspath(__file__))
src = f"{HERE}/data/events_multi.json"
events = json.load(open(src))

by_chain = collections.defaultdict(list)
for e in events:
    c = e.get("chain")
    if not c:
        raise SystemExit(f"event without a chain field: {e}")
    by_chain[c].append(e)

for chain, evs in sorted(by_chain.items()):
    out = f"{HERE}/data/events_{chain}.json"
    json.dump(evs, open(out, "w"))
    print(f"  {chain:<10} {len(evs):>6} events -> data/events_{chain}.json")
print(f"total {len(events)} events across {len(by_chain)} chains")
