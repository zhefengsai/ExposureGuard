#!/usr/bin/env python3
"""Prospective, read-only ExposureGuard shadow runner.

This process reads finalized production-chain logs, appends newly priced
Hyperlane transfers to an immutable JSONL observation stream, and freezes one
reviewable controller recommendation per UTC day.  It never holds a wallet,
sends a transaction, or changes production state.

The first collection catches up from the frozen paper trace.  Prospective
evaluation starts only after the first daily configuration has been frozen;
backfilled observations are therefore never counted as live-shadow days.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import time
from contextlib import contextmanager
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import chainlog_xval as cx
import egctl

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
LIVE = DATA / "live_shadow"
STATE = LIVE / "state.json"
EVENTS = LIVE / "events.jsonl"
STATE_LOCK = LIVE / "state.lock"
EVENTS_LOCK = LIVE / "events.lock"
FINALITY = {"ethereum": 64, "base": 200, "arbitrum": 200, "bsc": 200}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(tmp, path)


@contextmanager
def file_lock(path: Path):
    """Serialize short metadata/appends without serializing RPC collection."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def initial_state() -> dict:
    chains = {}
    for chain in egctl.CHAINS:
        prior = json.loads((DATA / f"chainlog_xval_{chain}.json").read_text())
        chains[chain] = {
            "last_finalized_block": int(prior["window"]["end_block"]),
            "source": "end of frozen paper trace; initial catch-up is not prospective",
        }
    return {
        "schema": 1,
        "created_at": utc_now().isoformat(),
        "first_config_date": None,
        "chains": chains,
    }


def load_state() -> dict:
    LIVE.mkdir(parents=True, exist_ok=True)
    with file_lock(STATE_LOCK):
        if not STATE.exists():
            atomic_json(STATE, initial_state())
        return json.loads(STATE.read_text())


def merge_chain_state(chain: str, chain_state: dict) -> dict:
    """Commit one chain cursor without overwriting concurrent collectors."""
    with file_lock(STATE_LOCK):
        state = json.loads(STATE.read_text())
        state["chains"][chain] = chain_state
        atomic_json(STATE, state)
        return state


def set_first_config_date(day: str) -> None:
    with file_lock(STATE_LOCK):
        state = json.loads(STATE.read_text())
        if state.get("first_config_date") is None:
            state["first_config_date"] = day
            atomic_json(STATE, state)


def append_unique(rows: list[dict]) -> int:
    with file_lock(EVENTS_LOCK):
        seen = set()
        if EVENTS.exists():
            for line in EVENTS.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    seen.add((r["chain"], r.get("tx"), r["route"], r.get("block")))
        fresh = []
        for r in rows:
            key = (r["chain"], r.get("tx"), r["route"], r.get("block"))
            if key not in seen:
                seen.add(key)
                fresh.append(r)
        if fresh:
            with EVENTS.open("a") as f:
                for r in sorted(fresh, key=lambda x: (x["t"], x["chain"], x["route"])):
                    f.write(json.dumps(r, sort_keys=True) + "\n")
        return len(fresh)


def collect_chain(chain: str, state: dict, shard: int) -> dict:
    url = os.environ.get(f"{chain.upper()}_RPC_URL", cx.DEFAULT_RPC[chain])
    if chain == "bsc" and os.environ.get("BSC_FALLBACK_RPC_URL"):
        os.environ["EXPOSUREGUARD_FALLBACK_RPC_URL"] = os.environ["BSC_FALLBACK_RPC_URL"]
    else:
        os.environ.pop("EXPOSUREGUARD_FALLBACK_RPC_URL", None)
    mailbox = cx.MAILBOX_CANDIDATES[chain]
    verified = None
    for attempt in range(3):
        verified = cx.verify_mailbox(url, chain, mailbox)
        if verified["ok"]:
            break
        time.sleep(2.0 * (attempt + 1))
    assert verified is not None
    if not verified["ok"]:
        raise RuntimeError(f"{chain}: mailbox verification failed")
    head = int(cx.rpc(url, "eth_blockNumber", []), 16)
    end = head - FINALITY[chain]
    start = int(state["chains"][chain]["last_finalized_block"]) + 1
    if end < start:
        return {"chain": chain, "start_block": start, "end_block": end,
                "caught_up": True, "process_logs": 0, "priced_events": 0,
                "coverage": 1.0, "unresolved": []}

    topic = cx.keccak_topic(cx.PROCESS_SIG)
    logs, unresolved, blocks_ok, blocks_total = cx.get_logs_sharded(
        url, mailbox, topic, start, end, shard=shard)
    routes = json.loads((DATA / f"hl_ism_{chain}.json").read_text())
    defaults = [r for r in routes if str(r.get("ism", "")).lower() == cx.ZERO]
    routes_by = {r["router"][2:].lower(): r for r in defaults}
    grouped: dict[str, list] = defaultdict(list)
    for log in logs:
        route = cx.recipient_from_process(log)[2:].lower()
        if route in routes_by:
            grouped[route].append(log)

    priced = []
    stage2 = {"attempted": False, "ok": True, "n_priced": 0}
    if grouped:
        stage2 = cx.stage2_value(url, routes_by, grouped, start, end, chain,
                                 {}, top_n=0, shard=shard)
        if stage2.get("ok"):
            priced = stage2.pop("priced_events", [])
    added = append_unique(priced)
    coverage = blocks_ok / blocks_total if blocks_total else 1.0
    snapshot = {
        "collected_at": utc_now().isoformat(), "chain": chain,
        "start_block": start, "end_block": end, "head": head,
        "finality_blocks": FINALITY[chain], "process_logs": len(logs),
        "default_route_process_logs": sum(map(len, grouped.values())),
        "priced_events": len(priced), "new_unique_events": added,
        "coverage": coverage, "unresolved": unresolved,
        "mailbox_verification": verified,
        "stage2": stage2,
    }
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    atomic_json(LIVE / "raw" / f"{stamp}-{chain}.json", snapshot)
    if not unresolved and coverage == 1.0 and stage2.get("ok", True):
        state["chains"][chain]["last_finalized_block"] = end
        state["chains"][chain]["updated_at"] = utc_now().isoformat()
    return snapshot


