# ExposureGuard contract implementation

The on-chain implementation is under `ExposureGuard/contracts/` and is built
with Foundry and Solidity 0.8.24. `ExposureBudgetIsm.sol` is the Hyperlane
enforcement module; `LayerZeroExposureReceiveLib.sol` is an interface-level
feasibility prototype rather than a public EndpointV2 deployment.

## Enforced properties

- All routes attached to one instance draw from one aggregate budget.
- The route-floor weights cannot sum above 10,000 basis points.
- Admission consumes the earned route floor before shared surplus.
- A message is metered only after the Mailbox reports it as delivered.
- Replay cannot consume the budget twice.
- Unknown routes and unpriced assets fail closed.
- Budget decreases and pauses take effect immediately.
- Budget increases and exemptions require the governance timelock.
- A guardian may tighten or pause policy but cannot increase exposure.

The module meters declared value; it does not authenticate cross-chain
messages. Deployment therefore requires composition with the existing verifier
and an aggregation threshold that makes both modules mandatory.

## Tests

Run all offline contract tests with:

```bash
make contract-tests
```

The checked repository contains 48 non-fork tests covering the exposure
envelope, replay, pricing, governance, Sybil behavior, route-count scaling,
the seven fault cases with negative controls, and the LayerZero interface.
Fork-based A/B tests require an archive-capable Ethereum RPC endpoint and are
intentionally separate from the offline target.

## Evidence boundary

The tests establish accounting and implementation invariants, not a
cryptographic security reduction. Public-testnet traffic was author generated,
and the production-data shadow run did not enforce admission decisions.

