# ExposureGuard

Artifact for the paper **"ExposureGuard: Bounding Aggregate Exposure for
Cross-Chain Routes Sharing a Verification Root."**

ExposureGuard is a destination-side admission control for cross-chain routes
that inherit the same verification root. Instead of assigning an independent
limit to every route or token, it meters their declared value against one
aggregate budget at the scope of the shared failure domain. The artifact
contains the Solidity enforcement module, the controller and auditor, frozen
production measurements, experiment scripts, public-testnet and prospective
shadow observations, cross-stack evidence, and the scripts used to generate
the paper figures.

The artifact does **not** claim a production deployment. The public-testnet
workload is author generated, and the prospective production-data run is
read-only.

## Repository layout

```text
ExposureGuard/
├── ExposureGuard/
│   ├── contracts/        Solidity enforcement module and Foundry tests
│   ├── control/          Controller, auditor, shadow, and testnet drivers
│   └── deploy/           Example systemd units for unattended runs
├── exp/
│   ├── census/           Cross-stack placement adapters and evidence
│   ├── fig/              Generated paper figures and system-figure sources
│   └── *.py              Measurement and RQ experiment scripts
├── data/
│   ├── live/             Frozen testnet and prospective-shadow observations
│   └── *.json            Frozen traces, configurations, and derived results
├── docs/                 Calibration, contract, fault, and separability notes
├── Makefile              Reproduction shortcuts
├── requirements.txt      Python dependencies
├── CITATION.cff          Citation metadata
└── LICENSE               Apache License 2.0
```

`exp/data`, `ExposureGuard/data`, and `ExposureGuard/control/data` are relative
links to the root `data/` directory. They preserve the paths used by the
evaluated scripts and Solidity tests while keeping one authoritative copy of
every dataset.

## Paper questions and experiments

### Production measurement and problem characterization

These scripts establish the workload, shared-root population, price coverage,
and measurement bias used by all three research questions.

| Purpose | Main scripts | Frozen outputs |
|---|---|---|
| Shared-root routes and owner clusters | `hl_ism.py`, `owner_independence.py` | `hl_ism_*.json`, `owner_independence.json` |
| Chain-log reconstruction and indexer cross-check | `chainlog_xval.py`, `apply_chainlog_flow.py` | `events_multi.json`, `chainlog_xval_*.json` |
| Pricing coverage and unpriced-route triage | `pricing_coverage.py`, `price_coverage_curve.py`, `unpriced_triage.py` | `pricing_coverage.json`, `price_coverage_curve.json` |
| Cross-stack placement study | `census/run.py`, `separability_check.py`, `census_evidence.py` | `census_evidence.json`, `separability.json` |

### RQ1: How much exposure does the aggregate bound remove?

RQ1 compares ExposureGuard with the deployed pause and independently sized
per-route or per-token limits. It also measures when fast detection or
correlated demand removes the benefit of aggregation.

| Experiment | Script | Principal output |
|---|---|---|
| Aggregate budget and pause comparison | `sim3.py`, `fig1.py` | `sim3.json`, `fig/fig_gap_*.{png,pdf}` |
| Per-route and per-token baselines | `baselines.py`, `pertoken_baseline.py` | `baselines.json`, `pertoken_baseline.json` |
| Common training availability targets with atomic held-out replay | `matched_availability.py` | `matched_availability.json`, `fig/fig_matched_availability.{png,pdf}` |
| Oracle detector crossover | `detector_baseline.py` | `detector_baseline.json`, `fig/fig_detector.*` |
| Correlated-demand generalization | `synth_generalization.py` | `synth_generalization.json`, `fig/fig_synth_*.{png,pdf}` |
| Peak and cold-route sensitivity | `peak_bootstrap.py`, `bootstrap_policy.py` | `peak_bootstrap.json`, `bootstrap_policy.json` |
| Historical incident envelopes | `incidents.py` | `fig/fig_incidents_*.{png,pdf}` |

Run the offline RQ1 pipeline with:

```bash
make rq1
```

The revised common-target comparison is reproduced independently with
`python3 exp/matched_availability.py`. It calibrates on the first 60 Ethereum
days and freezes policies for 2,055 later messages. Both message-count and
value service on arrival must meet the chosen training target. At the 100%
target, the six-hour envelopes are $2.16M for ExposureGuard, $2.81M for
per-token caps, and $3.64M for per-route caps. This yields a 1.30x per-token
comparison, not the 5.1x unequal-headroom comparison in the earlier release.
Matched training targets do not imply equal held-out availability: one $615k
transfer remains unsettled under ExposureGuard. A pooled bucket without
floors serves all held-out traffic with a smaller envelope, but provides no
reserved capacity against surplus drain.

The new replay debits a message only if it can be admitted in full, uses
600-second retries, and runs through seven days after the final arrival.
It includes never-delivered messages in arrival-failure counts. Independent
caps receive no automatic capacity for groups absent from training. Token
groups use frozen symbol labels. The older `pertoken_baseline.py` reports
full-trace peak-sizing diagnostics with legacy partial-credit semantics;
its availability fields must not be interpreted as the new atomic replay.
The fixed-grid study is retrospective and was not preregistered.

### RQ2: What availability does containment cost?

RQ2 replays held-out production traffic and measures deferral, bootstrap
behavior, refill-policy churn, demand growth, price coverage, and the cost of
static per-chain partitions.

