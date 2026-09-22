#!/usr/bin/env python3
"""Run an isolated fail-closed/refill sequence on the public testnet route.

The sequence deploys a fresh destination recipient but reuses the already
deployed 2-of-2 testnet aggregation.  It records expected reverts, never treats
them as harness failures, and does not modify the steady route's policy.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import testnet_workload as tw

HERE = Path(__file__).resolve().parent
DEPLOYMENT = HERE / "data/testnet_deployment.json"
OUTPUT = HERE / "data/testnet_fault_sequence.json"


def command(args: list[str], *, expect_ok: bool = True) -> subprocess.CompletedProcess:
    for attempt in range(5):
        p = subprocess.run(args, cwd=HERE / "contracts", text=True, capture_output=True)
        if not p.returncode or not expect_ok:
            return p
        if attempt < 4:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"{args[0]} failed: {p.stderr.strip()}")


def call(address: str, signature: str, *args: str) -> str:
    return tw.run("cast", "call", address, signature, *args,
                  "--rpc-url", tw.DST_RPC).split()[0]


def send_tx(address: str, signature: str, args: list[str], auth: list[str],
            *, expected_status: int = 1) -> dict:
    receipt = json.loads(tw.run(
        "cast", "send", address, signature, *args, *auth,
        "--rpc-url", tw.DST_RPC, "--gas-limit", "1000000", "--json"))
    status = int(receipt["status"], 16)
    if status != expected_status:
        raise RuntimeError(f"unexpected transaction status {status}, wanted {expected_status}")
    return receipt


def deploy_route(auth: list[str], mailbox: str) -> str:
    bytecode = command([
        "forge", "inspect", "src/ControlledWarpRoute.sol:ControlledWarpRoute",
        "bytecode"]).stdout.strip()
    encoded = tw.run("cast", "abi-encode", "constructor(address)", mailbox)
    initcode = bytecode + encoded.removeprefix("0x")
    receipt = json.loads(tw.run(
        "cast", "send", *auth, "--rpc-url", tw.DST_RPC,
        "--gas-limit", "2000000", "--json", "--create", initcode))
    if int(receipt["status"], 16) != 1 or not receipt.get("contractAddress"):
        raise RuntimeError("destination route deployment failed")
    return receipt["contractAddress"]


def process(message: str, auth: list[str], expected_status: int) -> dict:
    return send_tx(tw.DST_MAILBOX, "process(bytes,bytes)",
                   [tw.AGGREGATION_METADATA, message], auth,
                   expected_status=expected_status)


def main() -> None:
    tw.load_env()
    auth = tw.wallet_args()
    deployment = json.loads(DEPLOYMENT.read_text())
    dst = deployment["base_sepolia"]
    route = deploy_route(auth, dst["mailbox"])
    src32 = "0x" + "0" * 24 + tw.SRC_ROUTE.removeprefix("0x")
    send_tx(route, "setRemote(uint32,bytes32)", ["11155111", src32], auth)
    send_tx(route, "setInterchainSecurityModule(address)",
            [dst["aggregation_ism"]], auth)
    guard = dst["guard"]
    send_tx(guard, "setModes(address[],uint8[])", [f"[{route}]", "[1]"], auth)

    recipient32 = "0x" + "0" * 24 + route.removeprefix("0x")
    unpriced = dispatch_to(route, recipient32, auth, 10**18)
    unpriced_receipt = process(unpriced["message"], auth, 0)

    send_tx(guard, "setUnitValues(address[],uint256[])",
            [f"[{route}]", f"[{10**18}]"], auth)
    warmup = dispatch_to(route, recipient32, auth, 20 * 10**18)
    process(warmup["message"], auth, 1)
    available = int(call(guard, "availableFor(address)(uint256)", route))
    # Leave enough margin for public-RPC dispatch latency; a one-unit margin
    # can refill before the destination transaction is mined.
    delta = 10 * 10**18
    budget = int(call(guard, "budget()(uint96)"))
    alpha_bps = int(call(guard, "alphaBps()(uint16)"))
    window = int(call(guard, "WINDOW()(uint64)"))
    refill_cap = budget * (10_000 - alpha_bps) // 10_000
    overflow_amount = min(refill_cap, available + delta)
    if overflow_amount <= available:
        raise RuntimeError("fault route has no refill-recoverable overflow margin")
    overflow = dispatch_to(route, recipient32, auth, overflow_amount)
    overflow_receipt = process(overflow["message"], auth, 0)
    refill_need = overflow_amount - available
    wait_seconds = max(2, int(refill_need * window / refill_cap) + 30)
    time.sleep(wait_seconds)
    refill_receipt = process(overflow["message"], auth, 1)
    replay_receipt = process(overflow["message"], auth, 0)
    result = {
        "schema": 1, "completed_at": datetime.now(timezone.utc).isoformat(),
        "network": "sepolia_to_base_sepolia",
        "workload": "author_operated_fault_sequence",
        "fresh_destination_route": route,
        "checks": {
            "metered_but_unpriced_rejected": int(unpriced_receipt["status"], 16) == 0,
            "budget_overflow_rejected": int(overflow_receipt["status"], 16) == 0,
            "same_message_admitted_after_refill": int(refill_receipt["status"], 16) == 1,
            "delivered_message_replay_rejected": int(replay_receipt["status"], 16) == 0,
        },
        "refill_wait_seconds": wait_seconds,
        "message_ids": {"unpriced": unpriced["message_id"],
                        "overflow_refill": overflow["message_id"]},
        "transactions": {
            "unpriced_revert": unpriced_receipt["transactionHash"],
            "overflow_revert": overflow_receipt["transactionHash"],
            "refill_success": refill_receipt["transactionHash"],
            "replay_revert": replay_receipt["transactionHash"],
        },
    }
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


def dispatch_to(route: str, recipient32: str, auth: list[str], amount: int) -> dict:
    quote = int(tw.run("cast", "call", tw.SRC_ROUTE,
                       "quote(uint32,bytes32,uint256)(uint256)", tw.DST_DOMAIN,
                       recipient32, str(amount), "--rpc-url", tw.SRC_RPC).split()[0])
    receipt = json.loads(tw.run(
        "cast", "send", tw.SRC_ROUTE, "send(uint32,bytes32,uint256)(bytes32)",
        tw.DST_DOMAIN, recipient32, str(amount), "--value", str(quote),
        *auth, "--rpc-url", tw.SRC_RPC, "--json"))
    message = message_id = None
    for log in receipt["logs"]:
        if log["topics"][0].lower() == tw.DISPATCH_TOPIC:
            encoded = log["data"][2:]
            length = int(encoded[64:128], 16)
            message = "0x" + encoded[128:128 + 2 * length]
        if len(log["topics"]) > 1 and log["topics"][0].lower() == \
                "0x788dbc1b7152732178210e7f4d9d010ef016f9eafbe66786bd7169f56e0c353a":
            message_id = log["topics"][1]
    if not message or not message_id:
        raise RuntimeError("dispatch receipt lacks message")
    return {"receipt": receipt, "message": message, "message_id": message_id}


if __name__ == "__main__":
    main()
