#!/usr/bin/env python3
"""List unresolved Ethereum deliveries without assigning missing USD values.

This is a deterministic inventory of the frozen event audit, not another price
query. A token symbol is only a display label; route addresses and contract
price keys retain the identity used by the audit.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


def inventory(events):
    unresolved = [e for e in events if e["status"] != "recovered"]
    grouped = defaultdict(list)
    for event in unresolved:
        grouped[(event["route"], event.get("price_key"), event["status"])].append(event)
    rows = []
    for (route, price_key, status), group in grouped.items():
        dated = sorted(e["t"] for e in group if e.get("t"))
        rows.append({"chain": "ethereum", "route": "0x" + route.removeprefix("0x"),
                     "symbol": group[0].get("symbol"), "standard": group[0].get("standard"),
                     "price_key": price_key, "status": status, "messages": len(group),
                     "originally_omitted": sum(not e["originally_priced"] for e in group),
                     "originally_priced": sum(e["originally_priced"] for e in group),
                     "first_dated_event": dated[0] if dated else None,
                     "last_dated_event": dated[-1] if dated else None})
    rows.sort(key=lambda r: (-r["originally_omitted"], -r["messages"], r["route"], r["status"]))
    groups = {}
    for name, selected in (("all_unresolved", unresolved),
                           ("originally_omitted", [e for e in unresolved if not e["originally_priced"]]),
                           ("originally_priced", [e for e in unresolved if e["originally_priced"]])):
        groups[name] = {"messages": len(selected),
                        "statuses": dict(sorted(Counter(e["status"] for e in selected).items()))}
    return {"schema": 1, "chain": "ethereum", "groups": groups, "routes": rows,
            "status_meanings": {
                "historical_quote_unavailable": "local amount established, but no accepted historical quote for the verified asset key",
                "synthetic_historical_quote_unavailable": "no accepted historical quote for the local synthetic-token contract; amount reconstruction was not completed",
                "unresolved_registered_collateral_transfer": "registered collateral payment could not be assigned reliably",
                "ambiguous_or_missing_receive": "router receive event could not be matched reliably"},
            "limits": "frozen audit outcomes, not proven original collector exclusion reasons; absence of an accepted historical quote does not mean an asset has no value or no quote elsewhere; symbols do not establish cross-chain asset identity; unknown USD values remain unassigned"}


def main():
    source = DATA / "a2_ethereum_event_audit.json"
    result = inventory(json.loads(source.read_text())["events"])
    result["input_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    output = DATA / "a2_unknown_inventory.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    with (DATA / "a2_unknown_inventory.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result["routes"][0]),
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(result["routes"])
    print(json.dumps(result["groups"], indent=2))
    print("wrote", output, "and CSV")


if __name__ == "__main__":
    main()