| Experiment | Script | Principal output |
|---|---|---|
| Four-chain held-out calibration | `oos_calibrate.py` | `oos_calibrate.json` |
| Clean-load deferral | `delay.py` | console summary from the frozen trace |
| Calibration-policy comparison | `calibration_compare.py` | `calibration_compare.json` |
| Cold-route bootstrap and reservation policy | `bootstrap_policy.py` | `bootstrap_policy.json` |
| Goodput, capacity, and recovery | `capacity_recovery.py` | `capacity_recovery.json` |
| Weight-refresh cadence | `refresh_sweep.py` | `refresh_sweep.json` |
| Static partition versus global oracle | `partition_cost.py` | `partition_cost.json` |
| Price-map coverage | `price_coverage_curve.py` | `price_coverage_curve.json` |

Run the offline RQ2 pipeline with:

```bash
make rq2
```

### RQ3: Does the implementation behave as the model requires?

RQ3 checks the on-chain envelope, route-count scaling, gas overhead, failure
semantics, control-plane runtime, cross-stack insertion conditions, and the two
long-running validation campaigns.

| Experiment | Location | Principal output |
|---|---|---|
| Envelope, replay, fail-closed pricing, and governance | `ExposureGuard/contracts/test/` | Foundry test report |
| Constant verification cost and provisioning scaling | `Scaling.t.sol` | `scaling.json` |
| Deployed-ISM gas A/B | contract fixture and `gas_deployed_fixture.py` | `gas_deployed.json` |
| Seven fault classes and negative controls | `fault_matrix.py`, `FaultMatrix.t.sol` | `fault_matrix.json` |
| Controller and auditor runtime | `benchmark_control_plane.py` | frozen `control_plane_benchmark.json`; local rerun under `data/reproduced/` |
| LayerZero interface feasibility | `layerzero_feasibility.py`, LayerZero contract test | `layerzero_feasibility.json` |
| Public-testnet workload | `data/live/testnet_workload.jsonl` | `testnet_summary.json`, `testnet_faults.json` |
| Fourteen-day prospective shadow | `data/live/shadow_events.jsonl` | `shadow_summary.json` |

Run the offline RQ3 checks with:

```bash
make rq3
```

## Requirements

- Python 3.11 or later
- Foundry (`forge`) for the Solidity tests
- Python packages listed in `requirements.txt`
- An archive-capable Ethereum RPC endpoint only for optional fork tests and
  recollection; the frozen offline reproduction needs no RPC endpoint

Install the Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Quick offline reproduction

The shortest reproducibility path validates the frozen inputs, runs the Python
unit tests, and executes all non-fork Solidity tests:

```bash
make verify
```

Regenerate the paper plots:

```bash
make figures
```

Generated PNG and PDF files are written to `exp/fig/`.

## Full experiment order

The budget is derived from the measured peak, so experiment order matters.
For a complete offline rerun using the committed production trace:

```bash
make data-check
make rq1
make rq2
make rq3
make figures
make data-check
```

The final `data-check` detects accidental drift in paper-critical artifacts.
Detailed calibration dependencies are documented in
`docs/CALIBRATION.md`.

## Optional network-dependent reproduction

Controller timing is host dependent, so a rerun is written under
`data/reproduced/` and does not replace the M2 Pro measurement reported in the
paper. Recollecting chain logs is optional and may take hours because public archive
endpoints are rate limited. Set RPC URLs in the environment; never commit them
or a wallet key.

```bash
cd exp
ETH_RPC_URL=<archive-rpc> python3 chainlog_xval.py
python3 apply_chainlog_flow.py
```

Fork tests can be run separately:

```bash
cd ExposureGuard/contracts
forge test --fork-url "$ETH_RPC_URL"
```

The public-testnet drivers require author-controlled test tokens and a private
key supplied only through the local environment. They are included for inspection, but the frozen observations in
`data/live/` are the supported path for reproducing the paper's reported
testnet results. No private key, keystore, RPC credential, server address, or
machine-specific path is included in this repository.

## Data provenance

- `data/events_multi.json` is the priced four-chain trace used by the replay
  experiments.
- `data/SOURCE-MANIFEST.sha256` authenticates the immutable per-chain traces
  and census inputs used to regenerate the derived artifacts.
- `data/experiment_manifest.json` records SHA-256 hashes of paper-critical
  frozen artifacts.
- `data/live/testnet_workload.jsonl` contains 1,876 completed observations from
  the 335.4-hour author-operated public-testnet run.
- `data/live/shadow_events.jsonl` contains the frozen observations used for the
  fourteen-day read-only prospective shadow result.
- Cross-stack results distinguish on-chain evidence from verified-source
  architecture and do not estimate industry prevalence.

The revised paper corresponds to release `v1.1.0-paper`; the earlier
`v1.0.0-paper` remains available for comparison. The experiment manifest now
includes 29 artifacts, including both the historical sizing baseline and the
new common-target comparison.

Run `make data-check` before and after regeneration. Recollecting external
price quotes can produce small differences because the quoted price is fetched
at collection time; the frozen trace is the source for the paper results.

## Safety and scope

ExposureGuard does not detect a verifier compromise. It limits the value that
can be admitted after a shared verification root fails and before operators
respond. The bound depends on correct installation, conservative prices, and
governance keys that are operationally independent of the failed verifier.

The `data/live/` observations are evidence of operational behavior, not an
organic workload or a production enforcement deployment. LayerZero support is
an interface-level feasibility prototype; public EndpointV2 library
registration remains owner controlled.

## Citation and license

Citation metadata is provided in `CITATION.cff`. The original ExposureGuard
code, experiment scripts, documentation, and curated data in this repository
are released under the Apache License 2.0. Bundled third-party Solidity
dependencies retain their upstream licenses. A repository DOI should be added
after archival deposit.
