# Calibration and data dependencies

The experiment pipeline has one authoritative root: the frozen four-chain
event trace under `data/`. Derived outputs must be regenerated in dependency
order so that a newer trace is never combined with an older budget.

## Authoritative inputs

| Quantity | Source |
|---|---|
| Per-chain priced events | `data/events_{ethereum,base,arbitrum,bsc}.json` |
| Combined priced trace | `data/events_multi.json` |
| Chain-log cross-validation | `data/chainlog_xval*.json` |
| Measured flow summary | `data/flow_multi_summary.json` |
| Route inventory | `data/hl_ism_*.json` |
| Collateral snapshot | `data/stock_multi.json` |
| Window length | one day, a design parameter rather than a measurement |

`data/SOURCE-MANIFEST.sha256` authenticates these inputs. The final paper
artifacts are authenticated separately by `data/experiment_manifest.json`.

## Offline regeneration order

Run the checked-in trace through the pipelines in this order:

```bash
make data-check
make rq1
make rq2
make rq3
make figures
make data-check
```

`sim3.py` derives the per-chain budgets from the measured peaks. The baseline,
detector, reservation, availability, and fault experiments consume those
budgets. Changing `sim3.json` without rerunning its dependants is unsupported.

The controller runtime benchmark is host dependent. Its paper value is frozen
in `data/control_plane_benchmark.json`; local reruns are written beneath
`data/reproduced/` so they cannot silently replace the reported M2 Pro result.

## Recollection

Recollecting chain logs is optional and network dependent. It can change token
prices and the observation endpoint even when the on-chain messages are the
same. For this reason the paper results use the checked-in trace. A recollection
must be treated as a new snapshot: regenerate all downstream artifacts, inspect
the changed denominators, and create a new source manifest.

