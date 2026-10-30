#!/usr/bin/env python3
"""Reconstruct Ethereum delivery amounts and daily historical price references.

Public responses are cached by URL for offline replay and interrupted-run
recovery. Process records, router receive events and actual local transfers
are joined by transaction, route and recipient. Ambiguities remain unknown.
Synthetic tokens use their contract address as a price key; symbols are never
used to infer another asset. Historical daily quotes are accepted only within
24 hours of the delivery. The original paper trace is never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" if (HERE / "data").is_dir() else HERE.parent / "data"
CACHE = DATA / "a2_http_cache"
API = "https://eth.blockscout.com/api/v2/"
PRICE = "https://coins.llama.fi/prices/historical/"
RECEIVED = "0xba20947a325f450d232530e5f5fce293e7963499d5309a07cee84a269f2f15a6"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO = "0" * 40
_lock = threading.Lock()
_next = 0.


def norm(value):
    if isinstance(value, dict):
        value = value.get("hash") or value.get("address_hash") or ""
    return str(value or "").lower().removeprefix("0x")


def get(url, offline=False):
    global _next
    target = CACHE / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    if target.exists():
        stored = json.loads(target.read_text())
        assert stored["url"] == url
        return stored["response"]
    if offline:
        raise RuntimeError("uncached public response")
    for attempt in range(4):
        with _lock:
            wait = max(0., _next - time.monotonic())
            _next = max(_next, time.monotonic()) + .42
        time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ExposureGuard-revision-audit/1.0"})
            with urllib.request.urlopen(req, timeout=25) as response:
                result = json.load(response)
            CACHE.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(f".{threading.get_ident()}.tmp")
            temporary.write_text(json.dumps({"url": url, "response": result}, separators=(",", ":")))
            temporary.replace(target)
            return result
        except (urllib.error.URLError, TimeoutError, ValueError):
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")


def pages(path, params, minimum, offline):
    rows, seen = [], set()
    while True:
        url = API + path + "?" + urllib.parse.urlencode(sorted(params.items()))
        if url in seen:
            raise RuntimeError("pagination repeated")
        seen.add(url)
        response = get(url, offline)
        batch = response.get("items")
        if not isinstance(batch, list):
            raise RuntimeError("invalid list response")
        rows.extend(r for r in batch if int(r.get("block_number") or 0) >= minimum)
        cursor = response.get("next_page_params")
        if not batch or not cursor or min(int(r.get("block_number") or 0) for r in batch) < minimum:
            break
        # Follow the server cursor exactly. ERC-20 rows are filtered again
        # locally because some explorer cursors omit the initial type filter.
        params = cursor
    return rows


def received_for_route(route, rows, offline):
    low, high = min(e["block"] for e in rows), max(e["block"] for e in rows)
    logs = pages(f"addresses/0x{route}/logs", {"topic": RECEIVED,
                 "block_number": high + 1, "index": 2147483647, "items_count": 50}, low, offline)
    return [log for log in logs if log.get("topics", [None])[0] == RECEIVED]


def token_transfers_for_route(route, rows, offline):
    low, high = min(e["block"] for e in rows), max(e["block"] for e in rows)
    return pages(f"addresses/0x{route}/token-transfers", {"type": "ERC-20",
                 "block_number": high + 1, "index": 2147483647}, low, offline)


def match_erc20(receive, transfers, route):
    recipient = norm(receive["topics"][2])[-40:]
    tx = receive["transaction_hash"].lower()
    candidates = [t for t in transfers if t.get("transaction_hash", "").lower() == tx
                  and t.get("token_type", "ERC-20") == "ERC-20"
                  and norm(t.get("from")) == route and norm(t.get("to")) == recipient]
    if len(candidates) != 1:
        return {"amount_status": "ambiguous_or_missing_local_transfer", "transfer_matches": len(candidates)}
    transfer = candidates[0]
    decimals = transfer.get("total", {}).get("decimals")
    raw = transfer.get("total", {}).get("value")
    token = transfer.get("token", {}).get("address_hash")
    if decimals is None or raw is None or not token:
        return {"amount_status": "missing_transfer_units"}
    return {"amount_status": "established", "raw_local": str(raw), "decimals": int(decimals),
            "price_key": "ethereum:0x" + norm(token),
            "amount_source_id": tx + ":log:" + str(transfer["log_index"]),
            "amount_source": "unique outgoing ERC-20 transfer to receive-event recipient in same transaction"}


def receipt_collateral_amount(receive, route, metadata, offline):
    url = API + "transactions/" + receive["transaction_hash"] + "/logs"
    response = get(url, offline)
    logs = list(response.get("items", []))
    while response.get("next_page_params"):
        response = get(url + "?" + urllib.parse.urlencode(sorted(response["next_page_params"].items())), offline)
        logs.extend(response.get("items", []))
    recipient = norm(receive["topics"][2])[-40:]
    token = norm(metadata["collateral"])
    candidates = [log for log in logs if log.get("topics", [None])[0] == TRANSFER
                  and norm(log["topics"][2])[-40:] == recipient
                  and (norm(log.get("address")) == token or norm(log["topics"][1])[-40:] == token)]
    if len(candidates) != 1:
        return {"amount_status": "unresolved_registered_collateral_transfer", "transfer_matches": len(candidates)}
    log = candidates[0]
    actual_token = norm(log.get("address"))
    units = get(API + "tokens/0x" + actual_token, offline)
    if units.get("decimals") is None:
        return {"amount_status": "missing_transfer_units"}
    return {"amount_status": "established", "raw_local": str(int(log["data"], 16)),
            "decimals": int(units["decimals"]), "price_key": "ethereum:0x" + actual_token,
            "amount_source_id": receive["transaction_hash"].lower() + ":log:" + str(log["index"]),
            "amount_source": "unique receipt transfer of registered collateral, or underlying paid by registered vault, to receive recipient; token metadata decimals"}


def native_amount(receive, route, offline):
    trace = get(API + "transactions/" + receive["transaction_hash"] + "/raw-trace", offline)
    if not isinstance(trace, list):
        return {"amount_status": "missing_native_trace"}
    recipient = norm(receive["topics"][2])[-40:]
    failed = [tuple(r.get("traceAddress", [])) for r in trace if r.get("error")]
    matches = []
    for row in trace:
        action = row.get("action", {})
        address = tuple(row.get("traceAddress", []))
        if row.get("error") or any(address[:len(parent)] == parent for parent in failed):
            continue
        if (norm(action.get("from")) == route and norm(action.get("to")) == recipient
                and int(action.get("value", "0x0"), 16) > 0
                and action.get("callType") == "call"):
            matches.append((int(action["value"], 16), address))
    if len(matches) != 1:
        return {"amount_status": "ambiguous_or_missing_native_transfer", "transfer_matches": len(matches)}
    return {"amount_status": "established", "raw_local": str(matches[0][0]), "decimals": 18,
            "amount_source_id": receive["transaction_hash"].lower() + ":trace:" + str(matches[0][1]),
            "price_key": "coingecko:ethereum", "amount_source": "unique successful native transfer in execution trace"}


def synthetic_amount(receive, route, metadata, offline):
    response = get(API + "transactions/" + receive["transaction_hash"] + "/logs", offline)
    if response.get("next_page_params"):
        return {"amount_status": "paginated_receipt_not_resolved"}
    recipient = norm(receive["topics"][2])[-40:]
    matches = [log for log in response.get("items", []) if norm(log.get("address")) == route
               and log.get("topics", [None])[0] == TRANSFER
               and norm(log["topics"][1])[-40:] == ZERO
               and norm(log["topics"][2])[-40:] == recipient]
    if len(matches) != 1 or metadata.get("decimals") is None:
        return {"amount_status": "ambiguous_or_missing_mint", "transfer_matches": len(matches)}
    return {"amount_status": "established", "raw_local": str(int(matches[0]["data"], 16)),
            "decimals": int(metadata["decimals"]), "price_key": "ethereum:0x" + route,
            "amount_source_id": receive["transaction_hash"].lower() + ":log:" + str(matches[0]["index"]),
            "amount_source": "unique local mint to receive-event recipient; frozen registry decimals"}


def stamp(log):
    return int(datetime.fromisoformat(log["block_timestamp"].replace("Z", "+00:00")).timestamp())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--scope", choices=("all", "missing"), default="all")
    args = parser.parse_args()
    raw = json.loads((DATA / "chainlog_events.json").read_text())["events"]
    old = json.loads((DATA / "chainlog_priced_ethereum.json").read_text())["events"]
    metadata = {norm(r["router"]): r for r in json.loads((DATA / "hl_ism_ethereum.json").read_text())}
    old_counts = Counter((norm(e["route"]), e["tx"].lower()) for e in old)
    by_route = defaultdict(list)
    for e in raw:
        item = dict(e, route=norm(e["route"]))
        key = item["route"], e["tx"].lower()
        item["originally_priced"] = old_counts[key] > 0
        old_counts[key] -= int(item["originally_priced"])
        if args.scope == "all" or not item["originally_priced"]:
            by_route[item["route"]].append(item)
    observations, failures = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(received_for_route, route, events, args.offline): route for route, events in by_route.items()}
        logs_by_route = {}
        for job in as_completed(jobs):
            route = jobs[job]
            try:
                logs_by_route[route] = job.result()
            except Exception as exc:
                failures.append({"route": route, "stage": "receive_logs", "error": type(exc).__name__})
            print("receive routes", len(logs_by_route), "/", len(by_route), flush=True)
    # Collect actual local transfers for collateral routes in bulk, avoiding
    # assumptions about raw message units or the event order across versions.
    token_by_route = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(token_transfers_for_route, route, events, args.offline): route
                for route, events in by_route.items() if metadata[route].get("collateral")}
        for job in as_completed(jobs):
            route = jobs[job]
            try:
                token_by_route[route] = job.result()
            except Exception as exc:
                failures.append({"route": route, "stage": "token_transfers", "error": type(exc).__name__})
            print("collateral routes", len(token_by_route), "/", len(jobs), flush=True)
    extra, receipt_checks = [], []
    cohort_flags = defaultdict(list)
    for events in by_route.values():
        for e in events:
            cohort_flags[(e["route"], e["tx"].lower(), e["origin"])].append(e["originally_priced"])
    consumed_receives = Counter()
    for route, events in sorted(by_route.items()):
        md = metadata[route]
        grouped = defaultdict(list)
        for log in logs_by_route.get(route, []):
            grouped[log.get("transaction_hash", "").lower()].append(log)
        for e in events:
            row = dict(e, symbol=md.get("symbol"), family=md.get("family"), standard=md.get("standard"))
            candidates = [log for log in grouped[e["tx"].lower()] if log["block_number"] == e["block"]
                          and int(log["topics"][1], 16) == e["origin"]]
            group = (route, e["tx"].lower(), e["origin"])
            flags = cohort_flags[group]
            homogeneous_batch = (len(set(flags)) == 1 and len(candidates) == len(flags))
            if not homogeneous_batch:
                row.update(amount_status="ambiguous_or_missing_receive", receive_matches=len(candidates))
            else:
                candidates.sort(key=lambda log: log["index"])
                index = consumed_receives[group]
                consumed_receives[group] += 1
                if index >= len(candidates):
                    row.update(amount_status="ambiguous_or_missing_receive", receive_matches=len(candidates))
                    observations.append(row)
                    continue
                receive = candidates[index]
                row.update(ts=stamp(receive), t=datetime.fromtimestamp(stamp(receive), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                           raw_message=str(int(receive["data"], 16)), receive_log_index=receive["index"],
                           process_matching="same route/tx/origin count; homogeneous original cohort" if len(candidates) > 1 else "unique receive")
                if md.get("collateral"):
                    row.update(match_erc20(receive, token_by_route.get(route, []), route))
                    if row["amount_status"] != "established":
                        receipt_checks.append((row, receive))
                elif "Native" in str(md.get("standard")):
                    extra.append((row, receive, "native"))
                else:
                    row.update(amount_status="synthetic_quote_pending", price_key="ethereum:0x" + route)
                    extra.append((row, receive, "synthetic"))
            observations.append(row)
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(receipt_collateral_amount, receive, row["route"], metadata[row["route"]], args.offline): row
                for row, receive in receipt_checks}
        for number, job in enumerate(as_completed(jobs), 1):
            row = jobs[job]
            try:
                row.update(job.result())
            except Exception as exc:
                row.update(amount_status="amount_retrieval_error", retrieval_error=type(exc).__name__)
            if number % 50 == 0 or number == len(jobs):
                print("registered-collateral receipt checks", number, "/", len(jobs), flush=True)
    # Price queries use daily historical references. They never read current
    # prices or the explorer's current exchange_rate field.
    keys_by_day = defaultdict(set)
    for row in observations:
        if row.get("ts") and row.get("price_key"):
            keys_by_day[int(row["ts"] // 86400)].add(row["price_key"])
    for row, receive, kind in extra:
        if kind == "native":
            keys_by_day[int(row["ts"] // 86400)].add("coingecko:ethereum")
    quotes = {}
    def daily(day, keys):
        url = PRICE + str(day * 86400 + 43200) + "/" + urllib.parse.quote(",".join(sorted(keys)), safe=":,")
        return day, get(url, args.offline).get("coins", {})
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(daily, day, keys): day for day, keys in keys_by_day.items()}
        for job in as_completed(jobs):
            try:
                day, data = job.result()
                quotes[day] = data
            except Exception as exc:
                failures.append({"day": jobs[job], "stage": "historical_quotes", "error": type(exc).__name__})
        print("historical reference days", len(quotes), "/", len(keys_by_day), flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {}
        for row, receive, kind in extra:
            if kind == "native":
                jobs[pool.submit(native_amount, receive, row["route"], args.offline)] = row
            elif row["price_key"] in quotes.get(int(row["ts"] // 86400), {}):
                jobs[pool.submit(synthetic_amount, receive, row["route"], metadata[row["route"]], args.offline)] = row
            else:
                row["amount_status"] = ("synthetic_historical_quote_unavailable" if int(row["ts"] // 86400) in quotes
                                        else "historical_quote_retrieval_error")
        for number, job in enumerate(as_completed(jobs), 1):
            row = jobs[job]
            try:
                row.update(job.result())
            except Exception as exc:
                row.update(amount_status="amount_retrieval_error", retrieval_error=type(exc).__name__)
            if number % 25 == 0 or number == len(jobs):
                print("execution amount checks", number, "/", len(jobs), flush=True)
    source_counts = Counter(row["amount_source_id"] for row in observations if row.get("amount_source_id"))
    for row in observations:
        if row.get("amount_source_id") and source_counts[row["amount_source_id"]] > 1:
            row["amount_status"] = "nonunique_local_transfer_assignment"
        if row["amount_status"] != "established":
            row["status"] = row["amount_status"]
            continue
        quote = quotes.get(int(row["ts"] // 86400), {}).get(row["price_key"])
        if int(row["ts"] // 86400) not in quotes:
            row["status"] = "historical_quote_retrieval_error"
        elif not quote or not isinstance(quote.get("price"), (int, float)) or quote["price"] <= 0:
            row["status"] = "historical_quote_unavailable"
        elif abs(int(quote.get("timestamp", 0)) - row["ts"]) > 86400:
            row["status"] = "historical_quote_too_distant"
        else:
            row.update(status="recovered", price_usd=quote["price"], quote_timestamp=quote["timestamp"],
                       usd=int(row["raw_local"]) / 10**row["decimals"] * quote["price"])
            assert math.isfinite(row["usd"]) and row["usd"] >= 0
    observations.sort(key=lambda e: (e.get("ts", 0), e["block"], e["route"], e["tx"]))
    groups = {}
    for label, rows in (("all", observations), ("originally_priced", [r for r in observations if r["originally_priced"]]),
                        ("originally_omitted", [r for r in observations if not r["originally_priced"]])):
        groups[label] = {"messages": len(rows), "statuses": dict(Counter(r["status"] for r in rows)),
                         "recovered_messages": sum(r["status"] == "recovered" for r in rows),
                         "recovered_usd": sum(r.get("usd", 0) for r in rows)}
    artifact = {"schema": 1, "scope": args.scope, "chain": "ethereum", "original_deliveries": len(raw),
                "original_priced": len(old), "historical_pricing": "daily-noon UTC reference; quote timestamp within 24h of event",
                "amount_matching": "route/transaction/origin Process-receive count match; batched records require homogeneous original-cohort membership; unique actual local transfer assignment; no event-order assumption",
                "limits": "public-explorer reconstruction; unknowns retained; synthetic price key is local token contract only; transfer decimals from explorer token metadata or frozen registry",
                "input_sha256": {p: hashlib.sha256((DATA / p).read_bytes()).hexdigest() for p in
                                 ("chainlog_events.json", "chainlog_priced_ethereum.json", "hl_ism_ethereum.json")},
                "groups": groups, "retrieval_failures": sorted(failures, key=lambda r: json.dumps(r, sort_keys=True)),
                "events": observations}
    path = DATA / ("a2_ethereum_event_audit.json" if args.scope == "all" else "a2_ethereum_missing_audit.json")
    path.write_text(json.dumps(artifact, indent=2) + "\n")
    print(json.dumps(groups, indent=2), flush=True)
    print("wrote", path)


if __name__ == "__main__":
    main()