def combined_events() -> list[dict]:
    rows = egctl.load_events()
    if EVENTS.exists():
        for line in EVENTS.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                r["ts"] = datetime.strptime(r["t"], "%Y-%m-%dT%H:%M:%S").replace(
                    tzinfo=timezone.utc).timestamp()
                rows.append(r)
    dedup = {(r["chain"], r.get("tx"), r["route"], r["t"]): r for r in rows}
    return sorted(dedup.values(), key=lambda x: x["ts"])


def load_deployment_snapshot(path: Path | None) -> dict | None:
    """Load a read-only operational snapshot when one is explicitly supplied.

    The production-data shadow has no deployed production guard, so absence is
    recorded as such rather than silently replaced by a synthetic snapshot.
    """
    if path is None:
        return None
    return json.loads(path.read_text())


def assert_fresh_chains(state: dict, max_age_hours: float) -> dict[str, float]:
    now = utc_now()
    ages = {}
    stale = []
    for chain in egctl.CHAINS:
        updated = state["chains"][chain].get("updated_at")
        if not updated:
            stale.append(f"{chain}: never completed")
            continue
        age = (now - datetime.fromisoformat(updated)).total_seconds() / 3600
        ages[chain] = age
        if age > max_age_hours:
            stale.append(f"{chain}: {age:.1f}h old")
    if stale:
        raise RuntimeError("refusing recommendation with stale collection: " +
                           ", ".join(stale))
    return ages


def freeze_daily_config(state: dict, force: bool = False,
                        deployment_path: Path | None = None,
                        require_operational: bool = False,
                        max_chain_age_hours: float = 3.0) -> dict:
    day = utc_now().date().isoformat()
    path = LIVE / "configs" / f"{day}.json"
    if path.exists() and not force:
        return json.loads(path.read_text())
    chain_age_hours = assert_fresh_chains(state, max_chain_age_hours)
    now = datetime.combine(utc_now().date(), datetime.min.time(),
                           tzinfo=timezone.utc).timestamp()
    cfg = egctl.recommend(combined_events(), alpha=0.8, headroom=1.07,
                          lookback_days=60, as_of=now)
    deployment = load_deployment_snapshot(deployment_path)
    result = egctl.audit(
        cfg, max_age_days=2, now=now, deployment=deployment,
        require_operational=require_operational)
    record = {
        "mode": "prospective_production_data_shadow",
        "live_enforcement": False,
        "governance_transactions": False,
        "operational_snapshot": (
            str(deployment_path.resolve()) if deployment_path else None),
        "effective_date": day,
        "collection_age_hours": chain_age_hours,
        "max_collection_age_hours": max_chain_age_hours,
        "configuration": cfg,
        "audit": result,
    }
    if not result["ok"]:
        raise RuntimeError(f"daily configuration failed audit: {result['errors']}")
    atomic_json(path, record)
    set_first_config_date(day)
    return record


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("collect", "recommend", "run"), default="run",
                   nargs="?")
    p.add_argument("--chain", choices=egctl.CHAINS)
    p.add_argument("--shard", type=int, default=20_000)
    p.add_argument("--force", action="store_true")
    p.add_argument("--deployment", type=Path,
                   help="read-only deployment snapshot to audit with the recommendation")
    p.add_argument("--require-operational", action="store_true",
                   help="fail unless a complete, fresh deployment snapshot passes audit")
    p.add_argument("--max-chain-age-hours", type=float, default=3.0,
                   help="refuse a new recommendation if any chain cursor is older")
    args = p.parse_args()
    state = load_state()
    out = {"at": utc_now().isoformat(), "mode": args.mode, "results": []}
    if args.mode in ("collect", "run"):
        chains = (args.chain,) if args.chain else egctl.CHAINS
        for chain in chains:
            result = collect_chain(chain, state, args.shard)
            out["results"].append(result)
            state = merge_chain_state(chain, state["chains"][chain])
    if args.mode in ("recommend", "run"):
        state = load_state()
        out["config"] = freeze_daily_config(
            state, force=args.force, deployment_path=args.deployment,
            require_operational=args.require_operational,
            max_chain_age_hours=args.max_chain_age_hours)
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    atomic_json(LIVE / "runs" / f"{stamp}.json", out)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
