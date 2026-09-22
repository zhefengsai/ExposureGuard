#!/usr/bin/env python3
"""Author-driven Hyperlane public-testnet workload.

Sends a value-bearing message through the real Sepolia Mailbox, then processes
it through the real Base Sepolia Mailbox using the explicitly testnet-only
author-operated relayer ISM composed with ExposureBudgetIsm.  Secrets are read
only from a local `.env` file (or the process environment) and are never logged.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data/testnet_workload.jsonl"
STATE = HERE / "data/testnet_workload_state.json"

SRC_RPC = os.getenv("SEPOLIA_RPC_URL", "https://ethereum-sepolia-rpc.publicnode.com")
DST_RPC = os.getenv("BASE_SEPOLIA_RPC_URL", "https://sepolia.base.org")
SRC_ROUTE = "0xdec7ba3037f639bb307672a2d365dc6c8e2e3e93"
DST_ROUTE = "0xa88e45709f717c546667c650526b34c54c7c0f4f"
DST_MAILBOX = "0x6966b0E55883d49BFB24539356a2f8A673E02039"
DST_DOMAIN = "84532"
DST_RECIPIENT = "0x000000000000000000000000a88e45709f717c546667c650526b34c54c7c0f4f"
GUARD = "0x3c6c8d6ac90ca7491af7381e8a9ccd755cf29a81"
DISPATCH_TOPIC = "0x769f711d20c679153d382254f59892613b58a97cc876b249134ac25c80f9c814"
# Two one-byte metadata entries for the 2-of-2 (AuthorRelayerIsm, ExposureBudgetIsm).
AGGREGATION_METADATA = "0x000000100000001100000011000000120000"


def load_env() -> None:
    path = HERE / ".env"
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def run(*args: str) -> str:
    for attempt in range(5):
        p = subprocess.run(args, text=True, capture_output=True)
        if p.returncode == 0:
            return p.stdout.strip()
        if attempt == 4:
            raise RuntimeError(f"command failed: {args[0]} {args[1]}: {p.stderr.strip()}")
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def wallet_args() -> list[str]:
    keystore = os.environ.get("DEPLOYER_KEYSTORE")
    password_file = os.environ.get("DEPLOYER_PASSWORD_FILE")
    if keystore and password_file:
        return ["--keystore", keystore, "--password-file", password_file]
    private_key = os.environ.get("DEPLOYER_PRIVATE_KEY", "")
    if len(private_key.removeprefix("0x")) != 64:
        raise SystemExit("no deployer keystore or private key configured")
    return ["--private-key", private_key]


def send(auth: list[str], amount: int) -> dict:
    quote = int(run("cast", "call", SRC_ROUTE, "quote(uint32,bytes32,uint256)(uint256)",
                    DST_DOMAIN, DST_RECIPIENT, str(amount), "--rpc-url", SRC_RPC).split()[0])
    receipt = json.loads(run(
        "cast", "send", SRC_ROUTE, "send(uint32,bytes32,uint256)(bytes32)",
        DST_DOMAIN, DST_RECIPIENT, str(amount), "--value", str(quote),
        *auth, "--rpc-url", SRC_RPC, "--json",
    ))
    message = None
    message_id = None
    for log in receipt["logs"]:
        if log["topics"][0].lower() == DISPATCH_TOPIC:
            encoded = log["data"][2:]
            length = int(encoded[64:128], 16)
            message = "0x" + encoded[128:128 + 2 * length]
        if len(log["topics"]) > 1 and log["topics"][0].lower() == \
                "0x788dbc1b7152732178210e7f4d9d010ef016f9eafbe66786bd7169f56e0c353a":
            message_id = log["topics"][1]
    if not message or not message_id:
        raise RuntimeError("dispatch receipt did not contain a Hyperlane message")
    return {"receipt": receipt, "message": message, "message_id": message_id, "quote": quote}


def main() -> None:
    load_env()
    auth = wallet_args()
    state = json.loads(STATE.read_text()) if STATE.exists() else {"sequence": 0}
    sequence = int(state["sequence"]) + 1
    amount = (1 + sequence % 3) * 10**18
    started = time.time()
    dispatched = send(auth, amount)
    processed = json.loads(run(
        "cast", "send", DST_MAILBOX, "process(bytes,bytes)",
        AGGREGATION_METADATA, dispatched["message"], *auth,
        "--rpc-url", DST_RPC, "--gas-limit", "1000000", "--json",
    ))
    mid = dispatched["message_id"]
    delivered = run("cast", "call", DST_MAILBOX, "delivered(bytes32)(bool)", mid,
                    "--rpc-url", DST_RPC) == "true"
    metered = run("cast", "call", GUARD, "messageMetered(bytes32)(bool)", mid,
                  "--rpc-url", DST_RPC) == "true"
    received = int(run("cast", "call", DST_ROUTE, "receivedCount()(uint256)",
                       "--rpc-url", DST_RPC).split()[0])
    if not (delivered and metered and processed["status"] == "0x1"):
        raise RuntimeError("destination receipt did not close the delivery/metering loop")
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sequence": sequence,
        "workload": "author_generated_steady",
        "origin": "sepolia",
        "destination": "basesepolia",
        "amount": amount,
        "message_id": mid,
        "dispatch_tx": dispatched["receipt"]["transactionHash"],
        "process_tx": processed["transactionHash"],
        "dispatch_gas": int(dispatched["receipt"]["gasUsed"], 16),
        "process_gas": int(processed["gasUsed"], 16),
        "end_to_end_seconds": round(time.time() - started, 3),
        "delivered": delivered,
        "metered": metered,
        "received_count": received,
        "relayer": "author_operated_testnet_relayer",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    STATE.write_text(json.dumps({"sequence": sequence, "last_message_id": mid}, indent=2) + "\n")
    print(json.dumps(row, indent=2))


if __name__ == "__main__":
    main()
