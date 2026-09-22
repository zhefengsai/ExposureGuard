"""Budget-allocation simulator: does the floor/surplus split actually buy anything?

Replays the measured 31-day inbound message trace against four policies and
sweeps the reservation fraction alpha.

Policies
  none          admit everything (status quo: 0/89 ISMs deploy any limit)
  per-route     each route gets its own bucket; NO global bound (what the
                deployed primitive can express).  Reports sum of caps.
  shared(a=0)   single shared bucket, FCFS (the naive per-root budget)
  guard(a)      hard-reserved floors f_i = a*B*w_i  +  work-conserving
                surplus (1-a)*B

Scenarios
  clean         measured trace only
  grief         adversary controlling one route floods the bucket each window
  wake          a dormant route suddenly needs a large transfer, under grief
"""
import json
import os
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(__file__)
D = 86400.0                       # window: 24h
import json as _json, os as _os
def _eth_budget():
    """预算从 data/sim3.json 读取，不再硬编码——旧值 100_000 曾静默过期。"""
    _p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data/sim3.json")
    return float(_json.load(open(_p))["Bc"]["ethereum"])
B_DEFAULT = _eth_budget()        # 由 sim3.py 从实测峰值导出


def load():
    ev = json.load(open(f"{HERE}/data/events_ethereum.json"))
    for e in ev:
        e["ts"] = datetime.strptime(e["t"], "%Y-%m-%dT%H:%M:%S").timestamp()
    ev.sort(key=lambda e: e["ts"])
    return ev


class Bucket:
    """Token bucket that refills linearly to `cap` over D seconds."""

    def __init__(self, cap, t0):
        self.cap = cap
        self.level = cap
        self.t = t0

    def _refill(self, t):
        if self.cap <= 0:
            self.level = 0.0
        else:
            self.level = min(self.cap, self.level + (t - self.t) * self.cap / D)
        self.t = t

    def take(self, t, v):
        self._refill(t)
        if v <= self.level:
            self.level -= v
            return v
        got = self.level
        self.level = 0.0
        return got


def weights(events, routes, policy, n_routes):
    """Floor weights w_i, summing to 1."""
    used = defaultdict(float)
    for e in events:
        used[e["route"]] += e["usd"]
    if policy == "equal":
        return {r: 1.0 / n_routes for r in routes}
    if policy == "usage":
        tot = sum(used.values()) or 1.0
        return {r: used.get(r, 0.0) / tot for r in routes}
    if policy == "usage+min":
        # half the reservation split equally, half by usage
        tot = sum(used.values()) or 1.0
        return {r: 0.5 / n_routes + 0.5 * used.get(r, 0.0) / tot for r in routes}
    raise ValueError(policy)


ADV = "__adversary__"


