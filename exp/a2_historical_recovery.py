#!/usr/bin/env python3
"""Conservative, event-level historical recovery of Ethereum omissions.

This does not edit the frozen paper trace. It accepts a delivery only if its
receipt contains one matching Process event, one router receipt event, and one
local-collateral ERC-20 transfer from that router to the same recipient. The
transfer gives *local* units, unlike the pre-conversion message amount in
ReceivedTransferRemote. A quote must be historical at the block timestamp.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" if (HERE / "data").is_dir() else HERE.parent / "data"
ROOT = "https://eth.blockscout.com/api/v2/transactions/"
PRICE = "https://coins.llama.fi/prices/historical/"
RECEIVED = "0xba20947a325f450d232530e5f5fce293e7963499d5309a07cee84a269f2f15a6"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def get(url: str) -> dict:
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ExposureGuard-a2-audit/1.0"})
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError):
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")


def address(log: dict) -> str:
    return str(log.get("address", {}).get("hash") or "").lower().removeprefix("0x")


def single_event(event: dict, metadata: dict) -> dict:
    route = event["route"].lower().removeprefix("0x")
    tx = event["tx"]
    logs = get(ROOT + tx + "/logs").get("items", [])
    process = [log for log in logs if log.get("decoded", {}).get("method_call", "").startswith("Process(")
               and str(log.get("topics", [None, None, None, ""])[3] or "").lower().endswith(route)]
    received = [log for log in logs if address(log) == route
                and str(log.get("topics", [""])[0]).lower() == RECEIVED]
    transfers = [log for log in logs if address(log) == metadata["collateral"].lower().removeprefix("0x")
                 and str(log.get("topics", [""])[0]).lower() == TRANSFER
                 and str(log.get("topics", [None, ""])[1] or "").lower().endswith(route)]
    base = {"route": route, "tx": tx, "block": event["block"], "symbol": metadata.get("symbol")}
    if len(process) != 1 or len(received) != 1 or len(transfers) != 1:
        return dict(base, status="ambiguous_receipt", counts={"process": len(process),
                    "received": len(received), "transfer": len(transfers)})
    recv = received[0]
    transfer = transfers[0]
    recipient = str(recv.get("topics", [None, None, ""])[2] or "").lower()[-40:]
    transfer_to = str(transfer.get("topics", [None, None, ""])[2] or "").lower()[-40:]
    if recipient != transfer_to or int(transfer["index"]) <= int(recv["index"]):
        return dict(base, status="recipient_or_order_mismatch")
    stamp = str(recv["block_timestamp"])
    timestamp = int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())
    raw_message = int(recv["data"], 16)
    raw_local = int(transfer["data"], 16)
    decimals = int(metadata["decimals"])
    key = "ethereum:" + metadata["collateral"].lower()
    coins = get(PRICE + str(timestamp) + "/" + urllib.parse.quote(key, safe=":"))
    quote = coins.get("coins", {}).get(key)
    if not quote or not isinstance(quote.get("price"), (float, int)):
        return dict(base, status="no_historical_quote", timestamp=stamp,
                    raw_message=raw_message, raw_local=raw_local)
    quote_ts = int(quote.get("timestamp") or 0)
    if abs(quote_ts - timestamp) > 86400:
        return dict(base, status="quote_too_distant", timestamp=stamp,
                    quote_timestamp=quote_ts)
    original_interpretation_usd = raw_message / 10**decimals * quote["price"]
    return dict(base, status="recovered", timestamp=stamp,
                raw_message=raw_message, raw_local=raw_local, decimals=decimals,
                price_usd=quote["price"], quote_timestamp=quote_ts,
                local_amount=raw_local / 10**decimals,
                usd=raw_local / 10**decimals * quote["price"],
                raw_as_local_usd=original_interpretation_usd,
                crosses_original_50m_filter=original_interpretation_usd > 50e6)


def main() -> None:
    audit = json.loads((DATA / "a2_missing_price_audit.json").read_text())
    routes = {r["route"]: r for r in audit["routes"] if r["chain"] == "ethereum"
              and r["later_quote_observation"] == "local_collateral_later_quoted"}
    metadata = {row["router"].lower().removeprefix("0x"): row
                for row in json.loads((DATA / "hl_ism_ethereum.json").read_text())}
    priced = Counter((e["route"].lower().removeprefix("0x"), e["tx"].lower())
                     for e in json.loads((DATA / "chainlog_priced_ethereum.json").read_text())["events"])
    raw = json.loads((DATA / "chainlog_events.json").read_text())["events"]
    remaining = Counter((e["route"].lower().removeprefix("0x"), e["tx"].lower())
                        for e in raw if e["route"].lower().removeprefix("0x") in routes)
    # If multiple deliveries share a transaction, one receipt cannot safely
    # be attached to one Process record by route/tx alone; leave it unknown.
    candidates = [e for e in raw if e["route"].lower().removeprefix("0x") in routes
                  and remaining[(e["route"].lower().removeprefix("0x"), e["tx"].lower())] == 1
                  and priced[(e["route"].lower().removeprefix("0x"), e["tx"].lower())] == 0]
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(single_event, e, metadata[e["route"].lower().removeprefix("0x")]): e
                for e in candidates}
        for job in as_completed(jobs):
            try:
                results.append(job.result())
            except Exception as exc:
                e = jobs[job]
                results.append({"route": e["route"], "tx": e["tx"], "status": "retrieval_error",
                                "detail": type(exc).__name__})
    expected = sum(r["omitted"] for r in routes.values())
    statuses = Counter(r["status"] for r in results)
    out = {"scope": "Ethereum omissions with a local collateral address and later quote",
           "historical_source": "DefiLlama historical price at block timestamp",
           "amount_source": "ERC-20 Transfer from destination collateral router in same receipt",
           "expected_omissions": expected, "unique_tx_candidates": len(candidates),
           "statuses": dict(statuses),
           "recovered_usd": sum(r.get("usd", 0) for r in results),
           "crosses_original_50m_filter_at_historical_quote": sum(
               bool(r.get("crosses_original_50m_filter")) for r in results),
           "events": sorted(results, key=lambda r: (r["route"], r["tx"]))}
    path = DATA / "a2_historical_recovery.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("expected_omissions", "unique_tx_candidates",
                                         "statuses", "recovered_usd",
                                         "crosses_original_50m_filter_at_historical_quote")}, indent=2))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
