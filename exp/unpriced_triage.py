#!/usr/bin/env python3
"""Triage the unpriced deliveries into actionable governance classes.

A delivery is unpriced iff its route has no collateral token address, or the
price source returned no quote for that address (chainlog_xval.py:635-646).
That makes the split decidable rather than a judgement about route type:

  onboardable_now   collateral address present and the source quotes it today
  needs_reference   collateral address present, source has no listing
  no_local_asset    route mints a synthetic; no collateral address to price

Only the first is a price-onboarding task; the other two need a governance
reference price or a reviewed no-value exemption before their traffic can be
admitted.  Route type alone never establishes exemption.
"""
from __future__ import annotations

import json, time, urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CHAINS = ("ethereum", "base", "arbitrum", "bsc")
XVAL = {"ethereum": "chainlog_xval.json", "base": "chainlog_xval_base.json",
        "arbitrum": "chainlog_xval_arbitrum.json", "bsc": "chainlog_xval_bsc.json"}
LLAMA = {c: c for c in CHAINS}


def quote(chain: str, addrs: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    pfx = LLAMA[chain]
    for i in range(0, len(addrs), 40):
        q = ",".join(f"{pfx}:" + a for a in addrs[i:i + 40])
        for attempt in range(4):
            try:
                with urllib.request.urlopen(
                        "https://coins.llama.fi/prices/current/" + q, timeout=40) as r:
                    for k, v in json.loads(r.read())["coins"].items():
                        out[k.split(":")[1].lower()] = v["price"]
                break
            except Exception:
                time.sleep(0.5 * (attempt + 1))
    return out


def main() -> None:
    rows = []
    for chain in CHAINS:
        xval = json.loads((DATA / XVAL[chain]).read_text())
        priced = Counter(str(r["route"]).lower().removeprefix("0x")
                         for r in json.loads((DATA / f"chainlog_priced_{chain}.json").read_text())["events"])
        meta = {str(r["router"]).lower().removeprefix("0x"): r
                for r in json.loads((DATA / f"hl_ism_{chain}.json").read_text())}
        want = defaultdict(list)
        for r in xval["per_route"]:
            route = str(r["route"]).lower().removeprefix("0x")
            missing = int(r["chain_msgs"]) - priced[route]
            if missing <= 0:
                continue
            md = meta.get(route, {})
            coll = str(md.get("collateral") or "").lower()
            std = str(md.get("standard") or r.get("standard") or "")
            rows.append({"chain": chain, "route": route,
                         "symbol": str(r.get("symbol") or md.get("symbol") or "").upper(),
                         "standard": std, "collateral": coll or None,
                         "missing": missing})
            if coll.startswith("0x"):
                want[chain].append(coll)
        if want[chain]:
            px = quote(chain, sorted(set(want[chain])))
            for row in rows:
                if row["chain"] == chain and row["collateral"]:
                    row["source_quote"] = px.get(row["collateral"])

    for row in rows:
        if not row["collateral"]:
            row["class"] = "no_local_asset"
        elif row.get("source_quote") is not None:
            row["class"] = "onboardable_now"
        else:
            row["class"] = "needs_reference"

    by = defaultdict(lambda: {"routes": 0, "deliveries": 0})
    for row in rows:
        b = by[row["class"]]
        b["routes"] += 1
        b["deliveries"] += row["missing"]
    total = sum(r["missing"] for r in rows)
    out = {"scope": "deliveries omitted from the priced trace",
           "total_unpriced_deliveries": total,
           "total_unpriced_routes": len(rows),
           "method": "unpriced iff no collateral address or price source returns no quote "
                     "(chainlog_xval.py); classes are decidable from those two facts",
           "classes": {k: dict(v, share=v["deliveries"] / total) for k, v in sorted(by.items())},
           "exemption_note": "no class is an exemption decision; needs_reference and "
                             "no_local_asset both remain reject until governance supplies "
                             "an audited price or an explicit reviewed no-value classification",
           "routes": sorted(rows, key=lambda r: -r["missing"])}
    (DATA / "unpriced_triage.json").write_text(json.dumps(out, indent=2) + "\n")

    print(f"{total:,} unpriced deliveries over {len(rows)} routes\n")
    for k, v in sorted(by.items(), key=lambda kv: -kv[1]["deliveries"]):
        print(f"  {k:18} routes={v['routes']:3d}  deliveries={v['deliveries']:6,}  "
              f"({100*v['deliveries']/total:5.1f}%)")
    print("\ntop routes per class:")
    for k in sorted(by):
        print(f"  --- {k} ---")
        for r in [x for x in out["routes"] if x["class"] == k][:5]:
            print(f"      {r['chain']:9} {r['symbol']:12} {r['standard'][:22]:24} "
                  f"miss={r['missing']:5,}  quote={r.get('source_quote')}")
    print("\nwrote data/unpriced_triage.json")


if __name__ == "__main__":
    main()
