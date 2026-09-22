#!/usr/bin/env python3
"""Freeze the bytecode check of Equation 1 so the manuscript can cite it.

BurstEnvelope.t.sol drains the deployed module over four detection gaps and
compares what it admits with the refill-only formula and with burst+refill.
The compressed manuscript kept the forward reference to this check but lost
the numbers; recording them here lets the claim checker guard them.

Run:  cd contracts && forge test --match-contract BurstEnvelope -vv
"""
from __future__ import annotations

import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
B_UNITS = 100_000          # BurstEnvelope.t.sol: B = 100_000e18
D_DAYS = 1.0


def main() -> None:
    out = subprocess.run(
        ["forge", "test", "--match-contract", "BurstEnvelope", "-vv"],
        cwd=os.path.join(HERE, "contracts"),
        capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise SystemExit(out.stdout[-2000:] + out.stderr[-2000:])

    gaps, cur = [], None
    for line in out.stdout.splitlines():
        s = line.strip()
        m = re.fullmatch(r"(30 min|6 h|24 h|7 d)", s)
        if m:
            cur = {"gap": m.group(1)}
            gaps.append(cur)
            continue
        m = re.match(r"(paper formula|measured|burst\+refill)\s*:\s*(\d+)", s)
        if m and cur is not None:
            cur[m.group(1).replace("+", "_").replace(" ", "_")] = int(m.group(2))
    if len(gaps) != 4 or any(len(g) != 4 for g in gaps):
        raise SystemExit(f"could not parse forge output: {gaps}")

    for g in gaps:
        if g["measured"] > g["burst_refill"]:
            raise SystemExit(f"{g['gap']}: measured exceeds the envelope")
        if g["measured"] <= g["paper_formula"]:
            raise SystemExit(f"{g['gap']}: refill-only formula was not exceeded")

    res = {"schema": 1, "B_units": B_UNITS, "D_days": D_DAYS,
           "note": "units are budget units; the module admits at most burst+refill",
           "gaps": gaps,
           "burst_understatement_at_30min":
               gaps[0]["burst_refill"] / gaps[0]["paper_formula"]}
    json.dump(res, open(f"{HERE}/data/burst_envelope.json", "w"), indent=1)

    print(f"{'gap':>8}{'refill only':>14}{'measured':>12}{'burst+refill':>15}")
    for g in gaps:
        print(f"{g['gap']:>8}{g['paper_formula']:>14,}{g['measured']:>12,}"
              f"{g['burst_refill']:>15,}")
    print(f"\nomitting the burst understates the 30-minute bound "
          f"{res['burst_understatement_at_30min']:.0f}-fold")
    print("wrote data/burst_envelope.json")


if __name__ == "__main__":
    main()
