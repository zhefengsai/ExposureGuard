#!/usr/bin/env python3
"""Organizational-level separability probe for Hyperlane.

Address-set disjointness only rules out co-located keys. This script pulls
ValidatorAnnounce storage locations (typically operator-named S3 buckets) for
the 252 MultisigISM validators and for the Mailbox Safe signers, then checks:

  1. Do any Safe signer keys themselves appear as announced validators?
  2. What operator-name tokens appear in S3 paths for the 252 validators?
  3. Do any Safe-signer announcements share an operator token with the
     MultisigISM validator set? (same org, different keys)

Validators announce on the *origin* chain they validate, not on Ethereum
destination. We map each MultisigISM leaf to its ROUTING domain, then query
that domain's ValidatorAnnounce where we have an EVM RPC + VA address.

LIMITS (read before citing any output):
  * operator_token_overlap is VACUOUS. No Safe signer publishes a
    ValidatorAnnounce record, so the signer-side set is empty and the
    intersection is empty by arithmetic, not by measurement. It cannot
    return a positive result whatever the truth.
  * same_key_intersection duplicates the address-level check already in
    hyperlane_authority_sets.json; not independent evidence.
  * Operator tokens are heuristic S3 bucket-path segments; several are chain
    names, not operators (mainnetpolygon, mainnetcelo, ...).
  What this establishes is the CEILING on what an address- or bucket-based
  test can show, not organizational independence.

Reproduce:
  export ETH_RPC_URL=https://ethereum-rpc.publicnode.com
  export PATH="$HOME/.foundry/bin:$PATH"
  python3 hyperlane_org_overlap.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time
from collections import defaultdict

HERE = pathlib.Path(__file__).parent
RPC_ETH = os.environ.get("ETH_RPC_URL") or sys.exit("需要 ETH_RPC_URL")
MAILBOX = "0xc005dc82818d67AF737725bD4bf75435d065D239"
ZERO = "0x" + "0" * 40

# Domain → (ValidatorAnnounce, RPC). Major EVM origins with public RPCs.
# Source: https://docs.hyperlane.xyz/docs/reference/addresses/deployments/validatorAnnounce
DOMAIN_VA: dict[int, tuple[str, str]] = {
    1: ("0xCe74905e51497b4adD3639366708b821dcBcff96", RPC_ETH),
    10: ("0x30f5b08e01808643221528BB2f7953bf2830Ef38", "https://optimism-rpc.publicnode.com"),
    56: ("0x7024078130D9c2100fEA474DAD009C2d1703aCcd", "https://bsc-rpc.publicnode.com"),
    137: ("0x454E1a1E1CA8B51506090f1b5399083658eA4Fc5", "https://polygon-bor-rpc.publicnode.com"),
    42161: ("0x1df063280C4166AF9a725e3828b4dAC6c7113B08", "https://arbitrum-one-rpc.publicnode.com"),
    8453: ("0x182E8d7c5F1B06201b102123FC7dF0EaeB445a7B", "https://base-rpc.publicnode.com"),
    43114: ("0x9Cad0eC82328CEE2386Ec14a12E81d070a27712f", "https://avalanche-c-chain-rpc.publicnode.com"),
    42220: ("0xCeF677b65FDaA6804d4403083bb12B8dB3991FE1", "https://celo-rpc.publicnode.com"),
    59144: ("0x62B7592C1B6D1E43f4630B8e37f4377097840C05", "https://linea-rpc.publicnode.com"),
    81457: ("0xFC62DeF1f08793aBf0E67f69257c6be258194F72", "https://blast-rpc.publicnode.com"),
    100: ("0x87ED6926abc9E38b9C7C19f835B41943b622663c", "https://gnosis-rpc.publicnode.com"),
    534352: ("0xCe74905e51497b4adD3639366708b821dcBcff96", ""),  # skip if no rpc
    324: ("0x576aF402c97bFE452Dcc203B6c3f6F4EBC92A0f5", "https://zksync-era-rpc.publicnode.com"),
    5000: ("0x1956848601549de5aa0c887892061fA5aB4f6fC4", "https://mantle-rpc.publicnode.com"),
    252: ("0x1956848601549de5aa0c887892061fA5aB4f6fC4", "https://fraxtal-rpc.publicnode.com"),
    34443: ("0x48083C69f5a42c6B69ABbAd48AE195BD36770ee2", "https://mode-rpc.publicnode.com"),
    167000: ("0x01aE937A7B05d187bBCBE80F44F41879D3D335a4", "https://rpc.mainnet.taiko.xyz"),
    130: ("0xD743801ABB6c7664B623D8534C0f5AF8cD2F1C5e", "https://unichain-rpc.publicnode.com"),
    480: ("0x047ba6c9949baB22d13C347B40819b7A20C4C53a", "https://worldchain-mainnet.g.alchemy.com/public"),
    1135: ("0x062200d92dF6bB7bA89Ce4D6800110450f94784e", "https://rpc.api.lisk.com"),
    1868: ("0x84444cE490233CFa76E3F1029bc166aa8c266907", "https://rpc.soneium.org"),
    57073: ("0x426a3CE72C1586b1867F9339550371E86DB3e396", "https://rpc-gel.inkonchain.com"),
    146: ("0x84444cE490233CFa76E3F1029bc166aa8c266907", "https://rpc.soniclabs.com"),
    80094: ("0xa377b8269e0A47cdd2fD5AAeAe860b45623c6d82", "https://rpc.berachain.com"),
    98866: ("0xbE58F200ffca4e1cE4D2F4541E94Ae18370fC405", "https://rpc.plume.org"),
}

# Known operator labels for Safe signers / Hyperlane ecosystem (curated).
# Empty means unknown — we still report address-level announcement overlap.
KNOWN_SIGNER_LABELS: dict[str, str] = {
    "0xa7eccdb9be08178f896c26b7bbd8c3d4e844d9ba": "pausable-ism-owner (also Mailbox Safe signer)",
}

# Tokens that are infrastructure noise, not org names.
STOP_TOKENS = {
    "s3", "http", "https", "us", "eu", "east", "west", "north", "south",
    "central", "1", "2", "3", "0", "mainnet", "mainnet2", "mainnet3",
    "hyperlane", "validator", "validators", "signatures", "signature",
    "checkpoint", "checkpoints", "v3", "v2", "aws", "bucket", "folder",
    "ethereum", "base", "arbitrum", "optimism", "polygon", "bsc", "avalanche",
    "celo", "linea", "gnosis", "mantle", "blast", "mode", "taiko", "scroll",
    "zksync", "fraxtal", "worldchain", "unichain", "lisk", "soneium", "ink",
    "sonic", "berachain", "plume", "chain", "rpc", "public",
}

unresolved: list[dict] = []


def cast(rpc: str, addr: str, sig: str, *args: str, timeout: int = 90) -> str | None:
    cmd = ["cast", "call", "--rpc-url", rpc, addr, sig, *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        unresolved.append({
            "addr": addr, "sig": sig, "rpc": rpc[:40],
            "err": (r.stderr or r.stdout or "").strip()[:180],
        })
        return None
    return r.stdout.strip()


def addrs(out: str | None) -> list[str]:
    if not out:
        return []
    return [w.lower() for w in re.findall(r"0x[a-fA-F0-9]{40}", out)]


def parse_locations(out: str | None) -> list[str]:
    """Extract quoted storage-location strings from cast output."""
    if not out:
        return []
    # cast prints nested string[][] with quoted paths
    locs = re.findall(r'"(s3://[^"]+|https?://[^"]+|file://[^"]+)"', out)
    if locs:
        return locs
    # sometimes unquoted s3://... tokens
    locs = re.findall(r"(s3://[A-Za-z0-9.\-_/]+|https?://[A-Za-z0-9.\-_/]+)", out)
    return locs


def operator_tokens(location: str) -> set[str]:
    """Pull human-meaningful tokens from an S3/URL storage location."""
    # s3://bucket-name/region  or  s3://bucket/folder
    loc = location.lower().strip()
    loc = re.sub(r"^s3://", "", loc)
    loc = re.sub(r"^https?://", "", loc)
    parts = re.split(r"[/\-_.]+", loc)
    toks = set()
    for p in parts:
        p = p.strip()
        if len(p) < 3 or p in STOP_TOKENS or p.isdigit():
            continue
        # skip pure hex
        if re.fullmatch(r"[0-9a-f]+", p) and len(p) >= 8:
            continue
        toks.add(p)
    return toks


def query_locations(va: str, rpc: str, validators: list[str]) -> dict[str, list[str]]:
    """Per-address getAnnouncedStorageLocations (cast nested-array decode is unreliable)."""
    out_map: dict[str, list[str]] = {}
    for i, v in enumerate(validators):
        raw = cast(rpc, va, "getAnnouncedStorageLocations(address[])(string[][])", f"[{v}]")
        locs = parse_locations(raw)
        if locs:
            out_map[v.lower()] = locs
        if (i + 1) % 40 == 0:
            print(f"    … {i + 1}/{len(validators)}")
            time.sleep(0.1)
    return out_map


def main() -> None:
    auth = json.load(open(HERE / "data/hyperlane_authority_sets.json"))
    signers = [a.lower() for a in auth["install_authority"]["signers"]]
    target_validators = {a.lower() for a in auth["verification_authority"]["validators"]}

    print(f"target validators : {len(target_validators)}")
    print(f"Safe signers      : {len(signers)}")

    # Query every origin VA we have an RPC for. Validators announce on the
    # origin they validate; Ethereum-destination MultisigISM keys therefore
    # show up on those origins' ValidatorAnnounce, not Ethereum's.
    queryable = {d: pair for d, pair in DOMAIN_VA.items() if pair[1]}
    print(f"queryable domains : {len(queryable)} {sorted(queryable)}")

    announcements: dict[str, dict] = {}  # addr -> {locations, domains, tokens}

    def record(addr: str, locs: list[str], domain: int) -> None:
        a = addr.lower()
        ent = announcements.setdefault(a, {"locations": [], "domains": [], "tokens": set()})
        for loc in locs:
            if loc not in ent["locations"]:
                ent["locations"].append(loc)
            ent["tokens"] |= operator_tokens(loc)
        if domain not in ent["domains"]:
            ent["domains"].append(domain)

    for d, (va, rpc) in sorted(queryable.items()):
        print(f"\n=== domain {d} VA={va[:10]}… ===")
        announced = cast(rpc, va, "getAnnouncedValidators()(address[])")
        if announced is None:
            print("  getAnnouncedValidators FAILED — skip domain")
            continue
        ann_addrs = addrs(announced)
        # Intersect with measured MultisigISM set; always include Safe signers
        relevant = sorted((set(ann_addrs) & target_validators) | set(signers))
        print(f"  announced={len(ann_addrs)}  relevant_to_probe={len(relevant)}")
        locs_map = query_locations(va, rpc, relevant)
        for addr, locs in locs_map.items():
            record(addr, locs, d)
        n_target_hit = sum(1 for a in locs_map if a in target_validators)
        print(f"  locations: {len(locs_map)} total, {n_target_hit} of measured MultisigISM set")

    # --- Analysis ---
    signer_set = set(signers)
    val_announced = {
        a: announcements[a]
        for a in announcements
        if a in target_validators and announcements[a]["locations"]
    }
    signer_announced = {
        a: announcements[a]
        for a in announcements
        if a in signer_set and announcements[a]["locations"]
    }

    # 1. Same-key: signer is also a MultisigISM validator (already known empty)
    same_key = sorted(signer_set & target_validators)

    # 2. Signer keys that themselves announce as validators (any domain)
    signer_as_announced_validator = sorted(signer_announced.keys())

    # 3. Operator-token overlap between signer announcements and validator announcements
    val_tokens: set[str] = set()
    for ent in val_announced.values():
        val_tokens |= set(ent["tokens"])
    signer_tokens: set[str] = set()
    for ent in signer_announced.values():
        signer_tokens |= set(ent["tokens"])
    token_overlap = sorted(val_tokens & signer_tokens)

    # 4. Cluster validators by dominant operator token
    token_to_vals: dict[str, list[str]] = defaultdict(list)
    for a, ent in val_announced.items():
        for t in ent["tokens"]:
            token_to_vals[t].append(a)

    # Coverage
    n_target_with_loc = len(val_announced)
    n_target = len(target_validators)

    # Dynamic claim based on findings
    if same_key:
        claim = (
            f"CRITICAL: {len(same_key)} Safe signer key(s) also appear in the "
            f"MultisigISM validator set — key-level separability FAILS."
        )
    elif token_overlap:
        claim = (
            f"Operator-token overlap detected ({token_overlap}): at least one "
            f"S3/URL name token is shared between a Safe-signer announcement and "
            f"a MultisigISM validator announcement — organizational co-location "
            f"is consistent with the data (not a proof, but a positive signal)."
        )
    elif signer_announced:
        claim = (
            f"{len(signer_announced)} Safe signer key(s) themselves announce as "
            f"validators under distinct operator tokens from the MultisigISM set "
            f"(token ∩ = ∅). Still not organizational independence: unlabeled "
            f"signers and generic bucket names are invisible."
        )
    else:
        claim = (
            f"No co-located key and no Safe-signer announcement sharing an "
            f"operator token with the {n_target_with_loc}/{n_target} MultisigISM "
            f"validators we could attribute on {len(queryable)} origin domains. "
            f"This rules out co-located keys and same-bucket dual roles for "
            f"labeled operators; it does not prove organizational independence "
            f"for unlabeled Safe seats."
        )

    result = {
        "measured": True,
        "claim_ceiling": claim,
        "queryable_domains": sorted(queryable),
        "coverage": {
            "target_validators": n_target,
            "with_storage_location": n_target_with_loc,
            "fraction": round(n_target_with_loc / max(n_target, 1), 4),
            "safe_signers_with_announcement": len(signer_announced),
        },
        "same_key_intersection": same_key,
        "signer_keys_that_announce": {
            a: {
                "label": KNOWN_SIGNER_LABELS.get(a),
                "locations": signer_announced[a]["locations"],
                "tokens": sorted(signer_announced[a]["tokens"]),
                "domains": signer_announced[a]["domains"],
            }
            for a in signer_as_announced_validator
        },
        "operator_token_overlap": token_overlap,
        "validator_operator_tokens_top": sorted(
            ((t, len(vs)) for t, vs in token_to_vals.items()),
            key=lambda x: -x[1],
        )[:40],
        "sample_validator_announcements": {
            a: {"locations": ent["locations"][:3], "tokens": sorted(ent["tokens"])}
            for a, ent in list(val_announced.items())[:15]
        },
        "unresolved_n": len(unresolved),
        "unresolved_sample": unresolved[:20],
    }

    (HERE / "data").mkdir(exist_ok=True)
    out_path = HERE / "data/hyperlane_org_overlap.json"
    # JSON-serialize sets
    json.dump(result, open(out_path, "w"), indent=2, default=lambda o: list(o) if isinstance(o, set) else o)

    print("\n========== RESULT ==========")
    print(f"coverage            : {n_target_with_loc}/{n_target} validators have storage locations")
    print(f"same-key ∩          : {len(same_key)}")
    print(f"signers that announce: {len(signer_announced)}")
    for a, info in result["signer_keys_that_announce"].items():
        print(f"  {a}  tokens={info['tokens']}  locs={info['locations'][:2]}")
    print(f"operator-token ∩    : {token_overlap or '∅'}")
    print(f"top operator tokens : {result['validator_operator_tokens_top'][:10]}")
    print(f"unresolved RPC calls: {len(unresolved)}")
    print(f"wrote {out_path}")
    print("\n" + result["claim_ceiling"])


if __name__ == "__main__":
    main()
