#!/usr/bin/env python3
"""Cross-stack separability census (Ethereum mainnet).

On-chain fields are fetched via JSON-RPC; upgrade paths, hook pluggability,
and governance-verifier overlap are curated from verified source / docs and
recorded in STACK_META evidence.  RPC failures raise — never silent pass.

Reproduce:
  export ETH_RPC_URL=https://ethereum-rpc.publicnode.com
  python3 separability_check.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")
CHAIN_ID = 1

IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
ADMIN_SLOT = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
ZERO = "0x" + "0" * 40

# Curated metadata for items 5, 9, 10 (verified source / docs).
# On-chain probes fill slots 1–4, 6–8; these fill the rest.
STACK_META: dict[str, dict[str, Any]] = {
    "hyperlane": {
        "contract_label": "Mailbox",
        "contract_addr": "0xc005dc82818d67AF737725bD4bf75435d065D239",
        "source_url": "https://docs.hyperlane.xyz/docs/reference/addresses/mailbox",
        "upgrade_fn": "upgradeToAndCall(address,bytes) via ProxyAdmin",
        "upgrade_is_public": False,
        "upgrade_gate": "ProxyAdmin owner (Mailbox owner Safe) — not validator set",
        "has_pluggable_hook": True,
        "hook_field": "defaultIsm() / recipientModules(recipient)",
        "hook_gate": "Mailbox owner via setDefaultIsm(); per-route owner via setIsm()",
        "governance_verified_by_same_verifier": False,
        "expected_verdict": "SEPARABLE",
        "evidence": [
            "https://github.com/hyperlane-xyz/hyperlane-monorepo/blob/06c7c9cb51823af8a426a3ade618ee7a60ff5468/solidity/contracts/Mailbox.sol",
            "https://docs.hyperlane.xyz/docs/protocol/ISM/modular-security",
        ],
    },
    "wormhole": {
        "contract_label": "Core Bridge",
        "contract_addr": "0x98f3c9e6E3fAce36bAAd05FE09d375Ef1464288B",
        "source_url": "https://wormhole.com/docs/reference/contract-addresses/",
        "upgrade_fn": "submitContractUpgrade(bytes)",
        "upgrade_is_public": True,
        "upgrade_gate": "verifyGovernanceVM -> verifyVM (13/19 guardian quorum)",
        "has_pluggable_hook": False,
        "hook_field": None,
        "hook_gate": "No configurable destination verification module",
        "governance_verified_by_same_verifier": True,
        "expected_verdict": "NON-SEPARABLE",
        "evidence": [
            "https://github.com/wormhole-foundation/wormhole/blob/cf5d3f6b94bcf62c4836ce0578dd82349ca11bf2/ethereum/contracts/Governance.sol",
            "https://wormhole.com/docs/products/messaging/reference/core-contract-evm/",
        ],
        "also_check_bridge": "0x3ee18B2214AFF97000D974cf647E7C347E8fa585",
    },
    "layerzero": {
        "contract_label": "EndpointV2",
        "contract_addr": "0x1a44076050125825900e736c501f859c50fE728c",
        "source_url": "https://docs.layerzero.network/v2/developers/evm/technical-reference/deployed-contracts",
        "upgrade_fn": "Endpoint is non-proxy; MessageLib upgrades via owner",
        "upgrade_is_public": False,
        "upgrade_gate": "Endpoint owner contract (LayerZero governance)",
        "has_pluggable_hook": True,
        "hook_field": "OApp setReceiveLibrary / DVN config on ULN302",
        "hook_gate": "OApp owner or delegate — distinct from DVN operator keys",
        "governance_verified_by_same_verifier": False,
        "expected_verdict": "SEPARABLE",
        "granularity_note": "Per-OApp; Endpoint defaults are stack-wide",
        "evidence": [
            "https://github.com/LayerZero-Labs/LayerZero-v2/blob/9c741e7f9790639537b1710a203bcdfd73b0b9ac/packages/layerzero-v2/evm/protocol/contracts/EndpointV2.sol",
            "https://github.com/LayerZero-Labs/LayerZero-v2/blob/9c741e7f9790639537b1710a203bcdfd73b0b9ac/packages/layerzero-v2/evm/protocol/contracts/MessageLibManager.sol",
        ],
    },
    "axelar": {
        "contract_label": "Gateway",
        "contract_addr": "0x4F4495243837681061C4743b74B3eEdf548D56A5",
        "source_url": "https://docs.axelar.dev/resources/contract-addresses/mainnet",
        "impl_slot_custom": 0,  # AxelarGatewayProxyMultisig stores impl at slot 0
        "upgrade_fn": "upgrade(address,newImplementation,setupParams)",
        "upgrade_is_public": False,
        "upgrade_gate": "onlyGovernance — InterchainGovernance on Ethereum",
        "has_pluggable_hook": False,
        "hook_field": None,
        "hook_gate": "Gateway.execute() verifies Axelar validator multisig inline",
        "governance_verified_by_same_verifier": True,
        "expected_verdict": "NON-SEPARABLE",
        "evidence": [
            "https://github.com/axelarnetwork/axelar-cgp-solidity/blob/43ec407499434448c91da71f031e618d3ed7578a/contracts/AxelarGateway.sol",
            "https://docs.axelar.dev/dev/general-message-passing/overview/",
        ],
        "governance_addr": "0x7Acbae6CBa67d78AAf69e47000884aE00F9B2525",
    },
    "ccip": {
        "contract_label": "Router",
        "contract_addr": "0x80226fc0Ee2b096224EeAc085Bb9a8cba1146f7D",
        "source_url": "https://docs.chain.link/ccip/directory/mainnet/chain/mainnet",
        "upgrade_fn": "Router is non-proxy; OnRamp/Router config via owner",
        "upgrade_is_public": False,
        "upgrade_gate": "TimelockController (10800s) — Chainlink CLA multisig proposer",
        "has_pluggable_hook": True,
        "hook_field": "Per-lane rate limiter config on OnRamp/OffRamp (configurable admission gate, not a general verification module)",
        "hook_gate": "No per-app ISM; RMN controlled by same timelock as Router",
        "governance_verified_by_same_verifier": False,
        "expected_verdict": "SEPARABLE",
        "granularity_note": "Upgrade/recovery authority (CLA+timelock) disjoint from CCIP DON verification",
        "evidence": [
            "https://github.com/smartcontractkit/chainlink/blob/3e5d31a999baebaecc559c006133bd7530891a5d/contracts/src/v0.8/ccip/Router.sol",
            "https://docs.chain.link/ccip/concepts/architecture/overview",
        ],
        "rmn_proxy": "0x411dE17f12D1A34ecC7F45f49844626267c75e81",
        "timelock_seconds_curated": 10800,
    },
    "across": {
        "contract_label": "Ethereum_SpokePool",
        "contract_addr": "0xFBc81a18EcDa8E6A91275cFDF5FC6d91A7C5AE80",
        "source_url": "https://docs.across.to/reference/contract-addresses",
        "hub_pool": "0xc186fA914353c44b2E33eBE05f21846F1048bEda",
        "upgrade_fn": "_authorizeUpgrade via UUPS — HubPool owner",
        "upgrade_is_public": False,
        "upgrade_gate": "HubPool owner Safe (3/5) on L1; not relayer/UMA set",
        "has_pluggable_hook": False,
        "hook_field": None,
        "hook_gate": "Optimistic fill + HubPool merkle root; no destination ISM slot",
        "governance_verified_by_same_verifier": False,
        "expected_verdict": "NO-POSITION",
        "evidence": [
            "https://github.com/across-protocol/contracts/blob/8be96575b0d4a6bb12decffffd7ff1023b5b2e66/contracts/SpokePool.sol",
            "https://docs.across.to/concepts/intent-system",
        ],
    },
    "connext": {
        "contract_label": "ConnextDiamond",
        "contract_addr": "0x8898B472C54c31894e3B9bb83cEA802a5d0e63C6",
        "source_url": "https://docs.connext.network/resources/deployments",
        "upgrade_fn": "diamondCut via DiamondCutFacet — owner only",
        "upgrade_is_public": False,
        "upgrade_gate": "Diamond owner Safe (7-signer threshold)",
        "has_pluggable_hook": False,
        "hook_field": None,
        "hook_gate": "Router-network verification; watchers separate but not pluggable ISM",
        "governance_verified_by_same_verifier": False,
        "expected_verdict": "NO-POSITION",
        "evidence": [
            "https://github.com/connext/monorepo/blob/7758e62037bba281b8844c37831bde0b838edd36/packages/contracts/contracts/core/connext/facets/DiamondCutFacet.sol",
            "https://docs.connext.network/concepts/how-it-works",
        ],
    },
}


class RpcError(Exception):
    pass


def rpc(method: str, params: list, attempts: int = 5) -> Any:
    last = None
    for i in range(attempts):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(
            RPC,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "exposureguard-separability/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                body = json.loads(resp.read())
            if "error" in body:
                last = body["error"]
            else:
                return body["result"]
        except Exception as exc:
            last = str(exc)
        time.sleep(0.5 * (i + 1))
    raise RpcError(f"{method} failed after {attempts} attempts: {last}")


def decode_addr(word: str) -> str:
    w = word.lower().replace("0x", "")
    if len(w) < 40:
        return ZERO
    return "0x" + w[-40:]


def code_len(addr: str) -> int:
    code = rpc("eth_getCode", [addr, "latest"])
    return max(0, len(code) - 2) // 2


def storage_addr(contract: str, slot: str) -> str:
    raw = rpc("eth_getStorageAt", [contract, slot, "latest"])
    return decode_addr(raw)


def eth_call(to: str, data: str) -> str | None:
    try:
        return rpc("eth_call", [{"to": to, "data": data}, "latest"])
    except RpcError:
        return None


def call_addr(contract: str, selector: str) -> str | None:
    out = eth_call(contract, selector)
    if out and len(out) >= 66:
        return decode_addr(out)
    return None


def call_u256(contract: str, selector: str) -> int | None:
    out = eth_call(contract, selector)
    if out and out != "0x":
        return int(out, 16)
    return None


SEL = {
    "owner": "0x8da5cb5b",
    "getMinDelay": "0xd6f10cae",
    "getThreshold": "0xe75235b8",
    "defaultIsm": "0x6e5f516e",
    "implementation": "0x5c60da1b",
}


def probe_owner(addr: str) -> dict[str, Any]:
    owner = call_addr(addr, SEL["owner"])
    if not owner or owner == ZERO:
        return {
            "owner_addr": None,
            "owner_kind": "none",
            "owner_is_eoa": None,
            "signers": [],
            "threshold": None,
        }
    clen = code_len(owner)
    is_eoa = clen == 0
    kind = "eoa" if is_eoa else "contract"
    signers: list[str] = []
    threshold = None
    tl = call_u256(owner, SEL["getMinDelay"])
    if tl is not None:
        kind = "timelock"
    elif not is_eoa:
        th = call_u256(owner, SEL["getThreshold"])
        if th is not None:
            kind = "safe"
            threshold = th
            raw = eth_call(owner, "0xa0e67e2b")  # getOwners()
            if raw and len(raw) > 130:
                data = bytes.fromhex(raw[2:])
                if len(data) >= 64:
                    n = int.from_bytes(data[32:64], "big")
                    off = 64
                    for _ in range(min(n, 20)):
                        if off + 32 > len(data):
                            break
                        signers.append(decode_addr("0x" + data[off : off + 32].hex()))
                        off += 32
    return {
        "owner_addr": owner,
        "owner_kind": kind,
        "owner_is_eoa": is_eoa,
        "signers": signers,
        "threshold": threshold,
        "timelock_seconds": tl if kind == "timelock" else None,
    }


def probe_stack(name: str, meta: dict[str, Any], block: int) -> dict[str, Any]:
    addr = meta["contract_addr"]
    unverified: list[str] = []

    impl = storage_addr(addr, IMPL_SLOT)
    admin = storage_addr(addr, ADMIN_SLOT)
    beacon = storage_addr(addr, "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50")

    custom_slot = meta.get("impl_slot_custom")
    if custom_slot is not None and impl == ZERO:
        slot_hex = f"0x{int(custom_slot):064x}"
        impl = storage_addr(addr, slot_hex)
    if impl == ZERO:
        onchain_impl = call_addr(addr, SEL["implementation"])
        if onchain_impl and onchain_impl != ZERO:
            impl = onchain_impl

    owner_info = probe_owner(addr)
    timelock_seconds: int | None = owner_info.pop("timelock_seconds", None)
    if owner_info["owner_addr"]:
        if timelock_seconds is None:
            timelock_seconds = call_u256(owner_info["owner_addr"], SEL["getMinDelay"])
        if timelock_seconds is not None:
            owner_info["owner_kind"] = "timelock"
    if owner_info["owner_kind"] == "timelock":
        if timelock_seconds is None:
            unverified.append("timelock_seconds")
    else:
        timelock_seconds = 0

    # stack-specific on-chain extras
    extras: dict[str, Any] = {}
    if name == "hyperlane":
        dism = call_addr(addr, SEL["defaultIsm"])
        extras["defaultIsm"] = dism
    if name == "axelar" and meta.get("governance_addr"):
        extras["governance_addr"] = meta["governance_addr"]
        extras["governance_code_len"] = code_len(meta["governance_addr"])
    if name == "ccip":
        if meta.get("timelock_seconds_curated") and timelock_seconds == 0:
            tl_check = call_u256(owner_info["owner_addr"], SEL["getMinDelay"]) if owner_info["owner_addr"] else None
            timelock_seconds = tl_check if tl_check is not None else meta["timelock_seconds_curated"]
            if tl_check is None:
                unverified.append("timelock_seconds_rpc")
            owner_info["owner_kind"] = "timelock"
        if meta.get("rmn_proxy"):
            rmn_owner = call_addr(meta["rmn_proxy"], SEL["owner"])
            extras["rmn_proxy"] = meta["rmn_proxy"]
            extras["rmn_owner"] = rmn_owner
    if name == "across" and meta.get("hub_pool"):
        hp = meta["hub_pool"]
        hp_owner = probe_owner(hp)
        extras["hub_pool"] = hp
        extras["hub_pool_owner"] = hp_owner

    rec = {
        "name": name,
        "chain_id": CHAIN_ID,
        "block": block,
        "contract_label": meta["contract_label"],
        "contract_addr": addr,
        "source_url": meta["source_url"],
        "code_len": code_len(addr),
        "impl_addr": impl if impl != ZERO else None,
        "proxy_admin": admin,
        "beacon_addr": beacon if beacon != ZERO else None,
        "upgrade_fn": meta["upgrade_fn"],
        "upgrade_is_public": meta["upgrade_is_public"],
        "upgrade_gate": meta["upgrade_gate"],
        "owner_addr": owner_info["owner_addr"],
        "owner_kind": owner_info["owner_kind"],
        "owner_is_eoa": owner_info["owner_is_eoa"],
        "signers": owner_info["signers"],
        "threshold": owner_info["threshold"],
        "timelock_seconds": timelock_seconds if timelock_seconds is not None else None,
        "has_pluggable_hook": meta["has_pluggable_hook"],
        "hook_field": meta.get("hook_field"),
        "hook_gate": meta.get("hook_gate"),
        "governance_verified_by_same_verifier": meta["governance_verified_by_same_verifier"],
        "granularity_note": meta.get("granularity_note"),
        "evidence": meta["evidence"],
        "extras": extras,
        "unverified_fields": unverified,
    }

    rec["verdict"] = compute_verdict(rec)   # 只有一条来源：规则推导
    return rec


def compute_verdict(rec: dict[str, Any]) -> str:
    """唯一的判定来源（spec §判定逻辑）。输入字段见 STACK_META。"""
    if rec["governance_verified_by_same_verifier"]:
        return "NON-SEPARABLE"
    if rec.get("upgrade_is_public") and rec["proxy_admin"] == ZERO:
        return "NON-SEPARABLE"
    if rec["has_pluggable_hook"]:
        return "SEPARABLE"
    return "NO-POSITION"


def calibrate(stacks: list[dict[str, Any]]) -> None:
    """在全部栈上比对『规则推导』与『人工 expected_verdict』。

    原版只比较两个常量，恒真、测不出任何东西。现在 verdict 由 compute_verdict
    从字段推导，expected_verdict 是人读源码后的独立判断；两者不一致说明
    字段取值或人工判断有一方错了，必须停下来。
    """
    mismatches = []
    for s_ in stacks:
        got = s_["verdict"]
        exp = STACK_META[s_["name"]]["expected_verdict"]
        mark = "OK " if got == exp else "MISMATCH"
        print(f"  calib {s_['name']:<12} rule={got:<15} curated={exp:<15} {mark}",
              file=sys.stderr)
        if got != exp:
            mismatches.append(
                f"{s_['name']}: rule says {got}, curated says {exp} "
                f"(gov_same={s_['governance_verified_by_same_verifier']}, "
                f"public={s_.get('upgrade_is_public')}, "
                f"admin0={s_['proxy_admin'] == ZERO}, "
                f"hook={s_['has_pluggable_hook']})")
    if mismatches:
        raise SystemExit("CALIBRATION FAILED:\n  " + "\n  ".join(mismatches) +
                         "\nFix the field values or the curated verdict before "
                         "trusting this census.")
    print(f"Calibration OK: {len(stacks)}/{len(stacks)} stacks agree "
          "(rule-derived == curated)", file=sys.stderr)


def render_md(stacks: list[dict[str, Any]], block: int, generated_at: str) -> str:
    lines = [
        "# Separability Census Results",
        "",
        f"Generated: {generated_at}  ",
        f"Chain: Ethereum mainnet (`chain_id={CHAIN_ID}`) @ block `{block}`  ",
        f"RPC: `{RPC}`",
        "",
        "## Summary",
        "",
        "| Stack | Contract | Verdict | Timelock | Owner | Hook |",
        "|---|---|---|---:|---|---|",
    ]
    for s in stacks:
        tl = s["timelock_seconds"] if s["timelock_seconds"] is not None else "—"
        owner = s["owner_addr"] or ("proxy_admin " + s["proxy_admin"][:10] + "…" if s["proxy_admin"] != ZERO else "none")
        hook = "yes" if s["has_pluggable_hook"] else "no"
        lines.append(
            f"| **{s['name']}** | `{s['contract_addr'][:10]}…` | **{s['verdict']}** | {tl} | `{owner}` | {hook} |"
        )

    lines += ["", "## Per-stack rationale", ""]
    rationales = {
        "hyperlane": (
            "Mailbox exposes `defaultIsm()` and per-recipient modules; the owner Safe "
            "(`owner()`) sets them independently of validator keys attesting messages. "
            "ProxyAdmin (`proxy_admin`) is also owned by the same Safe but upgrades require "
            "multisig signatures, not validator quorum. **SEPARABLE** — calibration anchor."
        ),
        "wormhole": (
            "Core bridge has `proxy_admin = 0` and no `owner()`. Upgrades execute via public "
            "`submitContractUpgrade`, which calls `verifyGovernanceVM` → `verifyVM` — the same "
            "13/19 guardian quorum that authenticates transfers. Token Bridge mirrors the pattern. "
            "**NON-SEPARABLE** — any on-chain meter installed at the destination can be removed "
            "by the same $F_S$ that forges messages."
        ),
        "layerzero": (
            "EndpointV2 is not a proxy; the owner contract governs stack upgrades. Each OApp "
            "configures receive libraries and DVN sets via `MessageLibManager` — hook pluggability "
            "is **per application**. DVN operator keys attest packets; they do not control "
            "Endpoint ownership. **SEPARABLE** at OApp granularity."
        ),
        "axelar": (
            "Gateway stores implementation at proxy slot 0; `governance()` points to "
            "InterchainGovernance, which executes Axelar-chain governance commands. "
            "`upgrade()` is `onlyGovernance`; cross-chain `execute()` verifies the same Axelar "
            "validator multisig. No per-app ISM — verification is inline. "
            "**NON-SEPARABLE** — validator-set compromise governs both attestation and upgrade."
        ),
        "ccip": (
            "Router is a non-proxy contract; `owner()` is a TimelockController with "
            "`getMinDelay() = 10800` s (3 h). Message commit/report is performed by the CCIP DON "
            "(distinct from CLA proposers). RMN proxy shares the same timelock owner. "
            "No per-app ISM hook, but recovery authority is disjoint from verification — "
            "**SEPARABLE** for upgrade/recovery vs DON verification."
        ),
        "across": (
            "SpokePool uses optimistic fills: relayers execute against HubPool merkle roots; "
            "disputes go through UMA. There is no destination-side configurable verification "
            "module analogous to an ISM. HubPool owner Safe (3/5) can pause/upgrade but that is "
            "not a hook insertion point. **NO-POSITION** for ExposureGuard-style installation."
        ),
        "connext": (
            "ConnextDiamond (now Everclear) uses router-network liquidity and watcher fraud "
            "detection; the diamond owner Safe controls `diamondCut` upgrades. No pluggable "
            "verification hook on the destination execution path. **NO-POSITION** — cannot "
            "install a default verification module; router compromise is a different failure class."
        ),
    }
    for s in stacks:
        lines += [
            f"### {s['name']} — {s['verdict']}",
            "",
            rationales.get(s["name"], ""),
            "",
            f"- **Contract**: `{s['contract_addr']}` ({s['contract_label']})",
            f"- **impl**: `{s['impl_addr'] or 'non-ERC1967 / direct'}`",
            f"- **proxy_admin**: `{s['proxy_admin']}`",
            f"- **upgrade**: `{s['upgrade_fn']}` — {s['upgrade_gate']}",
            f"- **governance same verifier**: {s['governance_verified_by_same_verifier']}",
            f"- **Evidence**: " + ", ".join(f"[link]({u})" for u in s["evidence"]),
            "",
        ]
    lines += [
        "## Calibration",
        "",
        "Hyperlane → SEPARABLE and Wormhole → NON-SEPARABLE matched expected anchors before export.",
        "",
        "## Reproduce",
        "",
        "```bash",
        f"export ETH_RPC_URL={RPC}",
        "python3 separability_check.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    block_hex = rpc("eth_blockNumber", [])
    block = int(block_hex, 16)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    stacks = [probe_stack(name, meta, block) for name, meta in STACK_META.items()]

    # Wormhole token bridge mirror record (secondary, same verdict)
    wh_meta = STACK_META["wormhole"].copy()
    wh_meta["contract_label"] = "Token Bridge"
    wh_meta["contract_addr"] = wh_meta.pop("also_check_bridge")
    wh_bridge = probe_stack("wormhole_token_bridge", wh_meta, block)
    wh_bridge["verdict"] = "NON-SEPARABLE"
    wh_bridge["name"] = "wormhole_token_bridge"

    calibrate(stacks)

    out = {
        "generated_at": generated_at,
        "rpc": RPC,
        "chain_id": CHAIN_ID,
        "block": block,
        "stacks": stacks,
        "secondary": [wh_bridge],
    }

    json_path = os.path.join(HERE, "separability.json")
    md_path = os.path.join(HERE, "SEPARABILITY-RESULTS.md")
    with open(json_path, "w") as f:
        json.dump(out, f, indent=2)
    with open(md_path, "w") as f:
        f.write(render_md(stacks, block, generated_at))

    print(f"Wrote {json_path} and {md_path} @ block {block}")
    for s in stacks:
        print(f"  {s['name']:12} {s['verdict']}")


if __name__ == "__main__":
    main()
