"""B sensitivity + adaptive adversaries.

Extends sim.py with:
  * a sweep of the budget B (does the alpha optimum survive tighter budgets?)
  * three adversary models beyond the naive per-window flood
  * a Sybil adversary that deploys K routes purely to claim floor allocations

The Sybil case is the decisive test of the floor mechanism: floors are the
isolation guarantee, so an adversary that can mint floors defeats it from
the inside.
"""
import json
import os
from collections import defaultdict

from sim import Bucket, load, weights, summarize, D, ADV, B_DEFAULT

HERE = os.path.dirname(os.path.abspath(__file__))
ZERO = "0x" + "0" * 40


def adversary_stream(kind, t0, tend, B, n_sybil=0):
    """Return [(ts, route, usd)] for the adversary."""
    out = []
    if kind == "none":
        return out
    names = [f"{ADV}{i}" for i in range(max(n_sybil, 1))]
    if kind == "flood":                      # naive: dump B at each window start
        t = t0
        while t <= tend:
            out.append({"ts": t, "route": names[0], "usd": B})
            t += D
    elif kind == "drain":                    # continuous: consume exactly the refill
        step = D / 24
        t = t0
        while t <= tend:
            out.append({"ts": t, "route": names[0], "usd": B / 24})
            t += step
    elif kind == "sybil":                    # K routes each flooding every window
        t = t0
        while t <= tend:
            for nm in names:
                out.append({"ts": t, "route": nm, "usd": B / len(names)})
            t += D
    return out


def run2(events, B, alpha, floor_policy, adversary="none", n_sybil=0,
         wake_usd=0.0, all_ids=()):
    t0 = events[0]["ts"]
    tend = events[-1]["ts"]
    adv = adversary_stream(adversary, t0, tend, B, n_sybil)
    stream = list(events) + adv
    if wake_usd:
        stream.append({"ts": (t0 + tend) / 2, "route": "__dormant__",
                       "usd": wake_usd})
    stream.sort(key=lambda e: e["ts"])

    adv_names = {e["route"] for e in adv}
    # A Sybil route is brand new: it has no usage history, so a usage-weighted
    # floor assigns it nothing.  An equal-share floor grants it one immediately.
    universe = set(all_ids) | {e["route"] for e in stream}
    w = weights(events, universe, floor_policy, len(universe))
    wsum = sum(w.values()) or 1.0
    floors = {r: alpha * B * w.get(r, 0.0) / wsum for r in universe}
    fb = {r: Bucket(floors.get(r, 0.0), t0) for r in universe}
    surplus = Bucket((1 - alpha) * B, t0)

    adm = defaultdict(float)
    for e in stream:
        r, need = e["route"], e["usd"]
        got = fb[r].take(e["ts"], need)
        if got < need:
            got += surplus.take(e["ts"], need - got)
        adm[r] += got
    return adm, adv_names


def main():
    events = load()
    demand = defaultdict(float)
    for e in events:
        demand[e["route"]] += e["usd"]
    all_ids = [r["router"][2:].lower()
               for r in json.load(open(f"{HERE}/data/hl_ism_ethereum.json"))
               if r.get("ism") == ZERO]

    def legit(adm, adv_names):
        ex = tuple(adv_names) + ("__dormant__",)
        return summarize(adm, demand, exclude=ex)[0]

    print("trace: 31d, 12 active routes, $429,528; measured peak day $87,878\n")

    # ---------------------------------------------------------------- B sweep
    print("=" * 78)
    print("A. 预算 B 敏感性（usage 加权保底，flood 敌手）")
    print("=" * 78)
    print(f"{'B/day':>10}{'clean':>10}{'a=0':>10}{'a=0.4':>10}{'a=0.6':>10}"
          f"{'a=0.8':>10}{'a=1.0':>10}   最优 a")
    for B in (50_000, 88_000, B_DEFAULT, 150_000, 250_000, 500_000, 1_000_000):
        adm, _ = run2(events, B, 0.8, "usage", "none", all_ids=all_ids)
        clean = legit(adm, set())
        row, best, bestv = [], None, -1
        for a in (0.0, 0.4, 0.6, 0.8, 1.0):
            adm, an = run2(events, B, a, "usage", "flood", all_ids=all_ids)
            v = legit(adm, an)
            row.append(v)
            if v > bestv:
                bestv, best = v, a
        print(f"{B:>10,}{100*clean:>9.1f}%" +
              "".join(f"{100*v:>9.1f}%" for v in row) + f"     {best}")

    # -------------------------------------------------------- adversary types
    print()
    print("=" * 78)
    print("B. 自适应敌手（B from sim3.json，usage 加权保底）")
    print("=" * 78)
    print(f"{'alpha':>7}{'flood':>12}{'drain(连续)':>16}{'sybil x10':>13}"
          f"{'sybil x50':>13}")
    for a in (0.0, 0.4, 0.6, 0.8, 1.0):
        cells = []
        for kind, k in (("flood", 0), ("drain", 0), ("sybil", 10), ("sybil", 50)):
            adm, an = run2(events, B_DEFAULT, a, "usage", kind, n_sybil=k,
                           all_ids=all_ids)
            cells.append(legit(adm, an))
        print(f"{a:>7.1f}" + "".join(f"{100*c:>12.1f}%" for c in cells))

    # ------------------------------------------- Sybil vs floor-policy choice
    print()
    print("=" * 78)
    print("C. Sybil 攻击 x50 对不同保底策略（B from sim3.json）")
    print("=" * 78)
    print(f"{'alpha':>7}{'usage':>12}{'equal':>12}{'usage+min':>13}")
    for a in (0.0, 0.4, 0.8, 1.0):
        cells = []
        for fp in ("usage", "equal", "usage+min"):
            adm, an = run2(events, B_DEFAULT, a, fp, "sybil", n_sybil=50,
                           all_ids=all_ids)
            cells.append(legit(adm, an))
        print(f"{a:>7.1f}" + "".join(f"{100*c:>11.1f}%" for c in cells))

    # ------------------------------------------------ adversary's own take
    print()
    print("=" * 78)
    print("D. 敌手实际抢到的预算份额（B from sim3.json，usage）")
    print("=" * 78)
    print(f"{'alpha':>7}{'flood':>12}{'drain':>12}{'sybil x50':>13}")
    for a in (0.0, 0.4, 0.8, 1.0):
        cells = []
        for kind, k in (("flood", 0), ("drain", 0), ("sybil", 50)):
            adm, an = run2(events, B_DEFAULT, a, "usage", kind, n_sybil=k,
                           all_ids=all_ids)
            took = sum(adm.get(n, 0.0) for n in an)
            days = (events[-1]["ts"] - events[0]["ts"]) / D
            cells.append(took / days / B_DEFAULT)
        print(f"{a:>7.1f}" + "".join(f"{100*c:>11.1f}%" for c in cells))


if __name__ == "__main__":
    main()
