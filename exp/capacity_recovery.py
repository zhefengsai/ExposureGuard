#!/usr/bin/env python3
"""Throughput, goodput and recovery: the network-facing cost of the module.

Three questions a networking reviewer asks that gas-per-call does not answer.

  1. Capacity.  How many deliveries fit in a block with and without the module,
     and what is the resulting loss in messages per second?
  2. Goodput under attack.  While an adversary drains the shared budget, how
     much legitimate value still gets through, and how deep does the retry
     queue get?
  3. Recovery.  Once the attack stops, how long until the queue clears?

Wire overhead is reported separately and is not simulated: the module reads no
metadata (`verify(bytes calldata, bytes calldata message)` ignores its first
argument), and the message itself is untouched.  The only added bytes are the
aggregation metadata table that the deployed factory format requires.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

import oos_calibrate as O
from sim import Bucket

HERE = os.path.dirname(os.path.abspath(__file__))
ZERO = "0x" + "0" * 40

BLOCK_GAS = 36_000_000
BLOCK_S = 12.0
GAS_BASE = 156_641                 # deployed default ISM      (gas_deployed.json)
GAS_GUARD = 194_640                # + ExposureGuard in aggregation
AGG_METADATA_BYTES = 16            # 4B table + 3 x 4B offsets (GasDeployed.t.sol)

ALPHA = 0.8
ATTACK_H = 6.0
ADV = "__adversary__"
TICK = 600.0                       # retry cadence, as oos_calibrate
BIN_S = 1800.0                     # reporting resolution


def capacity():
    per_block_base = BLOCK_GAS // GAS_BASE
    per_block_guard = BLOCK_GAS // GAS_GUARD
    return {
        "block_gas": BLOCK_GAS, "block_seconds": BLOCK_S,
        "gas_base": GAS_BASE, "gas_guard": GAS_GUARD,
        "deliveries_per_block_base": per_block_base,
        "deliveries_per_block_guard": per_block_guard,
        "msgs_per_s_base": per_block_base / BLOCK_S,
        "msgs_per_s_guard": per_block_guard / BLOCK_S,
        "capacity_loss": 1.0 - per_block_guard / per_block_base,
        "gas_ratio_loss": 1.0 - GAS_BASE / GAS_GUARD,
        "added_calldata_bytes": AGG_METADATA_BYTES,
        "added_message_bytes": 0,
    }


def replay(events, B, alpha, floors, attack_usd_per_block=0.0,
           attack_window=None, adv_floor=0.0):
    """Event-driven replay with a retry queue, returning a time series.

    Legitimate messages that cannot be served are queued and retried every
    TICK; the adversary does not queue, it simply takes what it can each block.
    """
    t0 = events[0]["ts"]
    fb = {r: Bucket(floors.get(r, 0.0), t0) for r in floors}
    fb[ADV] = Bucket(adv_floor, t0)
    surplus = Bucket((1 - alpha) * B, t0)

    def admit(r, need, t, partial=False):
        """All-or-nothing for legitimate traffic: a failed attempt must not
        burn capacity, or every retry would drain the bucket it is waiting on.
        The adversary takes whatever is available, which is the point."""
        if r not in fb:
            fb[r] = Bucket(0.0, t)
        if partial:
            got = fb[r].take(t, need)
            if got < need:
                got += surplus.take(t, need - got)
            return got
        fb[r]._refill(t)
        surplus._refill(t)
        if fb[r].level + surplus.level < need - 1e-9:
            return 0.0
        from_floor = min(fb[r].level, need)
        fb[r].level -= from_floor
        surplus.level -= (need - from_floor)
        return need

    ticks = []
    if attack_window:
        a0, a1 = attack_window
        t = a0
        while t < a1:
            ticks.append(t)
            t += BLOCK_S

    stream = sorted(
        [(e["ts"], "legit", e) for e in events]
        + [(t, "adv", None) for t in ticks],
        key=lambda x: x[0])

    pending = []
    series = defaultdict(lambda: {"admitted": 0.0, "demand": 0.0,
                                  "adv_admitted": 0.0, "queue": 0})
    idx, t = 0, stream[0][0]
    end = stream[-1][0] + 7 * 86400
    drained_at = None
    attack_end = attack_window[1] if attack_window else None

    while idx < len(stream) or pending:
        nxt = stream[idx][0] if idx < len(stream) else float("inf")
        tick = t + TICK if pending else float("inf")
        t = min(nxt, tick)
        if t > end:
            break
        if pending:
            still = []
            for arrival, e in pending:
                if admit(e["route"], e["usd"], t) >= e["usd"] - 1e-9:
                    series[int(t // BIN_S)]["admitted"] += e["usd"]
                else:
                    still.append((arrival, e))
            pending = still
            if not pending and attack_end and t > attack_end and drained_at is None:
                drained_at = t
        while idx < len(stream) and stream[idx][0] <= t:
            ts, kind, e = stream[idx]
            idx += 1
            b = int(ts // BIN_S)
            if kind == "adv":
                series[b]["adv_admitted"] += admit(ADV, attack_usd_per_block, ts, partial=True)
            else:
                series[b]["demand"] += e["usd"]
                if admit(e["route"], e["usd"], ts) >= e["usd"] - 1e-9:
                    series[b]["admitted"] += e["usd"]
                else:
                    pending.append((ts, e))
        if pending:
            series[int(t // BIN_S)]["queue"] = max(
                series[int(t // BIN_S)]["queue"], len(pending))
    return series, drained_at, len(pending)


def window_stats(series, lo, hi):
    adm = sum(v["admitted"] for k, v in series.items() if lo <= k * BIN_S < hi)
    dem = sum(v["demand"] for k, v in series.items() if lo <= k * BIN_S < hi)
    q = max((v["queue"] for k, v in series.items() if lo <= k * BIN_S < hi),
            default=0)
    return adm, dem, q


def main() -> None:
    cap = capacity()
    print("1. per-block capacity")
    print(f"   baseline {cap['deliveries_per_block_base']} deliveries/block "
          f"({cap['msgs_per_s_base']:.1f} msg/s)")
    print(f"   guarded  {cap['deliveries_per_block_guard']} deliveries/block "
          f"({cap['msgs_per_s_guard']:.1f} msg/s)")
    print(f"   capacity loss {100*cap['capacity_loss']:.1f}% "
          f"(gas ratio implies {100*cap['gas_ratio_loss']:.1f}%)")
    print(f"   added calldata {cap['added_calldata_bytes']} B/delivery, "
          f"message unchanged\n")

    ev = O.load_chain("ethereum")
    t0 = ev[0]["ts"]
    cut = t0 + O.TRAIN_DAYS * O.D
    train = [e for e in ev if e["ts"] < cut]
    test = [e for e in ev if e["ts"] >= cut]
    ids = [r["router"][2:].lower()
           for r in json.load(open(f"{HERE}/data/hl_ism_ethereum.json"))
           if r.get("ism") == ZERO]
    B = round(O.peak_daily(train)[0] * O.HEADROOM, -3)
    uni = set(ids) | {e["route"] for e in train} | {e["route"] for e in test}
    used = defaultdict(float)
    for e in train:
        used[e["route"]] += e["usd"]
    tot = sum(used.values()) or 1.0
    floors = {r: ALPHA * B * used.get(r, 0.0) / tot for r in uni}

    # The attack is placed on the busiest held-out day. Goodput is reported
    # over the whole held-out stream rather than the attack window alone: the
    # window contains the $615k transfer of Section 7.4, which no policy
    # setting admits, and a window metric would report that single outlier
    # rather than the effect under test.
    daily = defaultdict(float)
    for e in test:
        daily[int(e["ts"] // 86400)] += e["usd"]
    busy = max(daily, key=daily.get)
    a0 = busy * 86400 + 6 * 3600
    a1 = a0 + ATTACK_H * 3600
    per_block = (1 - ALPHA) * B / 10.0          # drains surplus in ten blocks

    print(f"2. goodput and queueing under a {ATTACK_H:.0f} h drain\n"
          f"   x is the adversary's share of training volume; x=0 is a fresh "
          f"Sybil with no floor.\n"
          f"   Section 7.3.2 puts its analytical capture at "
          f"(1-alpha) + alpha*x/(1+x) of B.")
    print(f"{'x':>5}{'demand':>8}{'clean':>9}{'attacked':>10}{'retained':>10}"
          f"{'adv/B':>8}{'queue a/c':>11}{'recovery':>10}")
    rows = []
    for x in (0.0, 0.1, 1.0):
        # Adversarial training traffic dilutes honest weights rather than
        # adding capacity: floors still sum to alpha*B.
        share = x / (1.0 + x)
        adv_floor = ALPHA * B * share
        honest = {r: f * (1.0 - share) for r, f in floors.items()}
        for k in (1.0, 2.0, 5.0):
            scaled = [dict(e, usd=e["usd"] * k) for e in test]
            want = sum(e["usd"] for e in scaled)
            base_s, _, base_left = replay(scaled, B, ALPHA, dict(honest))
            atk_s, _, left = replay(scaled, B, ALPHA, dict(honest),
                                    attack_usd_per_block=per_block,
                                    attack_window=(a0, a1),
                                    adv_floor=adv_floor)
            b_adm = sum(v["admitted"] for v in base_s.values())
            a_adm = sum(v["admitted"] for v in atk_s.values())
            q = max((v["queue"] for v in atk_s.values()), default=0)
            q_clean = max((v["queue"] for v in base_s.values()), default=0)
            adv = sum(v["adv_admitted"] for v in atk_s.values())
            after = sorted(b for b in set(atk_s) | set(base_s) if b * BIN_S > a1)
            drained = None
            for b in after:
                if (atk_s.get(b, {}).get("queue", 0)
                        <= base_s.get(b, {}).get("queue", 0)):
                    drained = b * BIN_S
                    break
            rec = None if drained is None else (drained - a1) / 3600.0
            rows.append({"x": x, "demand_scale": k, "demand_usd": want,
                         "adv_floor_usd": adv_floor,
                         "capture_analytic": (1 - ALPHA) + ALPHA * share,
                         "admitted_clean_usd": b_adm,
                         "admitted_attacked_usd": a_adm,
                         "goodput_retained": a_adm / b_adm if b_adm else None,
                         "peak_queue": q, "peak_queue_clean": q_clean,
                         "recovery_hours": rec,
                         "undelivered_clean": base_left,
                         "undelivered_attacked": left,
                         "adversary_usd": adv, "adversary_x_B": adv / B})
            r = rows[-1]
            rs = "never" if rec is None else f"{rec:.1f} h"
            print(f"{x:>5.1f}{k:>7.0f}x{100*b_adm/want:>8.1f}%{100*a_adm/want:>9.1f}%"
                  f"{100*r['goodput_retained']:>9.1f}%{r['adversary_x_B']:>8.2f}"
                  f"{f'{q}/{q_clean}':>11}{rs:>10}")

    base = next(r for r in rows if r["x"] == 0.0 and r["demand_scale"] == 1.0)
    worst = next(r for r in rows if r["x"] == 1.0 and r["demand_scale"] == 1.0)
    print(f"\n   fresh Sybil takes {base['adversary_x_B']:.2f}x B "
          f"(analytic {base['capture_analytic']:.2f}); "
          f"x=1 takes {worst['adversary_x_B']:.2f}x B "
          f"(analytic {worst['capture_analytic']:.2f})")
    print(f"   envelope is {1 + ATTACK_H / 24:.2f}x B throughout")

    out = {"schema": 2, "capacity": cap, "B_usd": B, "alpha": ALPHA,
           "attack_hours": ATTACK_H, "attack_usd_per_block": per_block,
           "envelope_x_B": 1 + ATTACK_H / 24, "rows": rows}
    json.dump(out, open(f"{HERE}/data/capacity_recovery.json", "w"), indent=1)
    print("\nwrote data/capacity_recovery.json")


if __name__ == "__main__":
    main()
