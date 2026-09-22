#!/usr/bin/env python3
"""How fast must detection be before a pre-committed budget stops helping?

Section 2 argues that detectors and ExposureGuard compose: detectors shorten
t1, the budget bounds what leaves before it.  That was an assertion.  Here we
make it an experiment by adding the strongest baseline a reviewer can ask for,
an ORACLE detector: it never misses, never raises a false alarm, and pauses the
root exactly `delta_d` after t0.  Nothing deployable can beat it.

Admitted value is then

    oracle(delta_d)  = min(stock, R * delta_d)      R = adversary drain rate
    guard(delta_d)   = min(stock, B * (1 + delta_d / D))

and the quantity of interest is the crossover delta* where the two are equal.
Below delta* the detector alone suffices and the budget is redundant; above it
the budget is doing the work.

R has two independent ceilings under F_S and we report both.

  (a) Blockspace.  The adversary needs one delivery per collateralised route to
      touch the whole stock.  That is a fixed gas cost, so the drain time is a
      block count, which makes the bound insensitive to the exact gas limit.
      We sweep the fraction of block gas the adversary captures.

  (b) Realised incident rates.  The three incidents in Section 6.2 drained
      measured amounts over measured intervals.  Those rates are far below the
      blockspace ceiling and give the conservative end of the bracket.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

STOCK = 9_857_090.0                       # audited Ethereum collateral (baselines.py)
D = 1.0                                   # refill window, days
BLOCK_S = 12.0                            # Ethereum slot time
BLOCK_GAS = 36_000_000                    # assumption, swept below
ROUTES = 116                              # Ethereum routes on the default module
GAS_PER_DELIVERY = 156_641                # deployed default ISM, no guard (gas_deployed.json)

# (name, loss USD, hours from t0 to operator response) -- incidents.py
INCIDENTS = [("Wormhole", 326e6, 0.5), ("Nomad", 190e6, 8.28), ("Ronin", 625e6, 144.0)]
TURNOVER_LO, TURNOVER_HI = 0.01235, 0.01454      # B as a fraction of stock, incidents.py

SHARES = [1.0, 0.5, 0.25, 0.1, 0.05]      # fraction of block gas the adversary captures


def crossover_days(stock: float, rate_per_day: float, B: float) -> float:
    """Smallest delta where guard admits no more than the oracle detector.

    Solve R*d = B*(1 + d/D) on the branch below the stock ceiling; if the
    detector saturates the stock first, the crossover is where it saturates.
    """
    denom = rate_per_day - B / D
    if denom <= 0:
        return float("inf")               # budget never binds: drain is slower than refill
    d = B / denom
    return min(d, stock / rate_per_day)


def fmt(days: float) -> str:
    s = days * 86400.0
    if s < 90:
        return f"{s:.1f} s"
    if s < 5400:
        return f"{s/60:.1f} min"
    if days < 2:
        return f"{days*24:.2f} h"
    return f"{days:.1f} d"


def main() -> None:
    B = float(json.load(open(f"{HERE}/data/sim3.json"))["Bc"]["ethereum"])
    gas_total = ROUTES * GAS_PER_DELIVERY

    print(f"stock ${STOCK:,.0f} | B ${B:,.0f} | D {D:.0f} d")
    print(f"drain needs {ROUTES} deliveries x {GAS_PER_DELIVERY:,} gas = {gas_total/1e6:.2f}M gas\n")

    print("(a) blockspace-bounded adversary")
    print(f"{'block share':>12}{'blocks':>9}{'drain time':>12}"
          f"{'rate $/s':>14}{'crossover':>12}{'guard@6h':>12}")
    block_rows = []
    for share in SHARES:
        blocks = max(1.0, gas_total / (share * BLOCK_GAS))
        drain_s = blocks * BLOCK_S
        rate_day = STOCK / (drain_s / 86400.0)
        d = crossover_days(STOCK, rate_day, B)
        block_rows.append({"block_share": share, "blocks": blocks,
                           "drain_seconds": drain_s, "rate_usd_per_day": rate_day,
                           "crossover_days": d, "crossover": fmt(d)})
        print(f"{share:>12.2f}{blocks:>9.2f}{fmt(drain_s/86400.):>12}"
              f"{rate_day/86400.:>14,.0f}{fmt(d):>12}"
              f"{min(STOCK, B*(1+.25/D))/1e6:>11,.2f}M")

    print("\n(b) incident-realised drain rates, B sized at measured turnover")
    print(f"{'incident':>10}{'loss':>10}{'gap h':>8}{'rate $/h':>13}"
          f"{'crossover lo':>14}{'crossover hi':>14}")
    inc_rows = []
    for name, loss, hours in INCIDENTS:
        rate_day = loss / (hours / 24.0)
        lo = crossover_days(loss, rate_day, TURNOVER_LO * loss)
        hi = crossover_days(loss, rate_day, TURNOVER_HI * loss)
        inc_rows.append({"incident": name, "loss_usd": loss, "gap_hours": hours,
                         "rate_usd_per_day": rate_day,
                         "crossover_days_lo": lo, "crossover_days_hi": hi,
                         "crossover_lo": fmt(lo), "crossover_hi": fmt(hi)})
        print(f"{name:>10}{loss/1e6:>9,.0f}M{hours:>8.2f}{rate_day/24.:>13,.0f}"
              f"{fmt(lo):>14}{fmt(hi):>14}")

    out = {"schema": 1, "stock_usd": STOCK, "B_usd": B, "D_days": D,
           "block_seconds": BLOCK_S, "block_gas_assumed": BLOCK_GAS,
           "routes": ROUTES, "gas_per_delivery": GAS_PER_DELIVERY,
           "gas_to_drain": gas_total,
           "blockspace": block_rows, "incidents": inc_rows}
    json.dump(out, open(f"{HERE}/data/detector_baseline.json", "w"), indent=1)
    print("\nwrote data/detector_baseline.json")


if __name__ == "__main__":
    main()
