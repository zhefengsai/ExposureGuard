# Separability Census Results

> **Evidence boundary.** `SEPARABLE` in this report means address-level key-set
> disjointness (`S_key`) at the stated snapshot. It is an observable necessary
> condition, not proof of organizational independence (`S_admin`). Common
> operators, custody arrangements, or off-chain control may remain unknown.

Generated: 2026-09-10T04:37:30Z  
Chain: Ethereum mainnet (`chain_id=1`) @ block `25944643`  
RPC: `https://ethereum-rpc.publicnode.com`

## Summary

| Stack | Contract | Verdict | Timelock | Owner | Hook |
|---|---|---|---:|---|---|
| **hyperlane** | `0xc005dc82…` | **SEPARABLE** | 0 | `0x562dfaac27a84be6c96273f5c9594da1681c0da7` | yes |
| **wormhole** | `0x98f3c9e6…` | **NON-SEPARABLE** | 0 | `none` | no |
| **layerzero** | `0x1a440760…` | **SEPARABLE** | 0 | `0xbe010a7e3686fdf65e93344ab664d065a0b02478` | yes |
| **axelar** | `0x4F449524…` | **NON-SEPARABLE** | 0 | `none` | no |
| **ccip** | `0x80226fc0…` | **SEPARABLE** | 10800 | `0x44835bbba9d40deda9b64858095ecfb2693c9449` | yes |
| **across** | `0xFBc81a18…` | **NO-POSITION** | 0 | `none` | no |
| **connext** | `0x8898B472…` | **NO-POSITION** | 0 | `0x4d50a469fc788a3c0cdc8fd67868877dcb246625` | no |

## Per-stack rationale

### hyperlane — SEPARABLE

Mailbox exposes `defaultIsm()` and per-recipient modules; the owner Safe (`owner()`) sets them independently of validator keys attesting messages. ProxyAdmin (`proxy_admin`) is also owned by the same Safe but upgrades require multisig signatures, not validator quorum. **SEPARABLE** — calibration anchor.

