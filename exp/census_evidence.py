#!/usr/bin/env python3
"""Build the appendix provenance table for the ten-stack census.

Table 13 in the body gives each verdict's decisive fact. This builds the
complementary record: for every stack, the contract the reading was taken
from, the call or slot that decides it, the evidence level, and the block and
date at which it was read. A reader who wants to recheck a verdict should not
have to open the artifact to find out what to call.

Everything is taken from the frozen census records -- census/data for the
seven stacks read on 2026-08-23 and data/*_census.json for the three read on
2026-08-24 -- except the free-text description of what each call decides,
which restates the body table in call terms.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# What the verdict turns on, expressed as the call or slot a reader repeats.
READS = {
    "hyperlane": ("Mailbox", r"\texttt{defaultIsm()}; \texttt{interchainSecurityModule()} "
                             r"per route; \texttt{owner()} and \texttt{getOwners()} on the "
                             r"installing Safe"),
    "wormhole":  ("Core Bridge", r"\texttt{getCurrentGuardianSetIndex()}; the "
                                 r"\texttt{submitContractUpgrade} governance path"),
    "layerzero": ("EndpointV2", r"\texttt{registerLibrary()} owner; \texttt{getConfig()} "
                                r"on receiveUln302"),
    "ccip":      ("Router", r"\texttt{owner()} on Router and RMN; "
                            r"\texttt{getCurrentRateLimiterState()} per lane"),
    "axelar":    ("Gateway", r"verified source: upgrades arrive through the network "
                             r"being secured; no per-message hook"),
    "across":    (r"Ethereum\_SpokePool", r"verified source: no configurable per-message "
                                         r"admission point"),
    "connext":   ("ConnextDiamond", r"verified source: verification fixed in the router "
                                    r"network"),
    "cctp":      ("TokenMessenger", r"ERC1967 implementation and admin slots; "
                                    r"\texttt{signatureThreshold}; "
                                    r"\texttt{getNumEnabledAttesters}; "
                                    r"\texttt{burnLimitsPerMessage}"),
    "polygon":   ("RootChainManager", r"\texttt{getMinDelay()}; holders of "
                                      r"\texttt{PROPOSER\_ROLE}, \texttt{EXECUTOR\_ROLE} "
                                      r"and \texttt{DEFAULT\_ADMIN\_ROLE}"),
    "debridge":  ("DeBridgeGate", r"ProxyAdmin \texttt{owner()}; \texttt{getOwners()} on "
                                  r"the Safe; \texttt{getOracleInfo()} per signer; "
                                  r"\texttt{excessConfirmations}"),
}
LEVEL = {"architectural": "src"}
LABEL = {"hyperlane": "Hyperlane", "wormhole": "Wormhole", "layerzero": "LayerZero v2",
         "ccip": "CCIP", "axelar": "Axelar", "across": "Across v3", "connext": "Connext",
         "cctp": "CCTP", "polygon": "Polygon PoS", "debridge": "deBridge"}
ORDER = ["hyperlane", "wormhole", "layerzero", "ccip", "axelar", "across",
         "connext", "cctp", "polygon", "debridge"]


def short(a):
    # The brace matters: an address ending in letters would otherwise make TeX
    # read \ldotsD239 as one control sequence.
    return f"{a[:6]}\\ldots{{}}{a[-4:]}"


def main() -> None:
    merged = json.load(open(f"{HERE}/census/data/census_merged.json"))["stacks"]
    sep = {x["name"]: x.get("contract_addr")
           for x in json.load(open(f"{HERE}/separability.json"))["stacks"]}
    rows = []

    for s in merged:
        name = s["stack"]
        label, call = READS[name]
        # The address recorded is the contract a reader calls, which for
        # Hyperlane is the Mailbox rather than the module it currently points
        # at: the default ISM is exactly the state under test and changes.
        addr = (s.get("extras", {}).get("contract")
                or s.get("extras", {}).get("endpoint")
                or s.get("extras", {}).get("router"))
        src = "census_merged"
        if not addr:
            # A contract identity is not a block-dependent reading, so taking it
            # from the later separability run does not import that run's state;
            # we record where it came from rather than silently mixing sources.
            addr = sep.get(name)
            src = "separability"
        rows.append({"stack": name, "contract": label, "address": addr,
                     "address_source": src,
                     "read": call, "verdict": s["separability"],
                     "level": LEVEL.get(s["measurement_depth"], "chain"),
                     "block": s["block"], "date": s["generated_at"][:10]})

    for name in ("cctp", "polygon", "debridge"):
        d = json.load(open(f"{HERE}/data/{name}_census.json"))
        label, call = READS[name]
        key = next(k for k in d["contracts"] if k.startswith(label.replace("\\", "")[:12]))
        rows.append({"stack": name, "contract": label,
                     "address": d["contracts"][key]["addr"], "read": call,
                     "address_source": "stack_census",
                     "verdict": d.get("verdict"), "level": "chain",
                     "block": None, "date": d["generated_at"][:10]})

    rows.sort(key=lambda r: ORDER.index(r["stack"]))
    missing = [r["stack"] for r in rows if not r["address"]]
    if missing:
        raise SystemExit(f"no address recovered for: {', '.join(missing)}")

    levels = {}
    for r in rows:
        levels[r["level"]] = levels.get(r["level"], 0) + 1
    assert levels == {"chain": 7, "src": 3}, levels

    out = {"schema": 1, "stacks": len(rows), "levels": levels, "rows": rows}
    json.dump(out, open(f"{HERE}/data/census_evidence.json", "w"), indent=1)

    lines = []
    for r in rows:
        blk = f"{r['block']:,}".replace(",", "{,}") if r["block"] else "---"
        lines.append(f"{LABEL[r['stack']]} & \\texttt{{{short(r['address'])}}} & "
                     f"{r['read']} & \\textsc{{{r['level']}}} & {blk} & {r['date']} \\\\")
    frag = "\n".join(lines)
    open(f"{HERE}/data/census_evidence_rows.tex", "w").write(frag + "\n")

    print(f"{len(rows)} stacks; {levels['chain']} chain, {levels['src']} src")
    for r in rows:
        print(f"  {LABEL[r['stack']]:>13} {r['address'][:10]}... "
              f"{r['level']:>5} block {r['block'] or '-'} {r['date']}")
    print("\nwrote data/census_evidence.json and data/census_evidence_rows.tex")


if __name__ == "__main__":
    main()
