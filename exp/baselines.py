#!/usr/bin/env python3
"""RQ2 baseline comparison: what each deployed control admits during the gap.

No new measurement. Every input is already in the artifact:
  stock            data/stock_multi.json (Ethereum, audited collateral)
  per-root budget  data/sim3.json  (derived from measured peak)
  per-route caps   sim.py's per-route policy (each route at 2x its own peak day)

The point of the comparison is that per-route caps are sized by independent
operators against their own traffic and never defer a legitimate message, yet
their SUM -- which nobody chooses -- is what an adversary holding the root gets.
"""
import json, os
from sim import load, run, B_DEFAULT, D

HERE = os.path.dirname(os.path.abspath(__file__))
STOCK = 9_857_090.0

ev = load()
routes = sorted({e["route"] for e in ev})
ids = sorted({e["route"] for e in ev})
_, per_route_sum = run(ev, routes, "per-route", all_ids=ids)
B = B_DEFAULT

GAPS = [("30 min", .5/24), ("1 h", 1/24), ("6 h", .25), ("1 day", 1.), ("7 days", 7.)]

def envelope(cap, d):     # Eq. 1, capped by what is there to take
    return min(STOCK, cap * (1 + d))

print(f"stock ${STOCK:,.0f} | per-root B ${B:,.0f} | per-route sum ${per_route_sum:,.0f} "
      f"({per_route_sum/B:.1f}x B)\n")
print(f"{'gap':>8}{'no control':>13}{'pause':>13}{'per-route':>14}{'ExposureGuard':>16}"
      f"{'EG vs per-route':>17}")
rows = []
for lbl, d in GAPS:
    pr, eg = envelope(per_route_sum, d), envelope(B, d)
    rows.append({"gap": lbl, "delta_days": d, "none": STOCK, "pause": STOCK,
                 "per_route": pr, "exposureguard": eg,
                 "reduction_vs_pause": STOCK/eg, "reduction_vs_per_route": pr/eg})
    print(f"{lbl:>8}{STOCK/1e6:>12,.2f}M{STOCK/1e6:>12,.2f}M"
          f"{pr/1e6:>13,.2f}M{eg/1e6:>15,.3f}M{pr/eg:>16.1f}x")

json.dump({"stock": STOCK, "B_per_root": B, "per_route_cap_sum": per_route_sum,
           "per_route_over_B": per_route_sum/B, "rows": rows},
          open(f"{HERE}/data/baselines.json", "w"), indent=1)
print(f"\nper-route 从不延迟合法流量（放行 100%），但其上界是 ExposureGuard 的 "
      f"{per_route_sum/B:.1f} 倍——且该总额随新 route 加入线性增长，无人设定。")
print("wrote data/baselines.json")
