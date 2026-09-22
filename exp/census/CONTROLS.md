# Cross-stack census: control & gap predicates

Operational definitions for the frozen core census (`CENSUS-RESULTS.md`). Each stack
adapter fills the same JSON schema; predicates below are **boolean on the filled record**.

## Predicates

| Symbol | Field path | True when |
|--------|------------|-----------|
| **SR** | `shared_verification_root.present` | ≥1 verification component shared by ≥2 apps/routes/lanes |
| **SI** | `silent_inheritance` | `applicable=true` and `share ≥ 0.5` (Hyperlane ISM=0, LZ default receive lib) |
| **SI-N/A** | `silent_inheritance.applicable=false` | Mandatory stack-wide root (Wormhole guardians, CCIP DON, Axelar gateway) — counts as **SI_or_mandatory** |
| **AB** | `detection_gap.dest_auto_per_root_bound` | Automatic, non-binary, destination-side, **per-root** bound still enforced after root failure |
| **UDG** | `detection_gap.bound` | machine label `unbounded` or `partial`; in prose this means no control-imposed aggregate bound, not infinite economic loss |

## Control inventory columns

Every control row documents:

- **placement**: where the gate runs relative to forged attestation execution
- **scope**: what population one gate covers
- **automatic**: no human trigger required
- **on_path_under_Fs**: still consulted when verification root $F_S$ is compromised
- **active**: deployed and enabled in measurement snapshot

## Pattern P (recurring industry pattern)

A stack matches pattern **P** when:

```
SR ∧ (SI ≥ 50%  ∨  mandatory_stack_wide) ∧ ¬AB
```

Equivalently: `pattern_match.SR && pattern_match.SI_or_mandatory && pattern_match.no_dest_auto_per_root_bound`.

## Measurement depth

| Depth | Meaning |
|-------|---------|
| `on_chain_census` | Population enumerated + ≥1 on-chain probe (Hyperlane, Wormhole, LZ, CCIP) |
| `architectural` | Separability + spec/docs only (Across, Connext negative controls) |
| `imported` | Reuses prior pilot artifact without re-probing |

## CCIP-specific note

Per-lane TokenPool rate limiters are **automatic** but **scope=per_lane** (token × remote chain).
They do **not** satisfy AB. Summing lane caps can exceed any single-root budget — record in `extras.lane_cap_sum_usd`.

## Wormhole-specific note

Governor is **root_side**, **off_path** under $F_S$ compromise. Count as control but not AB.
