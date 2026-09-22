"""Minimal JSON-RPC helpers for census adapters."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")
ZERO_ADDR = "0x" + "0" * 40


class RpcError(RuntimeError):
    pass


def get_json(url: str, *, attempts: int = 5, timeout: float = 45.0) -> Any:
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "exposureguard-census/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.5 * (i + 1))
    raise RpcError(str(last or "fetch failed"))


def post_json(url: str, payload: dict[str, Any], *, attempts: int = 5, timeout: float = 45.0) -> Any:
    body = json.dumps(payload).encode()
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json", "User-Agent": "exposureguard-census/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out = json.loads(resp.read())
            if "error" in out:
                raise RpcError(out["error"])
            return out.get("result")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4 * (i + 1))
    raise RpcError(str(last or "rpc failed"))


def eth_block_number(rpc: str = DEFAULT_RPC) -> int:
    return int(post_json(rpc, {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}), 16)


def eth_call(to: str, data: str, rpc: str = DEFAULT_RPC) -> str:
    result = post_json(
        rpc,
        {"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": to, "data": data}, "latest"]},
    )
    if not result:
        raise RpcError(f"empty eth_call to {to}")
    return result


def pad_addr(addr: str) -> str:
    return addr.lower().replace("0x", "").zfill(64)


def pad_u32(n: int) -> str:
    return hex(n)[2:].zfill(64)


# Precomputed selectors (cast sig)
SEL_GET_RECEIVE_LIBRARY = "0x402f8468"
SEL_GET_CONFIG = "0x2b3197b9"
SEL_INBOUND_RL = "0xaf58d59f"
SEL_OUTBOUND_RL = "0xc75eea9c"
SEL_ISM = "0xde523cf3"
SEL_DEFAULT_ISM = "0x7f23b389"  # defaultIsm() on Mailbox


def call_get_receive_library(endpoint: str, oapp: str, eid: int, rpc: str = DEFAULT_RPC) -> tuple[str, bool]:
    data = SEL_GET_RECEIVE_LIBRARY + pad_addr(oapp) + pad_u32(eid)
    raw = eth_call(endpoint, data, rpc)
    body = raw[2:] if raw.startswith("0x") else raw
    lib = "0x" + body[24:64]
    # second word: bool is the least-significant byte
    is_default = len(body) >= 128 and int(body[126:128], 16) == 1
    return lib, is_default


def call_get_config(endpoint: str, oapp: str, lib: str, eid: int, config_type: int = 2, rpc: str = DEFAULT_RPC) -> str:
    data = SEL_GET_CONFIG + pad_addr(oapp) + pad_addr(lib) + pad_u32(eid) + pad_u32(config_type)
    return eth_call(endpoint, data, rpc)


def call_rate_limiter(
    pool: str, remote_selector: int, inbound: bool, rpc: str = DEFAULT_RPC
) -> tuple[int, int, bool, int, int]:
    sel = SEL_INBOUND_RL if inbound else SEL_OUTBOUND_RL
    data = sel + hex(remote_selector)[2:].zfill(64)
    raw = eth_call(pool, data, rpc)
    # five 32-byte words: tokens, lastUpdated, isEnabled, capacity, rate
    words = [int(raw[i : i + 64], 16) for i in range(2, len(raw), 64)]
    tokens, last_updated, enabled_word, capacity, rate = words[:5]
    return tokens, last_updated, bool(enabled_word & 1), capacity, rate


def config_fingerprint(config_hex: str) -> str:
    import hashlib

    raw = config_hex[2:] if config_hex.startswith("0x") else config_hex
    # getConfig returns ABI-encoded bytes: offset + length + payload
    if len(raw) >= 128:
        length = int(raw[64:128], 16)
        body = bytes.fromhex(raw[128 : 128 + length * 2])
    else:
        body = bytes.fromhex(raw)
    return hashlib.sha256(body).hexdigest()[:16]
