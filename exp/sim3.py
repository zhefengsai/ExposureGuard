"""Per-chain evaluation on the four-chain trace, with an explicit baseline.

Two things the single-chain simulator could not do:

  BASELINE. The obvious alternative to ExposureGuard is for every route owner to
  deploy the existing RateLimitedIsm with caps summing to B. That is exactly
  alpha = 1.0 -- a static per-route partition of a global budget -- so it is
  reported as a named baseline rather than a point on a sweep.

  PARTITION WASTE. SCHEME-CHECK V3 shows the budget must be split per chain
  ex ante, because no cross-chain aggregation survives root compromise. The
  cost is headroom stranded on a quiet chain while a busy one is constrained.
  Quantified here against a (physically unrealisable) global-budget oracle.
"""
import json
import os
from collections import defaultdict
from datetime import datetime

from sim import Bucket, summarize, D
from sim2 import run2

HERE = os.path.dirname(os.path.abspath(__file__))
ZERO = "0x" + "0" * 40
CHAINS = ["ethereum", "base", "arbitrum", "bsc"]


def load_multi():
    ev = json.load(open(f"{HERE}/data/events_multi.json"))
    for e in ev:
        e["ts"] = datetime.strptime(e["t"], "%Y-%m-%dT%H:%M:%S").timestamp()
    ev.sort(key=lambda e: e["ts"])
    by_chain = defaultdict(list)
    for e in ev:
        by_chain[e["chain"]].append(e)
    ids = {}
    for c in CHAINS:
        try:
            ids[c] = [r["router"][2:].lower()
                      for r in json.load(open(f"{HERE}/data/hl_ism_{c}.json"))
                      if r.get("ism") == ZERO]
        except FileNotFoundError:
            ids[c] = []
    return by_chain, ids


def peak_daily(events):
    d = defaultdict(float)
    for e in events:
        d[e["t"][:10]] += e["usd"]
    return max(d.values()) if d else 0.0


def chainlog_eth_peak():
    """Prefer on-chain Stage-2 peak when TASK-CHAINLOG-XVAL has completed."""
    path = f"{HERE}/data/chainlog_xval.json"
    if not os.path.exists(path):
        return None
    s2 = json.load(open(path)).get("stage2") or {}
    if s2.get("ok") and s2.get("peak_daily"):
        return float(s2["peak_daily"])
    return None


def main():
    by_chain, ids = load_multi()
    peaks = {c: peak_daily(by_chain[c]) for c in CHAINS}
    eth_chain = chainlog_eth_peak()
    if eth_chain is not None:
        print(f"ethereum peak: indexer ${peaks['ethereum']:,.0f} → "
              f"chainlog stage2 ${eth_chain:,.0f}")
        peaks["ethereum"] = eth_chain
    total_peak = sum(peaks.values())
    # global budget sized at 1.07x the summed peak (ETH headroom above measured peak)
    B_TOTAL = round(total_peak * 1.07, -3)
    print(f"four-chain trace: {sum(len(v) for v in by_chain.values())} events, "
          f"{len({e['route'] for v in by_chain.values() for e in v})} active routes")
    print(f"summed peak-day demand: ${total_peak:,.0f}   global budget B=${B_TOTAL:,.0f}\n")

    # budget split proportional to each chain's peak demand
    Bc = {c: B_TOTAL * peaks[c] / total_peak if total_peak else 0 for c in CHAINS}

    print("=" * 86)
    print("A. 每链结果：ExposureGuard(α=0.8) vs baseline(per-route caps = α=1.0)")
    print("=" * 86)
    print(f"{'chain':<10}{'B_c':>12}{'peak':>12}{'events':>8}"
          f"{'clean α.8':>11}{'drain α.8':>11}{'clean α1':>10}{'drain α1':>10}")
    agg = defaultdict(float)
    for c in CHAINS:
        ev = by_chain[c]
        if not ev:
            print(f"{c:<10}{Bc[c]:>12,.0f}{peaks[c]:>12,.0f}{0:>8}")
            continue
        dem = defaultdict(float)
        for e in ev:
            dem[e["route"]] += e["usd"]
        cells = []
        for a in (0.8, 1.0):
            for adv in ("none", "drain"):
                adm, an = run2(ev, Bc[c], a, "usage", adv, all_ids=ids[c])
                f = summarize(adm, dem, exclude=tuple(an) + ("__dormant__",))[0]
                cells.append(100 * f)
                agg[f"{a}-{adv}"] += sum(adm.get(r, 0.0) for r in dem)
                agg[f"want"] += 0
        print(f"{c:<10}{Bc[c]:>12,.0f}{peaks[c]:>12,.0f}{len(ev):>8}"
              f"{cells[0]:>10.1f}%{cells[1]:>10.1f}%{cells[2]:>9.1f}%{cells[3]:>9.1f}%")

    # -------------------------------------------------- partition waste
    print()
    print("=" * 86)
    print("B. 跨链静态切分的代价（对照一个物理上无法实现的全局预算 oracle）")
    print("=" * 86)
    all_ev = sorted((e for v in by_chain.values() for e in v), key=lambda e: e["ts"])
    all_ids = [i for c in CHAINS for i in ids[c]]
    dem_all = defaultdict(float)
    for e in all_ev:
        dem_all[e["route"]] += e["usd"]

    for a in (0.8, 1.0):
        for adv in ("none", "drain"):
            # partitioned: each chain independently limited to B_c
            got_part = 0.0
            for c in CHAINS:
                if not by_chain[c]:
                    continue
                adm, an = run2(by_chain[c], Bc[c], a, "usage", adv, all_ids=ids[c])
                got_part += sum(adm.get(r, 0.0)
                                for r in {e["route"] for e in by_chain[c]})
            # oracle: one global bucket of the same total size
            adm, an = run2(all_ev, B_TOTAL, a, "usage", adv, all_ids=all_ids)
            got_glob = sum(adm.get(r, 0.0) for r in dem_all)
            waste = 100 * (got_glob - got_part) / got_glob if got_glob else 0
            print(f"  α={a}  敌手={adv:<6}  分链放行 ${got_part:>12,.0f}   "
                  f"全局 oracle ${got_glob:>12,.0f}   切分损失 {waste:>5.1f}%")

    json.dump({"B_total": B_TOTAL, "Bc": Bc, "peaks": peaks},
              open(f"{HERE}/data/sim3.json", "w"), indent=1)


if __name__ == "__main__":
    main()
