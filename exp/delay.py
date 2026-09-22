"""Latency of deferral: how long is a message held before the budget admits it?

Rejection under this design is deferral -- the Mailbox rolls back its delivery
record when the ISM reverts, so a relayer can re-submit (proved on-chain by
test_RejectedMessageIsDeliverableAfterRefill). What matters for RQ5 is the
resulting delay distribution, and whether large transfers are systematically
starved by a stream of small ones under naive retry.
"""
import json
import os
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ZERO = "0x" + "0" * 40
D = 86400.0
CHAINS = ["ethereum", "base", "arbitrum", "bsc"]


def load():
    ev = json.load(open(f"{HERE}/data/events_multi.json"))
    for e in ev:
        e["ts"] = datetime.strptime(e["t"], "%Y-%m-%dT%H:%M:%S").timestamp()
    ev.sort(key=lambda e: e["ts"])
    by = defaultdict(list)
    for e in ev:
        by[e["chain"]].append(e)
    return by


def simulate(events, B, alpha, ids, retry_step=600.0, horizon=7 * D):
    """Replay with deferral. Returns per-message delay in seconds (None = never)."""
    used = defaultdict(float)
    for e in events:
        used[e["route"]] += e["usd"]
    tot = sum(used.values()) or 1.0
    universe = set(ids) | {e["route"] for e in events}
    floors = {r: alpha * B * used.get(r, 0.0) / tot for r in universe}

    lvl = {r: floors[r] for r in universe}
    last = {r: events[0]["ts"] for r in universe}
    s_cap = (1 - alpha) * B
    s_lvl, s_last = s_cap, events[0]["ts"]

    def refill_floor(r, t):
        cap = floors[r]
        if cap <= 0:
            return 0.0
        lvl[r] = min(cap, lvl[r] + (t - last[r]) * cap / D)
        last[r] = t
        return lvl[r]

    def refill_surplus(t):
        nonlocal s_lvl, s_last
        if s_cap <= 0:
            return 0.0
        s_lvl = min(s_cap, s_lvl + (t - s_last) * s_cap / D)
        s_last = t
        return s_lvl

    def try_admit(e, t):
        need, r = e["usd"], e["route"]
        f = refill_floor(r, t)
        take = min(f, need)
        rest = need - take
        if rest <= 0:
            lvl[r] = f - take
            return True
        s = refill_surplus(t)
        if s >= rest:
            lvl[r] = f - take
            nonlocal_set(s - rest)
            return True
        return False

    def nonlocal_set(v):
        nonlocal s_lvl
        s_lvl = v

    pending = []          # (arrival, event)
    out = []              # (event, delay_seconds)
    idx = 0
    end = events[-1]["ts"] + horizon

    # Walk the arrival times; between arrivals, retry whatever is pending on a
    # fixed tick. A message admitted on arrival has delay 0 -- otherwise the
    # retry granularity, not the budget, would dominate the distribution.
    t = events[0]["ts"]
    while idx < len(events) or pending:
        next_arrival = events[idx]["ts"] if idx < len(events) else float("inf")
        next_tick = t + retry_step if pending else float("inf")
        t = min(next_arrival, next_tick)
        if t > end:
            break
        if pending:
            still = []
            for arrival, e in pending:
                if try_admit(e, t):
                    out.append((e, t - arrival))
                else:
                    still.append((arrival, e))
            pending = still
        while idx < len(events) and events[idx]["ts"] <= t:
            e = events[idx]
            idx += 1
            if try_admit(e, t):
                out.append((e, 0.0))
            else:
                pending.append((e["ts"], e))
    return out, len(pending), len(events)


def pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def main():
    by = load()
    cfg = json.load(open(f"{HERE}/data/sim3.json"))
    Bc = cfg["Bc"]
    ids = {}
    for c in CHAINS:
        try:
            ids[c] = [r["router"][2:].lower()
                      for r in json.load(open(f"{HERE}/data/hl_ism_{c}.json"))
                      if r.get("ism") == ZERO]
        except FileNotFoundError:
            ids[c] = []

    print("延迟分布（deferral latency），retry 粒度 10 分钟，追加 7 天窗口\n")
    for alpha in (0.8, 1.0):
        print(f"=== α = {alpha} ===")
        print(f"{'chain':<10}{'msgs':>7}{'延迟>0':>9}{'P50':>10}{'P95':>10}"
              f"{'max':>11}{'始终未过':>10}")
        allo = []
        for c in CHAINS:
            ev = by.get(c) or []
            if not ev:
                continue
            res, blocked, n = simulate(ev, Bc[c], alpha, ids[c])
            d = [x for _, x in res]
            allo += d
            nz = [x for x in d if x > 0]
            print(f"{c:<10}{n:>7}{len(nz):>9}"
                  f"{pct(nz,0.5)/60:>9.1f}m{pct(nz,0.95)/3600:>9.1f}h"
                  f"{(max(nz)/3600 if nz else 0):>10.1f}h{blocked:>10}")
        nz = [x for x in allo if x > 0]
        print(f"{'ALL':<10}{len(allo):>7}{len(nz):>9}"
              f"{pct(nz,0.5)/60:>9.1f}m{pct(nz,0.95)/3600:>9.1f}h"
              f"{(max(nz)/3600 if nz else 0):>10.1f}h")
        print(f"           被延迟的消息占比: {100*len(nz)/max(len(allo),1):.1f}%\n")

    # starvation: are large transfers held longer?
    print("=== 大额是否被系统性饿死？（α=0.8，全部链合并）===")
    rows = []
    for c in CHAINS:
        ev = by.get(c) or []
        if not ev:
            continue
        res, _, _ = simulate(ev, Bc[c], 0.8, ids[c])
        for e, dl in res:
            rows.append((e["usd"], dl))
    rows.sort(key=lambda x: x[0])
    q = len(rows) // 4
    for label, seg in (("最小 25%", rows[:q]), ("中间 50%", rows[q:3*q]),
                       ("最大 25%", rows[3*q:])):
        ds = [d for _, d in seg]
        nz = [x for x in ds if x > 0]
        print(f"  {label:<10} n={len(seg):>5}  被延迟 {100*len(nz)/max(len(ds),1):>5.1f}%  "
              f"P95={pct(nz,0.95)/3600:>6.2f}h  均值金额=${sum(u for u,_ in seg)/max(len(seg),1):>10,.0f}")


if __name__ == "__main__":
    main()
