#!/usr/bin/env python3
"""Build a fixture for GasDeployed.t.sol: one real Mailbox.process() delivery.

Picks a chainlog tx, unwraps Multicall3 if needed, and writes metadata/message
so a mainnet-fork test can A/B:
  (1) process through the deployed default ISM
  (2) process through Aggregation(defaultISM, ExposureGuard)
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROCESS_SEL = "0x7c39d130"
AGG3_SEL = "0x82ad56cb"
CAST = os.path.expanduser("~/.foundry/bin/cast")
MAILBOX = "0xc005dc82818d67AF737725bD4bf75435d065D239"


def cast(*args):
    bin_ = CAST if os.path.isfile(CAST) else "cast"
    r = subprocess.run([bin_, *args], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "")[:300])
    return r.stdout.strip()


def unwrap_process(inp: str) -> str:
    if inp.startswith(AGG3_SEL):
        body = inp[10:]
        off = int(body[0:64], 16) * 2
        n = int(body[off:off + 64], 16)
        heads = off + 64
        for i in range(n):
            rel = int(body[heads + i * 64: heads + (i + 1) * 64], 16) * 2
            st = heads + rel
            cd_off = int(body[st + 128: st + 192], 16) * 2
            cs = st + cd_off
            ln = int(body[cs:cs + 64], 16)
            call = "0x" + body[cs + 64: cs + 64 + ln * 2]
            if call.startswith(PROCESS_SEL):
                return call
        raise ValueError("multicall: no process()")
    if not inp.startswith(PROCESS_SEL):
        raise ValueError(f"not process: {inp[:10]}")
    return inp


def decode_process(inp: str):
    inp = unwrap_process(inp)
    body = inp[10:]
    off_md = int(body[0:64], 16)
    off_msg = int(body[64:128], 16)
    base_md = off_md * 2
    ln_md = int(body[base_md:base_md + 64], 16)
    md = "0x" + body[base_md + 64: base_md + 64 + ln_md * 2]
    base_msg = off_msg * 2
    ln_msg = int(body[base_msg:base_msg + 64], 16)
    msg = "0x" + body[base_msg + 64: base_msg + 64 + ln_msg * 2]
    return md, msg


def recipient_from_message(msg_hex: str) -> str:
    # Hyperlane message: version(1) nonce(4) origin(4) sender(32) dest(4) recipient(32) body
    raw = bytes.fromhex(msg_hex[2:])
    recip = raw[1 + 4 + 4 + 32 + 4: 1 + 4 + 4 + 32 + 4 + 32]
    return "0x" + recip[-20:].hex()


def amount_from_message(msg_hex: str) -> int:
    # TokenMessage: recipient(32) + amount(32) as body after Hyperlane header (77 bytes)
    raw = bytes.fromhex(msg_hex[2:])
    body = raw[77:]
    if len(body) < 64:
        return 0
    return int.from_bytes(body[32:64], "big")


def main():
    url = os.environ.get("ETH_RPC_URL") or sys.exit("need ETH_RPC_URL")
    src = f"{HERE}/data/chainlog_events.json"
    ev = json.load(open(src))
    ev = ev["events"] if isinstance(ev, dict) else ev
    random.seed(7)
    sample = random.sample(ev, min(40, len(ev)))

    chosen = None
    for e in sample:
        try:
            inp = cast("tx", "--rpc-url", url, e["tx"], "input")
            md, msg = decode_process(inp)
            recip = recipient_from_message(msg)
            amt = amount_from_message(msg)
            if amt <= 0:
                continue
            chosen = {
                "tx": e["tx"],
                "block": int(e["block"]),
                "route": e.get("route"),
                "origin": e.get("origin"),
                "mailbox": MAILBOX,
                "metadata": md,
                "message": msg,
                "recipient": recip,
                "amount": amt,
                "fork_block": int(e["block"]) - 1,
            }
            break
        except Exception as ex:
            print(f"  skip {e['tx'][:14]}… {ex}", flush=True)
            continue
    if not chosen:
        sys.exit("no usable process() tx found")

    # Confirm default ISM at fork block
    dism = cast("call", "--rpc-url", url, "--block", str(chosen["fork_block"]),
                MAILBOX, "defaultIsm()(address)")
    chosen["default_ism"] = dism.strip().split()[-1] if dism else ""

    path = f"{HERE}/data/gas_deployed_fixture.json"
    json.dump(chosen, open(path, "w"), indent=1)
    print(json.dumps({k: (v if k not in ("metadata", "message") else v[:66] + "…")
                      for k, v in chosen.items()}, indent=2))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
