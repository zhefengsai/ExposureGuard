# Cross-stack verification-root census

Generated: 2026-08-23T13:58:23Z

## Prevalence (pattern P)

- **7/7 stacks** match shared root + (silent inherit OR mandatory) + no destination automatic per-root bound
- Pattern stacks: hyperlane, wormhole, axelar, across, connext, layerzero, ccip
- **TVL-weighted (strict, measured only): $1,179,893,674 / $1,179,893,674** (100.0%)

## Table X

| Stack | Population | Shared root | Silent inherit | Dest. auto per-root bound | Gap bound | Separability | TVL (strict) |
|---|---|---:|---|---:|---|---|---:|
| **hyperlane** | 152 warp_route_router | Mailbox defaultIsm (0x10cf42691db303ff44f35c9ced | 116 (78%) | No | unbounded | SEPARABLE | $14,551,185 |
| **wormhole** | 4628 token_bridge_transfer | 13-of-19 guardian quorum (hash 885b7752ebedd5e3) | N/A (mandatory) | No | unbounded | NON-SEPARABLE | $1,165,342,489 |
| **axelar** | 1 gateway_gmp | Gateway inline validator multisig | N/A (mandatory) | No | unbounded | NON-SEPARABLE | — |
| **across** | 1 spoke_pool_intent | HubPool merkle root + relayer network | N/A (mandatory) | No | partial | NO-POSITION | — |
| **connext** | 1 connext_diamond | Router network + watchers | N/A (mandatory) | No | unbounded | NO-POSITION | — |
| **layerzero** | 1036 oapp | ULN302 config fingerprint e7a9ae2efb1669a9 | 3711 paths (90%) | No | unbounded | SEPARABLE | — |
| **ccip** | 203 token_pool | CCIP DON message attestation (all lanes) | N/A (mandatory) | No | partial | SEPARABLE | — |

## Claim template (paper)

> 7/7 surveyed interoperability stacks exhibit recurring pattern P (shared verification, no destination-side automatic per-root bound during the detection gap), spanning ISM-, DVN-, guardian-, and DON-based designs; measured TVL behind pattern stacks ≥ $1,179,893,674.