- **Contract**: `0xc005dc82818d67AF737725bD4bf75435d065D239` (Mailbox)
- **impl**: `0x7b4d881c122a5e61adcffb56a2e3ce9927d53455`
- **proxy_admin**: `0x75ee15ee1b4a75fa3e2fdf5df3253c25599cc659`
- **upgrade**: `upgradeToAndCall(address,bytes) via ProxyAdmin` — ProxyAdmin owner (Mailbox owner Safe) — not validator set
- **governance same verifier**: False
- **Evidence**: [link](https://github.com/hyperlane-xyz/hyperlane-monorepo/blob/06c7c9cb51823af8a426a3ade618ee7a60ff5468/solidity/contracts/Mailbox.sol), [link](https://docs.hyperlane.xyz/docs/protocol/ISM/modular-security)

### wormhole — NON-SEPARABLE

Core bridge has `proxy_admin = 0` and no `owner()`. Upgrades execute via public `submitContractUpgrade`, which calls `verifyGovernanceVM` → `verifyVM` — the same 13/19 guardian quorum that authenticates transfers. Token Bridge mirrors the pattern. **NON-SEPARABLE** — any on-chain meter installed at the destination can be removed by the same $F_S$ that forges messages.

- **Contract**: `0x98f3c9e6E3fAce36bAAd05FE09d375Ef1464288B` (Core Bridge)
- **impl**: `0x3c3d457f1522d3540ab3325aa5f1864e34cba9d0`
- **proxy_admin**: `0x0000000000000000000000000000000000000000`
- **upgrade**: `submitContractUpgrade(bytes)` — verifyGovernanceVM -> verifyVM (13/19 guardian quorum)
- **governance same verifier**: True
- **Evidence**: [link](https://github.com/wormhole-foundation/wormhole/blob/cf5d3f6b94bcf62c4836ce0578dd82349ca11bf2/ethereum/contracts/Governance.sol), [link](https://wormhole.com/docs/products/messaging/reference/core-contract-evm/)

### layerzero — SEPARABLE

EndpointV2 is not a proxy; the owner contract governs stack upgrades. Each OApp configures receive libraries and DVN sets via `MessageLibManager` — hook pluggability is **per application**. DVN operator keys attest packets; they do not control Endpoint ownership. **SEPARABLE** at OApp granularity.

- **Contract**: `0x1a44076050125825900e736c501f859c50fE728c` (EndpointV2)
- **impl**: `non-ERC1967 / direct`
- **proxy_admin**: `0x0000000000000000000000000000000000000000`
- **upgrade**: `Endpoint is non-proxy; MessageLib upgrades via owner` — Endpoint owner contract (LayerZero governance)
- **governance same verifier**: False
- **Evidence**: [link](https://github.com/LayerZero-Labs/LayerZero-v2/blob/9c741e7f9790639537b1710a203bcdfd73b0b9ac/packages/layerzero-v2/evm/protocol/contracts/EndpointV2.sol), [link](https://github.com/LayerZero-Labs/LayerZero-v2/blob/9c741e7f9790639537b1710a203bcdfd73b0b9ac/packages/layerzero-v2/evm/protocol/contracts/MessageLibManager.sol)

### axelar — NON-SEPARABLE

Gateway stores implementation at proxy slot 0; `governance()` points to InterchainGovernance, which executes Axelar-chain governance commands. `upgrade()` is `onlyGovernance`; cross-chain `execute()` verifies the same Axelar validator multisig. No per-app ISM — verification is inline. **NON-SEPARABLE** — validator-set compromise governs both attestation and upgrade.

- **Contract**: `0x4F4495243837681061C4743b74B3eEdf548D56A5` (Gateway)
- **impl**: `0x99b5fa03a5ea4315725c43346e55a6a6fbd94098`
- **proxy_admin**: `0x0000000000000000000000000000000000000000`
- **upgrade**: `upgrade(address,newImplementation,setupParams)` — onlyGovernance — InterchainGovernance on Ethereum
- **governance same verifier**: True
- **Evidence**: [link](https://github.com/axelarnetwork/axelar-cgp-solidity/blob/43ec407499434448c91da71f031e618d3ed7578a/contracts/AxelarGateway.sol), [link](https://docs.axelar.dev/dev/general-message-passing/overview/)

### ccip — SEPARABLE

Router is a non-proxy contract; `owner()` is a TimelockController with `getMinDelay() = 10800` s (3 h). Message commit/report is performed by the CCIP DON (distinct from CLA proposers). RMN proxy shares the same timelock owner. No per-app ISM hook, but recovery authority is disjoint from verification — **SEPARABLE** for upgrade/recovery vs DON verification.

- **Contract**: `0x80226fc0Ee2b096224EeAc085Bb9a8cba1146f7D` (Router)
- **impl**: `non-ERC1967 / direct`
- **proxy_admin**: `0x0000000000000000000000000000000000000000`
- **upgrade**: `Router is non-proxy; OnRamp/Router config via owner` — TimelockController (10800s) — Chainlink CLA multisig proposer
- **governance same verifier**: False
- **Evidence**: [link](https://github.com/smartcontractkit/chainlink/blob/3e5d31a999baebaecc559c006133bd7530891a5d/contracts/src/v0.8/ccip/Router.sol), [link](https://docs.chain.link/ccip/concepts/architecture/overview)

### across — NO-POSITION

SpokePool uses optimistic fills: relayers execute against HubPool merkle roots; disputes go through UMA. There is no destination-side configurable verification module analogous to an ISM. HubPool owner Safe (3/5) can pause/upgrade but that is not a hook insertion point. **NO-POSITION** for ExposureGuard-style installation.

- **Contract**: `0xFBc81a18EcDa8E6A91275cFDF5FC6d91A7C5AE80` (Ethereum_SpokePool)
- **impl**: `non-ERC1967 / direct`
- **proxy_admin**: `0x0000000000000000000000000000000000000000`
- **upgrade**: `_authorizeUpgrade via UUPS — HubPool owner` — HubPool owner Safe (3/5) on L1; not relayer/UMA set
- **governance same verifier**: False
- **Evidence**: [link](https://github.com/across-protocol/contracts/blob/8be96575b0d4a6bb12decffffd7ff1023b5b2e66/contracts/SpokePool.sol), [link](https://docs.across.to/concepts/intent-system)

### connext — NO-POSITION

ConnextDiamond (now Everclear) uses router-network liquidity and watcher fraud detection; the diamond owner Safe controls `diamondCut` upgrades. No pluggable verification hook on the destination execution path. **NO-POSITION** — cannot install a default verification module; router compromise is a different failure class.

- **Contract**: `0x8898B472C54c31894e3B9bb83cEA802a5d0e63C6` (ConnextDiamond)
- **impl**: `non-ERC1967 / direct`
- **proxy_admin**: `0x0000000000000000000000000000000000000000`
- **upgrade**: `diamondCut via DiamondCutFacet — owner only` — Diamond owner Safe (7-signer threshold)
- **governance same verifier**: False
- **Evidence**: [link](https://github.com/connext/monorepo/blob/7758e62037bba281b8844c37831bde0b838edd36/packages/contracts/contracts/core/connext/facets/DiamondCutFacet.sol), [link](https://docs.connext.network/concepts/how-it-works)

## Calibration

Hyperlane → SEPARABLE and Wormhole → NON-SEPARABLE matched expected anchors before export.

## Reproduce

```bash
export ETH_RPC_URL=https://ethereum-rpc.publicnode.com
python3 separability_check.py
```
