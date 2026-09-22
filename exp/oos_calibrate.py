#!/usr/bin/env python3
"""Per-chain out-of-sample budget calibration.

For every measured chain, train on the first 60 days, freeze the budget and
usage-derived floor weights, and replay the remaining suffix.  This supplies a
spatial replication of the Ethereum result without pooling chains that have
different demand shifts or observation lengths.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime

from delay import pct
from sim import Bucket

HERE = os.path.dirname(os.path.abspath(__file__))
ZERO = "0x" + "0" * 40
D = 86400.0
TRAIN_DAYS = 60
HEADROOM = 1.07
ALPHA = 0.8
COLLATERAL = 9.86e6  # Ethereum default-module collateral used in the paper
CHAINS = ("ethereum", "base", "arbitrum", "bsc")


def load_chain(chain="ethereum"):
    ev = json.load(open(f"{HERE}/data/events_{chain}.json"))
    for e in ev:
        e["ts"] = datetime.strptime(e["t"], "%Y-%m-%dT%H:%M:%S").timestamp()
    ev.sort(key=lambda e: e["ts"])
    return ev


def load_eth():
    """Compatibility helper used by robustness.py."""
    return load_chain("ethereum")


def peak_daily(events):
    d = defaultdict(float)
    for e in events:
        d[e["t"][:10]] += e["usd"]
    if not d:
        return 0.0, None, d
    day = max(d, key=d.get)
    return d[day], day, d


def usage_weights(events, universe):
    used = defaultdict(float)
    for e in events:
        used[e["route"]] += e["usd"]
    tot = sum(used.values()) or 1.0
    return {r: used.get(r, 0.0) / tot for r in universe}


def admit_with_fixed_floors(events, B, floors, surplus_cap):
    """Replay with precomputed floors (not re-derived from the stream)."""
    t0 = events[0]["ts"]
    fb = {r: Bucket(floors.get(r, 0.0), t0) for r in floors}
    surplus = Bucket(surplus_cap, t0)
    adm = defaultdict(float)
    deferred = 0
    for e in events:
        r, need = e["route"], e["usd"]
        if r not in fb:
            fb[r] = Bucket(0.0, t0)
            floors[r] = 0.0
        got = fb[r].take(e["ts"], need)
        if got < need:
            got += surplus.take(e["ts"], need - got)
        if got < need - 1e-9:
            deferred += 1
        adm[r] += got
    return adm, deferred


def envelope(B, delta_h, window_h=24.0):
    return B + B * (delta_h / window_h)


def run_chain(chain):
    ev = load_chain(chain)
    ids = []
    try:
        ids = [r["router"][2:].lower()
               for r in json.load(open(f"{HERE}/data/hl_ism_{chain}.json"))
               if r.get("ism") == ZERO]
    except FileNotFoundError:
        pass

    t0 = ev[0]["ts"]
    cut = t0 + TRAIN_DAYS * D
    train = [e for e in ev if e["ts"] < cut]
    test = [e for e in ev if e["ts"] >= cut]
    assert train and test, "empty train or test"

    peak_tr, day_tr, _ = peak_daily(train)
    peak_te, day_te, _ = peak_daily(test)
    peak_all, day_all, _ = peak_daily(ev)
    B = round(peak_tr * HEADROOM, -3)

    universe = set(ids) | {e["route"] for e in train} | {e["route"] for e in test}
    w = usage_weights(train, universe)
    floors = {r: ALPHA * B * w[r] for r in universe}
    surplus_cap = (1.0 - ALPHA) * B

    # Deferral on test with frozen floors (retry simulator, same as RQ5).
    # delay.simulate re-derives floors from the events it sees — override by
    # passing only test events but injecting train weights via a thin wrapper.
    res, blocked, n = simulate_fixed(test, B, ALPHA, ids, w)
    delays = [d for _, d in res]
    nz = [d for d in delays if d > 0]

    adm, hard_defer = admit_with_fixed_floors(test, B, dict(floors), surplus_cap)
    want = sum(e["usd"] for e in test)
    got = sum(adm.values())

    return {
        "chain": chain,
        "train_days": TRAIN_DAYS,
        "train_n": len(train),
        "test_n": len(test),
        "train_span": [train[0]["t"][:10], train[-1]["t"][:10]],
        "test_span": [test[0]["t"][:10], test[-1]["t"][:10]],
        "peak_train": peak_tr,
        "peak_train_day": day_tr,
        "peak_test": peak_te,
        "peak_test_day": day_te,
        "peak_all": peak_all,
        "peak_all_day": day_all,
        "peak_test_over_train": peak_te / peak_tr if peak_tr else None,
        "B": B,
        "alpha": ALPHA,
        "headroom": HEADROOM,
        "test_deferral_rate": len(nz) / max(len(delays), 1),
        "test_deferred_count": len(nz),
        "test_p95_delay_h": pct(nz, 0.95) / 3600 if nz else 0.0,
        "test_undelivered_7d": blocked,
        "test_messages": n,
        "test_admitted_fraction_hard": got / want if want else 1.0,
        "test_hard_partial_rejects": hard_defer,
        "envelope_6h": envelope(B, 6),
        "envelope_6h_vs_collateral": (COLLATERAL / envelope(B, 6)
                                               if chain == "ethereum" else None),
        "pause_only_6h": COLLATERAL if chain == "ethereum" else None,
        "note": (
            "Per-chain parameters are frozen after 60 days. Demand shift is "
            "reported rather than assumed favourable."
        ),
    }


def main():
    chains = {chain: run_chain(chain) for chain in CHAINS}
    eth = chains["ethereum"]
    out = {
        "schema": 2,
        "mode": "per_chain_60_day_train_frozen_suffix_test",
        "chains": chains,
        "summary": {
            "chains": len(chains),
            "test_messages": sum(x["test_messages"] for x in chains.values()),
            "test_deferred_count": sum(x["test_deferred_count"] for x in chains.values()),
            "test_undelivered_7d": sum(x["test_undelivered_7d"] for x in chains.values()),
        },
        # Preserve the original Ethereum fields for downstream artifact scripts.
        **{k: v for k, v in eth.items() if k != "chain"},
    }
    path = f"{HERE}/data/oos_calibrate.json"
    json.dump(out, open(path, "w"), indent=1)
    print(json.dumps(out, indent=2))
    print(f"\nwrote {path}")


def simulate_fixed(events, B, alpha, ids, train_weights):
    """Like delay.simulate but floors come from train_weights, not test usage."""
    universe = set(ids) | {e["route"] for e in events} | set(train_weights)
    floors = {r: alpha * B * train_weights.get(r, 0.0) for r in universe}

    lvl = {r: floors[r] for r in universe}
    last = {r: events[0]["ts"] for r in universe}
    s_cap = (1 - alpha) * B
    s_lvl, s_last = s_cap, events[0]["ts"]
    retry_step = 600.0
    horizon = 7 * D

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
        nonlocal s_lvl
        need, r = e["usd"], e["route"]
        if r not in floors:
            floors[r] = 0.0
            lvl[r] = 0.0
            last[r] = t
        f = refill_floor(r, t)
        take = min(f, need)
        rest = need - take
        if rest <= 0:
            lvl[r] = f - take
            return True
        s = refill_surplus(t)
        if s >= rest:
            lvl[r] = f - take
            s_lvl = s - rest
            return True
        return False

    pending = []
    out = []
    idx = 0
    end = events[-1]["ts"] + horizon
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


if __name__ == "__main__":
    main()
