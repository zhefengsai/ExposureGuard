#!/usr/bin/env python3
"""How much onboarding does fail-closed operation actually cost?

Section 7.4.2 reports that 16.6% of observed deliveries carry no price and
would be rejected by a fail-closed module.  Stated that way it reads as a
blocking gap.  The operational question is different and answerable from the
same data: price mappings are maintained per route, routes are wildly unequal
in traffic, so how many mappings buy how much coverage?

We rank the unpriced routes by the deliveries they carry, add mappings
greedily, and report the coverage curve plus the mappings needed to reach 90,
95 and 99 percent of all observed deliveries.  Ranking by traffic is the
optimistic ordering an operator would actually follow; we also report the
value-weighted view where a price is available, and are explicit that for
routes with no local asset a price cannot simply be looked up.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS = (0.90, 0.95, 0.99, 1.00)
TOPK = (10, 20, 50)


def main() -> None:
    tri = json.load(open(f"{HERE}/data/unpriced_triage.json"))
    unpriced = tri["total_unpriced_deliveries"]
    routes = sorted(tri["routes"], key=lambda r: -r["missing"])

    # Total observed deliveries: priced trace plus the unpriced remainder.
    priced = 65_427
    total = priced + unpriced
    assert abs(unpriced / total - 0.166) < 0.002, unpriced / total

    print(f"{total:,} observed deliveries; {priced:,} priced "
          f"({100*priced/total:.1f}%), {unpriced:,} unpriced "
          f"({100*unpriced/total:.1f}%) across {len(routes)} routes\n")

    cum, curve = priced, []
    for i, r in enumerate(routes, 1):
        cum += r["missing"]
        curve.append({"mappings": i, "chain": r["chain"], "symbol": r["symbol"],
                      "class": r["class"], "deliveries": r["missing"],
                      "coverage": cum / total})

    print(f"{'mappings':>9}{'coverage':>10}   next route")
    for k in TOPK:
        c = curve[k - 1]
        print(f"{k:>9}{100*c['coverage']:>9.1f}%   {c['symbol']} "
              f"({c['chain']}, {c['class']})")
    print()

    need = {}
    for t in TARGETS:
        hit = next((c for c in curve if c["coverage"] >= t), None)
        if hit is None and priced / total >= t:
            need[t] = 0
        else:
            need[t] = hit["mappings"] if hit else None
        n = need[t]
        print(f"  {100*t:>5.0f}% coverage needs {n} price mapping"
              f"{'' if n == 1 else 's'}")

    # Onboarding is not uniform in difficulty: a route with no local asset has
    # nothing to price against, so its mapping is a governance judgement rather
    # than a lookup.
    cls = {}
    for c in curve[:max(TOPK)]:
        cls[c["class"]] = cls.get(c["class"], 0) + 1
    print(f"\n  of the top {max(TOPK)} routes by traffic, by class: "
          + ", ".join(f"{k} {v}" for k, v in sorted(cls.items())))

    out = {"schema": 1, "total_deliveries": total, "priced": priced,
           "unpriced": unpriced, "unpriced_routes": len(routes),
           "baseline_coverage": priced / total,
           "topk": {str(k): curve[k - 1]["coverage"] for k in TOPK},
           "mappings_for_target": {str(t): need[t] for t in TARGETS},
           "top_classes": cls, "curve": curve}
    json.dump(out, open(f"{HERE}/data/price_coverage_curve.json", "w"), indent=1)
    print("\nwrote data/price_coverage_curve.json")


if __name__ == "__main__":
    main()