def run(events, routes, policy, alpha=0.0, B=B_DEFAULT, floor_policy="usage",
        grief=False, wake_usd=0.0, n_routes=None, all_ids=()):
    """Return per-route admitted value and totals."""
    n_routes = n_routes or len(routes)
    t0 = events[0]["ts"]
    stream = list(events)

    # the adversary owns one route and floods at the start of every window
    if grief:
        t = t0
        end = events[-1]["ts"]
        while t <= end:
            stream.append({"ts": t, "route": ADV, "usd": B})
            t += D
    # a dormant route wakes up mid-trace and needs a large transfer
    if wake_usd:
        mid = (t0 + events[-1]["ts"]) / 2
        stream.append({"ts": mid, "route": "__dormant__", "usd": wake_usd})
    stream.sort(key=lambda e: e["ts"])

    all_routes = set(routes) | ({ADV} if grief else set()) | \
                 ({"__dormant__"} if wake_usd else set())

    if policy == "none":
        adm = defaultdict(float)
        for e in stream:
            adm[e["route"]] += e["usd"]
        return adm, float("inf")

    if policy == "per-route":
        # each route capped at its own historical peak-day demand x2, no global bound
        peak = defaultdict(float)
        day = defaultdict(lambda: defaultdict(float))
        for e in events:
            day[e["route"]][int(e["ts"] // D)] += e["usd"]
        for r, dd in day.items():
            peak[r] = max(dd.values())
        caps = {r: max(peak.get(r, 0.0) * 2, B * 0.01) for r in all_routes}
        buckets = {r: Bucket(caps[r], t0) for r in all_routes}
        adm = defaultdict(float)
        for e in stream:
            adm[e["route"]] += buckets[e["route"]].take(e["ts"], e["usd"])
        return adm, sum(caps.values())

    # shared / guard.  Floors are provisioned over EVERY route sharing the root
    # (all_ids), so capacity reserved for dormant routes sits idle -- that is the
    # real cost of reservation and must not be normalised away.
    universe = set(all_ids) | all_routes
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
    return adm, B


def summarize(adm, demand, exclude=(ADV, "__dormant__")):
    legit = {r: v for r, v in demand.items() if r not in exclude}
    got = sum(adm.get(r, 0.0) for r in legit)
    want = sum(legit.values()) or 1.0
    ratios = [adm.get(r, 0.0) / v for r, v in legit.items() if v > 0]
    n = len(ratios) or 1
    jain = (sum(ratios) ** 2) / (n * sum(x * x for x in ratios)) if any(ratios) else 0.0
    starved = sum(1 for x in ratios if x < 0.99)
    return got / want, jain, starved, len(ratios)


def main():
    events = load()
    routes = sorted({e["route"] for e in events})
    demand = defaultdict(float)
    for e in events:
        demand[e["route"]] += e["usd"]
    ZERO = "0x" + "0" * 40
    all_ids = [r["router"][2:].lower()
               for r in json.load(open(f"{HERE}/data/hl_ism_ethereum.json"))
               if r.get("ism") == ZERO]
    N_ALL = len(all_ids)

    span = (events[-1]["ts"] - events[0]["ts"]) / D
    print(f"trace: {len(events)} messages, {len(routes)} active routes, "
          f"{span:.1f} days, total ${sum(demand.values()):,.0f}")
    peak_note = 262_203.0
    try:
        peak_note = float(__import__("json").load(open(f"{HERE}/data/sim3.json"))["peaks"]["ethereum"])
    except Exception:
        pass
    print(f"budget B = ${B_DEFAULT:,.0f}/day (measured peak day = ${peak_note:,.0f})\n")

    print("=" * 74)
    print("A. 无攻击下各策略的合法放行率")
    print("=" * 74)
    for pol, kw in (("none", {}), ("per-route", {}),
                    ("shared naive (a=0)", {"policy": "shared", "alpha": 0.0}),
                    ("guard a=0.8 usage", {"policy": "shared", "alpha": 0.8,
                                           "floor_policy": "usage"}),
                    ("guard a=0.8 equal", {"policy": "shared", "alpha": 0.8,
                                           "floor_policy": "equal"})):
        p = kw.pop("policy", pol)
        adm, cap = run(events, routes, p, n_routes=N_ALL, all_ids=all_ids, **kw)
        frac, jain, starved, n = summarize(adm, demand)
        capstr = "unbounded" if cap == float("inf") else f"${cap:,.0f}"
        print(f"  {pol:<22} 放行 {100*frac:6.2f}%  Jain {jain:.3f}  "
              f"饥饿 {starved}/{n}  聚合上界 {capstr}")

    print()
    print("=" * 74)
    print("B. Grief 攻击下：敌手每窗口注入 B，扫描 alpha 与保底策略")
    print("=" * 74)
    base = {}
    for fp in ("usage", "equal", "usage+min"):
        adm, _ = run(events, routes, "shared", alpha=0.0, floor_policy=fp,
                     n_routes=N_ALL, all_ids=all_ids)
        base[fp] = summarize(adm, demand)[0]
    print(f"{'alpha':>6}", end="")
    for fp in ("usage", "equal", "usage+min"):
        print(f"{fp:>16}", end="")
    print("      <- 攻击下合法放行率")
    rows = []
    for a in (0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 1.0):
        line = {}
        print(f"{a:>6.1f}", end="")
        for fp in ("usage", "equal", "usage+min"):
            adm, _ = run(events, routes, "shared", alpha=a, floor_policy=fp,
                         grief=True, n_routes=N_ALL, all_ids=all_ids)
            frac = summarize(adm, demand)[0]
            line[fp] = frac
            print(f"{100*frac:>15.2f}%", end="")
        print()
        rows.append((a, line))

    print()
    print("=" * 74)
    print("C. 休眠 route 苏醒：攻击进行中，一条零历史的 route 需要 $50,000")
    print("=" * 74)
    print(f"{'alpha':>6}{'usage':>14}{'equal':>14}{'usage+min':>14}")
    for a in (0.0, 0.4, 0.8, 1.0):
        print(f"{a:>6.1f}", end="")
        for fp in ("usage", "equal", "usage+min"):
            adm, _ = run(events, routes, "shared", alpha=a, floor_policy=fp,
                         grief=True, wake_usd=50_000.0, n_routes=N_ALL,
                         all_ids=all_ids)
            got = adm.get("__dormant__", 0.0)
            print(f"{100*got/50_000:>13.1f}%", end="")
        print()

    json.dump({"grief_sweep": rows, "base": base},
              open(f"{HERE}/data/sim_results.json", "w"), indent=1)


if __name__ == "__main__":
    main()
