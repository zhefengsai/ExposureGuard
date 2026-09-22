#!/usr/bin/env python3
"""Chain-log cross-validation of Hyperlane indexer flow (Ethereum first).

Stage 1: Mailbox Process event counts vs indexer events, per default-ISM route.
Stage 2 (best-effort): ReceivedTransferRemote amounts → independent USD peak.

Discipline: RPC failures raise or go into unresolved[]; never silent skip.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
ZERO = "0x" + "0" * 40

# Candidates from hyperlane-registry (must pass verify_mailbox before use).
MAILBOX_CANDIDATES = {
    "ethereum": "0xc005dc82818d67AF737725bD4bf75435d065D239",
    "base": "0xeA87ae93Fa0019a82A727bfd3eBd1cFCa8f64f1D",
    "arbitrum": "0x979Ca5202784112f4738403dBec5D0F3B9daabB9",
    "bsc": "0x2971b9Aec44bE4eb673DF1B88cDB57b96eefe8a4",
}
HYPERLANE_DOMAIN = {
    "ethereum": 1,
    "base": 8453,
    "arbitrum": 42161,
    "bsc": 56,
}
DEFAULT_RPC = {
    "ethereum": "https://ethereum.public.blockpi.network/v1/rpc/public",
    "base": "https://mainnet.base.org",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "bsc": "https://bsc.rpc.blxrbdn.com",
}
LLAMA = {"ethereum": "ethereum", "base": "base", "arbitrum": "arbitrum", "bsc": "bsc"}

# Discovered on-chain (see discover_process_topic); also recorded in JSON output.
PROCESS_SIG = "Process(uint32,bytes32,address)"
RECEIVED_SIG = "ReceivedTransferRemote(uint32,bytes32,uint256)"

_TS_CACHE: dict[int, int] = {}


class RpcError(Exception):
    pass


def rpc(url: str, method: str, params: list, attempts: int | None = None) -> Any:
    if attempts is None:
        attempts = int(os.environ.get("EXPOSUREGUARD_RPC_ATTEMPTS", "6"))
    timeout = float(os.environ.get("EXPOSUREGUARD_RPC_TIMEOUT_SECONDS", "90"))
    last = None
    for i in range(attempts):
        req = urllib.request.Request(
            url,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "exposureguard-chainlog/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read())
            if "error" in body:
                last = body["error"]
            else:
                return body["result"]
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(0.4 * (i + 1))
    raise RpcError(f"{method} failed: {last}")


def keccak_topic(sig: str) -> str:
    """Use cast if available; else require precomputed."""
    import subprocess
    out = subprocess.check_output([cast_bin(), "keccak", sig], text=True).strip()
    return out if out.startswith("0x") else "0x" + out


def block_by_number(url: str, n: int) -> dict:
    for attempt in range(5):
        b = rpc(url, "eth_getBlockByNumber", [hex(n), False])
        if b and b.get("timestamp"):
            return b
        time.sleep(0.3 * (attempt + 1))
    raise RpcError(f"eth_getBlockByNumber returned null for {n}")


def block_ts(url: str, n: int) -> int:
    if n in _TS_CACHE:
        return _TS_CACHE[n]
    b = block_by_number(url, n)
    ts = int(b["timestamp"], 16)
    _TS_CACHE[n] = ts
    return ts


def cast_bin() -> str:
    cast = os.path.expanduser("~/.foundry/bin/cast")
    return cast if os.path.isfile(cast) else "cast"


def verify_mailbox(url: str, chain: str, addr: str) -> dict:
    """Three checks: code non-empty, localDomain==expected, defaultIsm!=0."""
    expect = HYPERLANE_DOMAIN[chain]
    checks: dict[str, Any] = {
        "address": addr,
        "expected_domain": expect,
        "source": "hyperlane-registry chains/<chain>/addresses.yaml mailbox",
    }
    try:
        code = rpc(url, "eth_getCode", [addr, "latest"])
    except RpcError as e:
        code = str(e)[:200]
    checks["code_len"] = len(code) if code.startswith("0x") else 0
    checks["code_ok"] = checks["code_len"] > 2

    def _call(sig: str) -> str:
        try:
            # ``cast call`` accepts a return annotation (``f()(uint32)``),
            # whereas the EVM selector hashes only the input signature.
            selector = keccak_topic(sig.split(")", 1)[0] + ")")[:10]
            return rpc(url, "eth_call", [{"to": addr, "data": selector}, "latest"])
        except (RpcError, OSError) as e:
            return str(e)[:300]

    raw_dom = _call("localDomain()(uint32)")
    raw_ism = _call("defaultIsm()(address)")
    checks["localDomain_raw"] = raw_dom[:120]
    checks["defaultIsm_raw"] = raw_ism[:120]
    try:
        dom = int(raw_dom, 16)
    except Exception:
        dom = None
    try:
        ism = "0x" + raw_ism[-40:] if raw_ism.startswith("0x") else None
    except Exception:
        ism = None
    checks["localDomain"] = dom
    checks["defaultIsm"] = ism
    checks["domain_ok"] = dom == expect
    checks["ism_ok"] = (
        isinstance(ism, str)
        and ism.lower().startswith("0x")
        and len(ism) == 42
        and ism.lower() != ZERO
    )
    checks["ok"] = bool(checks["code_ok"] and checks["domain_ok"] and checks["ism_ok"])
    return checks


def find_block_ge(url: str, target_ts: int, lo: int, hi: int) -> int:
    """Smallest block with timestamp >= target_ts."""
    while lo < hi:
        mid = (lo + hi) // 2
        ts = block_ts(url, mid)
        if ts < target_ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def parse_iso(s: str) -> int:
    # events use '2026-04-24T16:57:31' without Z
    if s.endswith("Z"):
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def discover_process_topic(url: str, mailbox: str) -> dict:
    """Pull a recent Mailbox log and confirm topic0 == keccak(Process(...))."""
    expected = keccak_topic(PROCESS_SIG)
    bn = int(rpc(url, "eth_blockNumber", []), 16)
    logs = None
    span = 500
    max_span = 500_000
    last_err = None
    while span >= 50:
        try:
            logs = rpc(url, "eth_getLogs", [{
                "address": mailbox,
                "fromBlock": hex(bn - span),
                "toBlock": hex(bn),
                "topics": [expected],
            }])
            if logs:
                break
            if span >= max_span:
                break
            span = min(max_span, span * 2)
        except RpcError as e:
            last_err = e
            span //= 2
    if not logs:
        raise RpcError(
            f"discover Process failed: {last_err or 'no logs in window'}")
    got = logs[0]["topics"][0].lower()
    if got != expected.lower():
        raise RpcError(f"topic mismatch: on-chain {got} != keccak {expected}")
    recip = "0x" + logs[0]["topics"][3][-40:]
    return {
        "signature": PROCESS_SIG,
        "topic0": expected,
        "confirmed_via": f"eth_getLogs Mailbox last {span} blocks, n={len(logs)}",
        "sample_tx": logs[0]["transactionHash"],
        "sample_recipient": recip,
        "sample_block": int(logs[0]["blockNumber"], 16),
    }


def _compact_log(L: dict) -> dict:
    """Keep only fields stage-1 / stage-2 need (cuts checkpoint size)."""
    return {
        "address": L.get("address"),
        "blockNumber": L["blockNumber"],
        "transactionHash": L["transactionHash"],
        "topics": L["topics"],
        "data": L.get("data", "0x"),
    }


def stage1_ckpt_paths(chain: str) -> tuple[pathlib.Path, pathlib.Path]:
    return (
        DATA / f"chainlog_stage1_{chain}.ckpt.json",
        DATA / f"chainlog_stage1_{chain}.logs.json",
    )


def load_stage1_checkpoint(
    chain: str,
    mailbox: str,
    topic0: str,
    start: int,
    end: int,
) -> dict | None:
    meta_path, logs_path = stage1_ckpt_paths(chain)
    if not meta_path.exists() or not logs_path.exists():
        return None
    meta = json.loads(meta_path.read_text())
    if (meta.get("chain") != chain
            or str(meta.get("mailbox", "")).lower() != mailbox.lower()
            or str(meta.get("topic0", "")).lower() != topic0.lower()
            or int(meta.get("start_block", -1)) != start
            or int(meta.get("end_block", -1)) != end):
        print(f"  stage1 ckpt exists but window/mailbox mismatch — ignoring "
              f"{meta_path.name}", flush=True)
        return None
    logs = json.loads(logs_path.read_text())
    print(f"  stage1 resume: next_block={meta['next_block']}  "
          f"logs={len(logs)}  unresolved={len(meta.get('unresolved') or [])}",
          flush=True)
    return {
        "next_block": int(meta["next_block"]),
        "logs": logs,
        "unresolved": list(meta.get("unresolved") or []),
        "blocks_ok": int(meta.get("blocks_ok") or 0),
    }


def save_stage1_checkpoint(
    chain: str,
    mailbox: str,
    topic0: str,
    start: int,
    end: int,
    next_block: int,
    logs: list[dict],
    unresolved: list[dict],
    blocks_ok: int,
) -> None:
    meta_path, logs_path = stage1_ckpt_paths(chain)
    tmp_logs = logs_path.with_suffix(".json.tmp")
    tmp_meta = meta_path.with_suffix(".json.tmp")
    tmp_logs.write_text(json.dumps(logs) + "\n")
    tmp_meta.write_text(json.dumps({
        "chain": chain,
        "mailbox": mailbox,
        "topic0": topic0,
        "start_block": start,
        "end_block": end,
        "next_block": next_block,
        "blocks_ok": blocks_ok,
        "n_logs": len(logs),
        "unresolved": unresolved,
        "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=2) + "\n")
    tmp_logs.replace(logs_path)
    tmp_meta.replace(meta_path)


def clear_stage1_checkpoint(chain: str) -> None:
    for p in stage1_ckpt_paths(chain):
        if p.exists():
            p.unlink()


def get_logs_sharded(
    url: str,
    address: "str | list[str]",
    topic0: str,
    start: int,
    end: int,
    shard: int = 2000,
    extra_topics: list | None = None,
    *,
    checkpoint_chain: str | None = None,
    checkpoint_mailbox: str | None = None,
    resume: bool = True,
) -> tuple[list[dict], list[dict], int, int]:
    """Return (logs, unresolved_ranges, blocks_ok, blocks_total).

    `address` may be a single address or a list. eth_getLogs accepts an array
    (JSON-RPC: DATA|Array), so passing every route at once replaces N full-window
    scans with one. On Arbitrum that is the difference between ~615k calls and
    ~20k. Callers must group the returned logs by log["address"] themselves.

    When ``checkpoint_chain`` is set, progress is written under
    ``data/chainlog_stage1_<chain>.{ckpt,logs}.json`` every ~50k blocks so a
    killed run can resume from ``next_block`` instead of rescanning the window.
    """
    logs: list[dict] = []
    unresolved: list[dict] = []
    blocks_ok = 0
    blocks_total = max(0, end - start + 1)
    cur = start
    initial_shard = max(1, shard)
    shard = initial_shard
    last_ckpt_at = start

    if (resume and checkpoint_chain and checkpoint_mailbox
            and isinstance(address, str)):
        ckpt = load_stage1_checkpoint(
            checkpoint_chain, checkpoint_mailbox, topic0, start, end)
        if ckpt:
            logs = ckpt["logs"]
            unresolved = ckpt["unresolved"]
            blocks_ok = ckpt["blocks_ok"]
            cur = max(start, ckpt["next_block"])
            last_ckpt_at = cur

    while cur <= end:
        width = min(shard, end - cur + 1)
        ok = False
        w = width
        while w >= 1:
            fr, to = cur, cur + w - 1
            topics = [topic0] + (extra_topics or [])
            try:
                chunk = rpc(url, "eth_getLogs", [{
                    "address": address,
                    "fromBlock": hex(fr),
                    "toBlock": hex(to),
                    "topics": topics,
                }])
                logs.extend(_compact_log(L) for L in chunk)
                blocks_ok += (to - fr + 1)
                cur = to + 1
                ok = True
                # adaptive: grow back toward the caller's initial shard
                if w == width and shard < initial_shard:
                    shard = min(initial_shard, max(shard * 2, w))
                break
            except RpcError as e:
                err = str(e)
                if w == 1:
                    # Public archive RPCs sometimes rate-limit an isolated
                    # block after a large scan. Give that block a cooled-down
                    # retry before recording a real coverage hole.
                    time.sleep(2.0)
                    try:
                        chunk = rpc(url, "eth_getLogs", [{
                            "address": address,
                            "fromBlock": hex(fr),
                            "toBlock": hex(to),
                            "topics": topics,
                        }], attempts=6)
                        logs.extend(_compact_log(L) for L in chunk)
                        blocks_ok += 1
                    except RpcError as retry_error:
                        fallback = os.environ.get("EXPOSUREGUARD_FALLBACK_RPC_URL")
                        if fallback and fallback != url:
                            try:
                                chunk = rpc(fallback, "eth_getLogs", [{
                                    "address": address,
                                    "fromBlock": hex(fr),
                                    "toBlock": hex(to),
                                    "topics": topics,
                                }], attempts=3)
                                logs.extend(_compact_log(L) for L in chunk)
                                blocks_ok += 1
                            except RpcError as fallback_error:
                                unresolved.append({
                                    "from": fr, "to": to,
                                    "error": str(fallback_error)[:200],
                                    "primary_error": str(retry_error)[:200],
                                    "initial_error": err[:200],
                                })
                        else:
                            unresolved.append({
                                "from": fr, "to": to,
                                "error": str(retry_error)[:200],
                                "initial_error": err[:200],
                            })
                    cur = to + 1
                    ok = True  # advance past the failed single block
                    break
                w = max(1, w // 2)
                shard = w
                time.sleep(0.2)
        if not ok:
            unresolved.append({"from": cur, "to": cur, "error": "internal"})
            cur += 1
        progressed = cur - start
        if progressed % 50000 < shard or cur > end:
            print(f"  … block {cur}/{end}  logs={len(logs)}  unresolved={len(unresolved)}",
                  flush=True)
            if (checkpoint_chain and checkpoint_mailbox
                    and isinstance(address, str)
                    and (cur - last_ckpt_at >= 50_000 or cur > end)):
                save_stage1_checkpoint(
                    checkpoint_chain, checkpoint_mailbox, topic0,
                    start, end, cur, logs, unresolved, blocks_ok)
                last_ckpt_at = cur
                print(f"  … checkpoint saved next_block={cur}", flush=True)
    return logs, unresolved, blocks_ok, blocks_total


def recipient_from_process(log: dict) -> str:
    return "0x" + log["topics"][3][-40:].lower()


def prices_for_routes(chain: str, routes: list[dict]) -> dict[str, float]:
    """Same DefiLlama path as flow_multi.py."""
    addrs = sorted({
        r["collateral"].lower() for r in routes
        if r.get("collateral") and str(r["collateral"]).startswith("0x")
    })
    out: dict[str, float] = {}
    pfx = LLAMA[chain]
    for i in range(0, len(addrs), 40):
        chunk = addrs[i:i + 40]
        q = ",".join(f"{pfx}:" + a for a in chunk)
        last = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(
                        "https://coins.llama.fi/prices/current/" + q, timeout=40) as r:
                    for k, v in json.loads(r.read())["coins"].items():
                        out[k.split(":")[1].lower()] = v["price"]
                break
            except Exception as e:
                last = e
                time.sleep(0.5 * (attempt + 1))
        else:
            print(f"  [warn] price chunk failed: {last}", file=sys.stderr)
    return out


def negative_control(url: str, mailbox: str, topic0: str, sample_logs: list[dict]) -> dict:
    """Re-fetch 5 Process txs and confirm the log is present in the receipt."""
    picks = sample_logs[:5]
    results = []
    for L in picks:
        txh = L["transactionHash"]
        try:
            rcpt = rpc(url, "eth_getTransactionReceipt", [txh])
            if not rcpt:
                results.append({"tx": txh, "ok": False, "err": "null receipt"})
                continue
            found = False
            for lg in rcpt.get("logs") or []:
                if (lg.get("address", "").lower() == mailbox.lower()
                        and lg.get("topics") and lg["topics"][0].lower() == topic0.lower()
                        and lg["topics"][3].lower() == L["topics"][3].lower()):
                    found = True
                    break
            results.append({
                "tx": txh,
                "ok": found,
                "block": int(L["blockNumber"], 16),
                "recipient": recipient_from_process(L),
            })
        except RpcError as e:
            results.append({"tx": txh, "ok": False, "err": str(e)[:160]})
    return {
        "n": len(results),
        "passed": sum(1 for r in results if r.get("ok")),
        "results": results,
    }


def stage2_value(
    url: str,
    routes_by_addr: dict[str, dict],
    process_logs_by_route: dict[str, list],
    start_b: int,
    end_b: int,
    chain: str,
    summary_indexer: dict,
    top_n: int = 15,
    shard: int = 2000,
) -> dict:
    """Best-effort ReceivedTransferRemote reconstruction for routes with traffic."""
    topic = keccak_topic(RECEIVED_SIG)
    discovery = {
        "signature": RECEIVED_SIG,
        "topic0": topic,
        "confirmed_via": None,
    }
    # Confirm on the busiest route that has process logs
    ranked = sorted(process_logs_by_route.items(), key=lambda kv: -len(kv[1]))
    confirmed = False
    for addr, _ in ranked[:10]:
        meta = routes_by_addr.get(addr)
        if not meta:
            continue
        router = meta["router"]
        try:
            sample, unr, _, _ = get_logs_sharded(
                url, router, topic, max(start_b, end_b - 20000), end_b, shard=2000)
            if sample:
                discovery["confirmed_via"] = (
                    f"eth_getLogs {router} n={len(sample)} "
                    f"sample_tx={sample[0]['transactionHash']}"
                )
                discovery["sample_standard"] = meta.get("standard")
                confirmed = True
                break
        except RpcError as e:
            discovery["confirm_error"] = str(e)[:200]
    if not confirmed:
        return {
            "attempted": True,
            "ok": False,
            "reason": "could not confirm ReceivedTransferRemote on-chain",
            "discovery": discovery,
        }

    daily: dict[str, float] = defaultdict(float)
    route_usd: dict[str, float] = defaultdict(float)
    priced_events: list[dict] = []
    n_events = 0
    n_priced = 0
    unresolved_routes = []
    # top_n<=0 → all routes with Process traffic; else top-N by count.
    ranked = sorted(process_logs_by_route.items(), key=lambda kv: -len(kv[1]))
    with_traffic = [a for a, ls in ranked if ls]
    if top_n and top_n > 0:
        active = with_traffic[:top_n]
    else:
        active = with_traffic
    # Only active routes can contribute events in this window. Pricing every
    # configured route adds avoidable external-API latency to an hourly run.
    px = prices_for_routes(chain, [routes_by_addr[a] for a in active])
    uncovered = [a for a in with_traffic if a not in set(active)]
    print(f"  stage2: scanning ReceivedTransferRemote on {len(active)}/"
          f"{len(with_traffic)} routes with Process traffic"
          f"{'' if not uncovered else f' (uncovered={len(uncovered)})'}…",
          flush=True)
    # One batched scan over every active route, then group locally. Falls back
    # to per-route scans if the endpoint rejects an address array.
    def _norm(a: str) -> str:
        return str(a).lower().replace("0x", "", 1)

    router_of = {addr: routes_by_addr[addr]["router"] for addr in active}
    addr_of_router = {_norm(r): a for a, r in router_of.items()}
    logs_by_addr: dict[str, list] = {a: [] for a in active}
    batched = False
    if len(active) > 1:
        try:
            batch, unr, _, _ = get_logs_sharded(
                url, [router_of[a] for a in active], topic, start_b, end_b,
                shard=shard)
            for L in batch:
                a = addr_of_router.get(_norm(L.get("address", "")))
                if a is not None:
                    logs_by_addr[a].append(L)
            if unr:
                unresolved_routes.append({"route": "*batched*",
                                          "unresolved_shards": len(unr)})
            # Cross-check before trusting the batch. An endpoint that accepts
            # an address array but silently returns partial results would not
            # raise, and the fallback above would never fire. Rescan the busiest
            # route alone over a narrow window and require an exact match.
            probe_addr = active[0]
            lo = max(start_b, end_b - 50_000)
            probe, _, _, _ = get_logs_sharded(
                url, router_of[probe_addr], topic, lo, end_b, shard=shard)
            in_batch = [L for L in logs_by_addr[probe_addr]
                        if lo <= int(L["blockNumber"], 16) <= end_b]
            if len(probe) != len(in_batch):
                print(f"  stage2: batch cross-check FAILED on "
                      f"{router_of[probe_addr]} ({len(in_batch)} batched vs "
                      f"{len(probe)} direct) — falling back to per-route",
                      flush=True)
                logs_by_addr = {a_: [] for a_ in active}
            else:
                batched = True
                print(f"  stage2: batched scan returned {len(batch)} logs "
                      f"across {len(active)} routes "
                      f"(cross-check ok: {len(probe)} on busiest route)",
                      flush=True)
        except RpcError as e:
            print(f"  stage2: batched scan rejected ({str(e)[:80]}), "
                  f"falling back to per-route", flush=True)

    for i, addr in enumerate(active):
        meta = routes_by_addr[addr]
        router = meta["router"]
        if batched:
            logs = logs_by_addr[addr]
        else:
            try:
                logs, unr, _, _ = get_logs_sharded(
                    url, router, topic, start_b, end_b, shard=shard)
            except RpcError as e:
                unresolved_routes.append({"route": addr, "error": str(e)[:160]})
                continue
            if unr:
                unresolved_routes.append({"route": addr,
                                          "unresolved_shards": len(unr)})
        coll = str(meta.get("collateral") or "").lower()
        price = px.get(coll)
        dec = meta.get("decimals") if meta.get("decimals") is not None else 18
        sym = str(meta.get("symbol") or "UNK")
        fam = str(meta.get("family") or sym)
        for L in logs:
            n_events += 1
            if not L.get("data") or L["data"] == "0x":
                continue
            amt = int(L["data"], 16)
            if price is None:
                continue
            usd = amt / (10 ** dec) * price
            if usd > 50e6:
                continue
            n_priced += 1
            # timestamp via block (cache later if needed)
            try:
                ts = block_ts(url, int(L["blockNumber"], 16))
            except RpcError:
                continue
            day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            t_iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S")
            daily[day] += usd
            route_usd[addr] += usd
            priced_events.append({
                "t": t_iso,
                "route": addr,
                "family": fam,
                "symbol": sym,
                "usd": usd,
                "chain": chain,
                "tx": L.get("transactionHash"),
                "block": int(L["blockNumber"], 16),
                "provenance": "chain",
            })
        if (i + 1) % 5 == 0 or (i + 1) == len(active):
            print(f"    … {i+1}/{len(active)} routes, priced={n_priced}", flush=True)

    peak = max(daily.values()) if daily else 0.0
    mean = (sum(daily.values()) / len(daily)) if daily else 0.0
    idx_peak = float(summary_indexer.get("peak_daily") or 0)
    idx_mean = float(summary_indexer.get("mean_daily") or 0)
    return {
        "attempted": True,
        "ok": True,
        "discovery": discovery,
        "n_transfer_events": n_events,
        "n_priced": n_priced,
        "peak_daily": peak,
        "mean_daily": mean,
        "indexer_peak_daily": idx_peak,
        "indexer_mean_daily": idx_mean,
        "peak_ratio_chain_over_indexer": (peak / idx_peak) if idx_peak else None,
        "unresolved_routes": unresolved_routes[:20],
        "top_routes_usd": sorted(route_usd.items(), key=lambda x: -x[1])[:10],
        "priced_events": priced_events,
        "stage2_top_n": len(active),
        "routes_with_process": len(with_traffic),
        "routes_scanned": len(active),
        "routes_uncovered": uncovered,
        "coverage_routes": (len(active) / len(with_traffic)) if with_traffic else 1.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", default="ethereum")
    ap.add_argument("--skip-stage2", action="store_true")
    ap.add_argument("--stage2-only", action="store_true",
                    help="reuse prior chainlog_xval_<chain>.json window/per_route")
    ap.add_argument("--shard", type=int, default=2000,
                    help="initial eth_getLogs block span. Raise it when the "
                         "endpoint is slow per call rather than per byte: BSC "
                         "measured 11.2s/call, so shard=2000 costs ~20h where "
                         "shard=20000 costs ~2h. Adaptive halving still applies "
                         "on failure, so an over-large value self-corrects.")
    ap.add_argument("--no-resume", action="store_true",
                    help="ignore any stage-1 checkpoint and start from the "
                         "window start (also deletes an incompatible ckpt only "
                         "when a fresh successful run finishes)")
    ap.add_argument("--stage2-top", type=int, default=0,
                    help="top-N by Process count; 0 = all routes with traffic")
    ap.add_argument("--max-blocks", type=int, default=0,
                    help="debug: cap end-start (0 = full window)")
    ap.add_argument("--rpc", default="", help="override RPC URL")
    args = ap.parse_args()
    chain = args.chain
    if chain not in MAILBOX_CANDIDATES:
        sys.exit(f"unsupported chain {chain}")

    url = args.rpc or os.environ.get("ETH_RPC_URL") or DEFAULT_RPC[chain]
    print(f"RPC={url}", flush=True)

    ism = json.loads((DATA / f"hl_ism_{chain}.json").read_text())
    routes = [r for r in ism if str(r.get("ism", "")).lower() == ZERO]
    routes_by = {r["router"][2:].lower(): r for r in routes}
    route_set = set(routes_by)

    # Prefer indexer backup for window/truth baseline (current file may be merged).
    bak = DATA / f"events_{chain}.indexer.bak.json"
    ev_path = bak if bak.exists() else DATA / f"events_{chain}.json"
    events = json.loads(ev_path.read_text())
    # If reading current merged file without bak, strip to indexer-only for window
    if not bak.exists():
        events = [e for e in events if e.get("provenance") != "chain"] or events
    summary = json.loads((DATA / "flow_multi_summary.json").read_text())[chain]
    if not events:
        sys.exit("no indexer events — cannot determine window")
    # Pristine indexer peak/mean for stage-2 comparison (summary may already be merged).
    idx_daily: dict[str, float] = defaultdict(float)
    for e in events:
        idx_daily[str(e["t"])[:10]] += float(e["usd"])
    summary = dict(summary)
    if idx_daily:
        summary["peak_daily"] = max(idx_daily.values())
        summary["mean_daily"] = sum(idx_daily.values()) / len(idx_daily)
        summary["priced_events"] = len(events)

    candidate = MAILBOX_CANDIDATES[chain]
    print(f"verifying Mailbox {candidate} on {chain}…", flush=True)
    mb_check = verify_mailbox(url, chain, candidate)
    (DATA / f"mailbox_verify_{chain}.json").write_text(
        json.dumps(mb_check, indent=2) + "\n")
    if not mb_check["ok"]:
        sys.exit(f"Mailbox verification FAILED for {chain}: {json.dumps(mb_check)}")
    mailbox = candidate
    print(f"Mailbox OK domain={mb_check['localDomain']} ism={mb_check['defaultIsm']}",
          flush=True)

    xval_path = DATA / f"chainlog_xval_{chain}.json"
    # ethereum also keeps legacy name for apply/compat
    legacy_xval = DATA / "chainlog_xval.json"

    if args.stage2_only:
        prior_path = xval_path if xval_path.exists() else legacy_xval
        if not prior_path.exists():
            sys.exit(f"--stage2-only requires {prior_path}")
        prior = json.loads(prior_path.read_text())
        if prior.get("chain") != chain:
            sys.exit(f"--stage2-only prior chain={prior.get('chain')} != {chain}")
        t_min = prior["window"]["t_min"]
        t_max = prior["window"]["t_max"]
        start_b = int(prior["window"]["start_block"])
        end_b = int(prior["window"]["end_block"])
        disc = prior.get("process_discovery") or {"topic0": None, "confirmed_via": "reused"}
        per_route = prior["per_route"]
        miss = prior.get("indexer_miss_routes") or []
        neg = prior.get("negative_control") or {"n": 0, "passed": 0, "results": []}
        coverage = float((prior.get("coverage") or {}).get("fraction") or 1.0)
        blocks_ok = int((prior.get("coverage") or {}).get("blocks_ok") or 0)
        blocks_total = int((prior.get("coverage") or {}).get("blocks_total") or 0)
        unresolved = prior.get("unresolved") or []
        other = int((prior.get("totals") or {}).get("process_logs_other_recipients") or 0)
        chain_msgs = int((prior.get("totals") or {}).get("chain_msgs_default_routes") or 0)
        indexer_msgs = int((prior.get("totals") or {}).get("indexer_priced_msgs") or 0)
        by_route_logs = {
            r["route"]: [None] * int(r.get("chain_msgs") or 0)
            for r in per_route if int(r.get("chain_msgs") or 0) > 0
        }
        print(f"stage2-only: reusing window {t_min} → {t_max}  "
              f"blocks {start_b}→{end_b}", flush=True)
        ev_file = DATA / f"chainlog_events_{chain}.json"
        if not ev_file.exists():
            ev_file = DATA / "chainlog_events.json"
        chain_events = json.loads(ev_file.read_text()).get("events", []) if ev_file.exists() else []
    else:
        t_min = min(e["t"] for e in events)
        t_max = max(e["t"] for e in events)
        ts0, ts1 = parse_iso(t_min), parse_iso(t_max)
        print(f"window {t_min} → {t_max}  routes_default={len(routes)}", flush=True)

        disc = discover_process_topic(url, mailbox)
        print(f"Process topic0={disc['topic0']}  via {disc['confirmed_via']}", flush=True)

        head = int(rpc(url, "eth_blockNumber", []), 16)
        # Binary search: L2s have denser blocks — use larger lookback
        lookback = 80_000_000 if chain in ("arbitrum",) else 15_000_000
        lo_bound = max(1, head - lookback)
        print("binary-searching start/end blocks…", flush=True)
        start_b = find_block_ge(url, ts0, lo_bound, head)
        end_b = find_block_ge(url, ts1, start_b, head)
        end_b = min(head, end_b + 300)
        if args.max_blocks:
            end_b = min(end_b, start_b + args.max_blocks)
        print(f"blocks {start_b} → {end_b}  ({end_b - start_b + 1} blocks)", flush=True)

        print("stage1: fetching Mailbox Process logs…", flush=True)
        if args.no_resume:
            clear_stage1_checkpoint(chain)
            print("  --no-resume: cleared any prior stage-1 checkpoint", flush=True)
        logs, unresolved, blocks_ok, blocks_total = get_logs_sharded(
            url, mailbox, disc["topic0"], start_b, end_b, shard=args.shard,
            checkpoint_chain=chain, checkpoint_mailbox=mailbox,
            resume=not args.no_resume)
        print(f"  raw Process logs in window: {len(logs)}", flush=True)

        by_route_logs = defaultdict(list)
        other = 0
        for L in logs:
            recip = recipient_from_process(L)
            key = recip[2:].lower()
            if key in route_set:
                by_route_logs[key].append(L)
            else:
                other += 1

        indexer_by_route = Counter(
            str(e["route"]).lower().removeprefix("0x") for e in events)

        per_route = []
        miss = []
        for addr, meta in sorted(routes_by.items()):
            c_n = len(by_route_logs.get(addr, []))
            i_n = indexer_by_route.get(addr, 0)
            row = {
                "route": addr,
                "symbol": meta.get("symbol"),
                "standard": meta.get("standard"),
                "chain_msgs": c_n,
                "indexer_msgs": i_n,
                "ratio": (i_n / c_n) if c_n else None,
            }
            per_route.append(row)
            if c_n > 0 and i_n == 0:
                miss.append(row)

        chain_msgs = sum(r["chain_msgs"] for r in per_route)
        indexer_msgs = sum(r["indexer_msgs"] for r in per_route)
        coverage = blocks_ok / blocks_total if blocks_total else 0.0

        candidates = []
        for addr, ls in by_route_logs.items():
            if indexer_by_route.get(addr, 0) > 0:
                candidates.extend(ls)
        if len(candidates) >= 5:
            sample = [candidates[i] for i in sorted({0, len(candidates)//4,
                                                     len(candidates)//2,
                                                     3*len(candidates)//4,
                                                     len(candidates)-1})]
        else:
            sample = candidates[:5]
        neg = negative_control(url, mailbox, disc["topic0"], sample)
        print(f"negative control: {neg['passed']}/{neg['n']} receipts confirmed", flush=True)
        if neg["n"] and neg["passed"] < neg["n"]:
            print("FATAL: negative control failed — decode/filter bug", file=sys.stderr)

        chain_events = []
        for addr, ls in by_route_logs.items():
            for L in ls:
                chain_events.append({
                    "route": addr,
                    "block": int(L["blockNumber"], 16),
                    "tx": L["transactionHash"],
                    "origin": int(L["topics"][1], 16),
                })
        (DATA / f"chainlog_events_{chain}.json").write_text(json.dumps({
            "chain": chain,
            "mailbox": mailbox,
            "mailbox_verify": mb_check,
            "process_topic": disc,
            "n": len(chain_events),
            "events": chain_events,
        }, indent=1) + "\n")
        if chain == "ethereum":
            (DATA / "chainlog_events.json").write_text(
                (DATA / f"chainlog_events_{chain}.json").read_text())
        # Stage-1 complete and durable — drop checkpoint so a later rerun
        # does not accidentally resume a finished window.
        clear_stage1_checkpoint(chain)

    stage2 = {"attempted": False}
    if not args.skip_stage2:
        print("stage2: value reconstruction…", flush=True)
        try:
            stage2 = stage2_value(
                url, routes_by, by_route_logs, start_b, end_b, chain, summary,
                top_n=args.stage2_top, shard=args.shard)
        except Exception as e:
            stage2 = {"attempted": True, "ok": False, "reason": str(e)[:300]}

    priced = stage2.pop("priced_events", None) if isinstance(stage2, dict) else None
    if priced:
        priced_path = DATA / f"chainlog_priced_{chain}.json"
        priced_path.write_text(json.dumps({
            "chain": chain,
            "source": "ReceivedTransferRemote stage2",
            "stage2_top_n": stage2.get("stage2_top_n"),
            "routes_scanned": stage2.get("routes_scanned"),
            "routes_with_process": stage2.get("routes_with_process"),
            "routes_uncovered": stage2.get("routes_uncovered"),
            "peak_daily": stage2.get("peak_daily"),
            "mean_daily": stage2.get("mean_daily"),
            "n": len(priced),
            "events": priced,
        }) + "\n")
        print(f"wrote {priced_path} ({len(priced)} priced events)", flush=True)

    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chain": chain,
        "rpc": url,
        "mailbox": mailbox,
        "mailbox_verify": mb_check if not args.stage2_only else None,
        "window": {"t_min": t_min, "t_max": t_max, "start_block": start_b, "end_block": end_b},
        "process_discovery": disc,
        "coverage": {
            "blocks_ok": blocks_ok,
            "blocks_total": blocks_total,
            "fraction": round(coverage, 6),
            "unresolved_ranges": len(unresolved),
            "unresolved_span": sum(
                (u.get("to", 0) - u.get("from", 0) + 1) for u in unresolved),
        },
        "unresolved": unresolved[:50],
        "totals": {
            "routes_default": len(routes),
            "chain_msgs_default_routes": chain_msgs,
            "indexer_priced_msgs": indexer_msgs,
            "indexer_summary_messages": summary.get("messages"),
            "indexer_peak_daily": summary.get("peak_daily"),
            "indexer_mean_daily": summary.get("mean_daily"),
            "process_logs_other_recipients": other,
            "ratio_indexer_priced_over_chain": (indexer_msgs / chain_msgs) if chain_msgs else None,
        },
        "indexer_miss_routes": miss,
        "per_route": per_route,
        "negative_control": neg,
        "stage2": {k: v for k, v in stage2.items() if k != "priced_events"},
    }
    xval_path.write_text(json.dumps(out, indent=2) + "\n")
    if chain == "ethereum":
        legacy_xval.write_text(json.dumps(out, indent=2) + "\n")

    # Markdown
    lines = [
        f"# Chain-log Cross-Validation ({chain})",
        "",
        f"Generated: {out['generated_at']}  ",
        f"RPC: `{url}`  ",
        f"Mailbox: `{mailbox}` (verified)  ",
        f"Window: `{t_min}` → `{t_max}`  blocks `{start_b}`–`{end_b}`  ",
        f"Process topic0: `{disc.get('topic0')}` ({disc.get('confirmed_via')})",
        "",
        "## Coverage",
        "",
        f"- Blocks queried successfully: **{blocks_ok:,} / {blocks_total:,}** "
        f"({100*coverage:.2f}%)",
        f"- Unresolved ranges: **{len(unresolved)}** "
        f"(span {out['coverage']['unresolved_span']:,} blocks)",
        "",
        "## Stage 1 — message counts",
        "",
        f"| | Count |",
        f"|---|---:|",
        f"| Default-ISM routes | {len(routes)} |",
        f"| Chain `Process` (those routes) | {chain_msgs:,} |",
        f"| Indexer priced events | {indexer_msgs:,} |",
        f"| Indexer GraphQL messages (summary) | {summary.get('messages')} |",
        f"| Indexer / chain (priced) | "
        f"{out['totals']['ratio_indexer_priced_over_chain'] and round(out['totals']['ratio_indexer_priced_over_chain'], 4)} |",
        "",
        f"Routes with `chain_msgs > 0` and `indexer_msgs == 0`: **{len(miss)}**",
        "",
    ]
    if miss:
        lines += ["### Possible indexer gaps (chain saw delivery, indexer priced none)", ""]
        for r in miss[:30]:
            lines.append(f"- `{r['route']}` {r.get('symbol')}  chain={r['chain_msgs']}")
        lines.append("")
    lines += [
        "## Negative control",
        "",
        f"{neg['passed']}/{neg['n']} sampled Process txs re-confirmed in receipts.",
        "",
    ]
    for r in neg.get("results") or []:
        lines.append(f"- `{r.get('tx','')[:18]}…` ok={r.get('ok')} "
                     f"recipient=`{r.get('recipient','')[:12]}…`")
    lines += ["", "## Stage 2 — value reconstruction", ""]
    if stage2.get("ok"):
        lines += [
            f"- Routes scanned: **{stage2.get('routes_scanned')}** / "
            f"{stage2.get('routes_with_process')} with Process traffic "
            f"(uncovered={len(stage2.get('routes_uncovered') or [])})",
            "",
            f"| | Indexer | Chain |",
            f"|---|---:|---:|",
            f"| peak_daily | ${stage2['indexer_peak_daily']:,.0f} | "
            f"${stage2['peak_daily']:,.0f} |",
            f"| mean_daily | ${stage2['indexer_mean_daily']:,.0f} | "
            f"${stage2['mean_daily']:,.0f} |",
            f"| ratio chain/indexer peak | | "
            f"{stage2.get('peak_ratio_chain_over_indexer')} |",
            "",
        ]
        if stage2.get("routes_uncovered"):
            lines.append(f"Uncovered routes (explicit): "
                         f"`{', '.join(stage2['routes_uncovered'][:20])}`")
            lines.append("")
    else:
        lines.append(f"Not completed: {stage2.get('reason') or stage2}")
        lines.append("")

    # Verdict hint for paper (do not auto-edit paper)
    lines += ["## Verdict hint (do not auto-edit paper)", ""]
    if coverage >= 0.95 and len(miss) < 5 and neg["passed"] == neg["n"]:
        lines.append("Coverage ≥ 95% and few indexer-miss routes → Threats can upgrade.")
    elif coverage >= 0.85:
        lines.append("Coverage 85–95%: reportable with caveats.")
    else:
        lines.append("Coverage < 85% or controls failed: keep existing caveat / fix first.")
    if stage2.get("ok") and stage2.get("peak_ratio_chain_over_indexer"):
        ratio = stage2["peak_ratio_chain_over_indexer"]
        if ratio > 1.10:
            lines.append(
                f"**Peak on-chain is {ratio:.2f}× indexer — expected under-reporting "
                f"signal (🔴 not an anomaly). Do not run sim3 in this task.**"
            )
        elif abs(ratio - 1) < 0.10:
            lines.append("Stage-2 peak within 10% of indexer — $B$ calibration holds.")
    lines.append("")
    md_path = HERE / f"CHAINLOG-XVAL-{chain}.md"
    md_path.write_text("\n".join(lines))
    if chain == "ethereum":
        (HERE / "CHAINLOG-XVAL.md").write_text("\n".join(lines))

    print(f"wrote {xval_path}")
    print(f"wrote {DATA / f'chainlog_events_{chain}.json'} ({len(chain_events)} events)")
    print(f"wrote {md_path}")
    print(f"coverage={100*coverage:.2f}%  chain_msgs={chain_msgs}  "
          f"indexer_priced={indexer_msgs}  miss_routes={len(miss)}")

    if neg["n"] and neg["passed"] < neg["n"]:
        sys.exit(2)
    if coverage < 0.50:
        sys.exit(3)


if __name__ == "__main__":
    main()
