# Fault Matrix Results

Budget $B$ = **$1,233,331.75**/day (`B_wei=1233331752968037000000000`, from `sim3.json` → `Bc.ethereum`)

## Summary

| Test | Status |
|---|---|
| `test_F1_NegativeControl` | **PASS** |
| `test_F1_SameBlockSurplusRace` | **PASS** |
| `test_F2_NegativeControl` | **PASS** |
| `test_F2_RefillBoundary` | **PASS** |
| `test_F3_NegativeControl` | **PASS** |
| `test_F3_ReplayAlreadyMetered` | **PASS** |
| `test_F4_NegativeControl` | **PASS** |
| `test_F4_ReorgConsistency` | **PASS** |
| `test_F5_LowerBudgetMidTraffic` | **PASS** |
| `test_F5_NegativeControl` | **PASS** |
| `test_F6_NegativeControl` | **PASS** |
| `test_F6_RaiseTimelock` | **PASS** |
| `test_F7_NegativeControl` | **PASS** |
| `test_F7_PauseMidRefill` | **PASS** |

## Fault classes

| # | Fault | Invariant | Result |
|---|---|---|---|
| F1 | Same-block dual surplus claim | sum admitted ≤ surplus | PASS |
| F2 | Refill-boundary landing | cumulative ≤ B + BΔ/D | PASS |
| F3 | Relayer replay | AlreadyMetered; charge once | PASS |
| F4 | Reorg (snapshot/revert) | messageMetered + budget restore | PASS |
| F5 | mid-traffic lowerBudget | immediate; no refund; new cap binds | PASS |
| F6 | raise before timelock | TimelockPending; succeeds after delay | PASS |
| F7 | pause mid-window (optional) | Paused; restores on unpause | PASS |

## Anti-vacuity

Each Fx has a paired `NegativeControl`. The driver refuses to export
if any pair is missing. Positive tests assert `observed > 0` before
`observed ≤ bound`.

## Reproduce

```bash
cd pilot && python3 fault_matrix.py
```
