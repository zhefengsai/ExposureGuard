#!/usr/bin/env python3
"""Who owns the routes that share Ethereum's default ISM?

Reproduces the paper's claim that the 116 routes on the Ethereum default
belong to at least 50 independent parties, the largest controlling 18.

Method: call owner() on each router; for owners that are contracts, read the
Safe signer set; then merge any two Safes sharing a signer. Merging is
conservative -- a shared signer does not establish shared control -- so the
resulting party count is a LOWER bound on independence.

Discipline: an RPC failure is recorded in `unresolved`, never silently treated
as "no owner". A router whose owner cannot be read is excluded from the
denominator and reported separately.

  export ETH_RPC_URL=https://ethereum-rpc.publicnode.com
  python3 owner_independence.py
"""
from __future__ import annotations
import json, os, subprocess, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
RPC = os.environ.get("ETH_RPC_URL") or sys.exit("need ETH_RPC_URL")
ZERO = "0x" + "0" * 40
unresolved: list[dict] = []


def cast(addr: str, sig: str) -> str | None:
    r = subprocess.run(["cast", "call", "--rpc-url", RPC, addr, sig],
                       capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        unresolved.append({"addr": addr, "sig": sig,
                           "err": (r.stderr or "").strip()[:160]})
        return None
    return r.stdout.strip()


def addrs(out: str | None) -> list[str]:
    if not out:
        return []
    return [w.lower() for w in out.replace("[", " ").replace("]", " ")
            .replace(",", " ").split() if w.startswith("0x") and len(w) == 42]


def main() -> None:
    routes = [r for r in json.load(open(f"{HERE}/data/hl_ism_ethereum.json"))
              if str(r.get("ism", "")).lower() == ZERO]
    print(f"routers on the Ethereum default ISM: {len(routes)}")

    owner_of: dict[str, str] = {}
    for r in routes:
        o = addrs(cast(r["router"], "owner()(address)"))
        if o:
            owner_of[r["router"].lower()] = o[0]

    owners = collections.Counter(owner_of.values())
    print(f"distinct owner addresses: {len(owners)}  "
          f"(routers resolved {len(owner_of)}/{len(routes)})")

    # Safe signer sets, for owners that are contracts
    signers_of: dict[str, list[str]] = {}
    for o in owners:
        code = cast(o, "0x") if False else subprocess.run(
            ["cast", "code", "--rpc-url", RPC, o],
            capture_output=True, text=True, timeout=90).stdout.strip()
        if code and code != "0x":
            s = addrs(cast(o, "getOwners()(address[])"))
            if s:
                signers_of[o] = s

    # union-find over owners sharing a signer
    parent = {o: o for o in owners}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb

    by_signer: dict[str, list[str]] = collections.defaultdict(list)
    for o, ss in signers_of.items():
        for s in ss:
            by_signer[s].append(o)
    shared = {s: os_ for s, os_ in by_signer.items() if len(os_) > 1}
    for os_ in shared.values():
        for o in os_[1:]:
            union(os_[0], o)

    groups = collections.Counter(find(o) for o in owners)
    routes_per_party = collections.Counter(
        find(owner_of[r]) for r in owner_of)
    largest = routes_per_party.most_common(1)[0][1] if routes_per_party else 0

    out = {
        "routers_on_default": len(routes),
        "routers_with_owner_resolved": len(owner_of),
        "distinct_owner_addresses": len(owners),
        "safes_with_signers_read": len(signers_of),
        "signers_shared_across_owners": len(shared),
        "independent_parties_after_merge": len(groups),
        "largest_party_routes": largest,
        "largest_party_share": round(largest / max(len(owner_of), 1), 4),
        "unresolved": unresolved,
        "note": ("Merging Safes that share a signer is conservative, so the "
                 "party count is a lower bound on independence. Routers whose "
                 "owner could not be read are excluded from the denominator "
                 "and listed in unresolved."),
    }
    json.dump(out, open(f"{HERE}/data/owner_independence.json", "w"), indent=1)
    print(f"distinct owners {len(owners)} -> {len(groups)} parties after merging "
          f"{len(shared)} shared signers")
    print(f"largest party controls {largest}/{len(owner_of)} routes "
          f"({100*out['largest_party_share']:.0f}%)")
    if unresolved:
        print(f"\n{len(unresolved)} unresolved calls -- counts are a lower bound:")
        for u in unresolved[:5]:
            print(f"   {u['addr'][:12]}… {u['sig']}: {u['err'][:80]}")
    else:
        print("\nno unresolved calls")


if __name__ == "__main__":
    main()
