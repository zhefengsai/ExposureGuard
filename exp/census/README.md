# Cross-stack verification-root study

Unified measurement pipeline for Table 5 in the ExposureGuard paper. The
purposive selection rule, inclusion rationale, and explicit non-claims are in
[`SELECTION.md`](SELECTION.md).

## Quick start

```bash
cd exp/census
export ETH_RPC_URL=https://ethereum-rpc.publicnode.com   # optional

python3 run.py --quick      # ~3 min: 100 OApps, 40 CCIP pools
python3 run.py              # ~30–45 min: full LZ + CCIP on Ethereum
python3 run.py --import-only  # Hyperlane + Wormhole + architectural only
```

## Outputs

| File | Content |
|------|---------|
| `data/census_*.json` | Per-stack records (schema in `schema.json`) |
| `data/census_merged.json` | All stacks |
| `data/census_aggregate.json` | Within-sample verdict counts + strict TVL fields |
| `CENSUS-RESULTS.md` | Frozen seven-stack core output |

## Stacks

| Adapter | Depth | Source |
|---------|-------|--------|
| `hyperlane` | imported | `../../data/hl_ism_*.json`, `../../data/stock_multi.json` |
| `wormhole` | imported | `../../data/wh_custody_multi.json` |
| `layerzero` | on-chain | LZ metadata + `getReceiveLibrary` / ULN config |
| `ccip` | on-chain | CCIP directory API + TokenPool rate limiters |
| `architectural` | spec | Axelar, Across, Connext (+ `separability.json`) |

Predicate definitions: `CONTROLS.md`.

The paper adds CCTP, Polygon PoS, and deBridge through
`../separability_check.py` and frozen inputs in `../../data/`, for ten rows total.

## Pattern P result

The seven-stack core pipeline and three additional frozen rows form the
ten-stack table in the paper. Counts are descriptive of this purposive sample
and are not an ecosystem prevalence estimate.
Strict measurable TVL is available only for Hyperlane and Wormhole
(approximately **$1.18B** combined).
