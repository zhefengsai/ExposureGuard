# Placement-study selection protocol

## Purpose

The ten-stack study tests whether the placement criterion is executable and
discriminates among deployed architectures. It is a purposive architectural
study, not an estimate of industry prevalence, market share, or TVL coverage.
Verdict counts apply only to these ten stacks.

## Snapshot and unit of analysis

- Snapshot: Ethereum mainnet blocks 25,811,875--25,818,293 (August 2026).
- Unit: one production interoperability stack, not one contract or chain pair.
- Quantitative Hyperlane results additionally cover Base, Arbitrum, and BSC.
- A verdict records the shared verification root, inheritance mode,
  destination admission point, detection-gap bound, upgrade path, and
  installation/verifier key sets.

## Inclusion rationale

The study combines two deliberately different groups. The first covers modular
messaging stacks for which an Ethereum application population can be
enumerated. The second adds production bridge and issuer architectures chosen
to exercise distinct verifier and governance paths. Inclusion was not
conditioned on the resulting separability verdict.

| Stack | Inclusion rationale | Evidence level |
|---|---|---|
| Hyperlane | Default ISM inheritance and enumerable warp-route population | On-chain, four-chain measurement |
| LayerZero v2 | Default receive-library inheritance and configurable DVNs | On-chain Ethereum reads |
| CCIP | Shared DON with deployed per-lane buckets | On-chain Ethereum reads |
| Wormhole | Shared guardian root and token-bridge holdings | On-chain, five-chain measurement |
| Axelar | Validator-authorized gateway and interchain governance | Verified source architecture |
| Across v3 | Optimistic admission path without a configurable message hook | Verified source architecture |
| Connext | Router-network verification without a destination hook | Verified source architecture |
| CCTP | Issuer attestation with a non-proxy destination minter | On-chain Ethereum reads |
| Polygon PoS | Checkpoint verification and deployed timelock roles | On-chain Ethereum reads |
| deBridge | Oracle verification separated from proxy administration | On-chain Ethereum reads |

## Explicit non-claims

- The ten stacks are not the top ten by TVL or message volume.
- The set is not exhaustive over canonical rollup bridges, liquidity networks,
  or discontinued interoperability stacks.
- Verdict counts must not be extrapolated to an ecosystem-wide percentage.
- Address-level key disjointness does not establish organizational independence.
- Source-derived rows are not equivalent to the multi-chain measurements used
  for Hyperlane and Wormhole.

## Reproduction

`run.py` and `aggregate.py` reproduce the seven core census rows. The paper's
three additional on-chain rows (CCTP, Polygon PoS, and deBridge) are reproduced
by `../separability_check.py` from the frozen JSON inputs in `../data/`.
Derivations stop if a verdict disagrees with its recorded fields.
`CENSUS-RESULTS.md` contains the frozen core output; the paper reports the
evidence asymmetry in its study-limitations paragraph.
