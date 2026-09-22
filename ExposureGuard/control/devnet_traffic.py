#!/usr/bin/env python3
"""Emit one metered message through the persistent local operational devnet."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE = HERE / "data/devnet"
DEPLOY = LIVE / "deployment.json"
STATE = LIVE / "traffic_state.json"
LOG = LIVE / "traffic.jsonl"
RPC = "http://127.0.0.1:8545"
DEVNET_ACCOUNT = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"
CAST = os.environ.get("CAST_BIN", "cast")


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def main() -> None:
    dep = json.loads(DEPLOY.read_text())
    st = json.loads(STATE.read_text()) if STATE.exists() else {"nonce": 0}
    st["nonce"] += 1
    nonce = st["nonce"]
    route = dep["route"].lower().removeprefix("0x")
    recipient32 = "00" * 12 + route
    message = ("03" + nonce.to_bytes(4, "big").hex() + (1).to_bytes(4, "big").hex()
               + "00" * 32 + (2).to_bytes(4, "big").hex() + recipient32
               + recipient32 + (100 * 10**18).to_bytes(32, "big").hex())
    msg_hex = "0x" + message
    msg_id = run(CAST, "keccak", msg_hex)
    mark = run(CAST, "send", dep["mailbox"],
               "markDelivered(bytes32)", msg_id, "--rpc-url", RPC,
               "--unlocked", "--from", DEVNET_ACCOUNT, "--json")
    verify = run(CAST, "send", dep["guard"],
                 "verify(bytes,bytes)", "0x", msg_hex, "--rpc-url", RPC,
                 "--unlocked", "--from", DEVNET_ACCOUNT, "--json")
    record = {
        "at": datetime.now(timezone.utc).isoformat(), "nonce": nonce,
        "message_id": msg_id, "mailbox_receipt": json.loads(mark),
        "guard_receipt": json.loads(verify), "network": "local_anvil_devnet",
    }
    LIVE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    STATE.write_text(json.dumps(st, indent=2) + "\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
