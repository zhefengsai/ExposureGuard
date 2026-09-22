#!/usr/bin/env python3
"""Source-checked LayerZero V2 feasibility probe.

This intentionally refuses to call an OApp-local `_lzReceive` hook a second
root-wide implementation.  It verifies the two protocol facts that determine
the actual integration point: the Endpoint owner can replace the default
receive library, while each OApp/delegate can select a non-default library and
escape that default.  A real ExposureGuard port must therefore wrap a receive
MessageLib registered by the Endpoint owner and monitor coverage; an OApp stub
would establish only per-application admission.
"""
from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data/layerzero_feasibility.json"
PROTOTYPE = HERE.parent / "ExposureGuard" / "contracts" / "src" / "LayerZeroExposureReceiveLib.sol"
UPSTREAM_COMMIT = "9c741e7f9790639537b1710a203bcdfd73b0b9ac"
BASE = f"https://raw.githubusercontent.com/LayerZero-Labs/LayerZero-v2/{UPSTREAM_COMMIT}/"
FILES = {
    "endpoint": BASE + "packages/layerzero-v2/evm/protocol/contracts/EndpointV2.sol",
    "manager": BASE + "packages/layerzero-v2/evm/protocol/contracts/MessageLibManager.sol",
    "receiver": BASE + "packages/layerzero-v2/evm/oapp/contracts/oapp/OAppReceiver.sol",
}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "exposureguard-feasibility/1.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode()


def main() -> None:
    src = {name: fetch(url) for name, url in FILES.items()}
    checks = {
        "endpoint_owner_sets_default_receive_library": (
            "function setDefaultReceiveLibrary(" in src["manager"] and
            "external onlyOwner" in src["manager"]),
        "receive_library_resolves_per_receiver_then_default": (
            "receiveLibrary[_receiver][_srcEid]" in src["manager"] and
            "defaultReceiveLibrary[_srcEid]" in src["manager"]),
        "oapp_or_delegate_can_override_receive_library": (
            "function setReceiveLibrary(" in src["manager"] and
            "_assertAuthorized(_oapp);" in src["manager"]),
        "endpoint_executes_receiver_directly": (
            "ILayerZeroReceiver(_receiver).lzReceive" in src["endpoint"]),
        "oapp_hook_is_internal_application_logic": (
            "_lzReceive(_origin, _guid, _message, _executor, _extraData);" in src["receiver"] and
            ") internal virtual;" in src["receiver"]),
    }
    if not all(checks.values()):
        raise SystemExit(f"LayerZero source shape changed: {checks}")
    out = {
        "mode": "official_source_checked_integration_feasibility",
        "prototype_shipped": PROTOTYPE.exists(),
        "prototype_sha256": hashlib.sha256(PROTOTYPE.read_bytes()).hexdigest()
        if PROTOTYPE.exists() else None,
        "checks": checks,
        "source_sha256": {name: hashlib.sha256(text.encode()).hexdigest()
                          for name, text in src.items()},
        "sources": FILES,
        "result": {
            "matched_scope_candidate": "Endpoint-owner-installed default receive MessageLib wrapper",
            "authority": "Endpoint owner, distinct at address level from DVN operators",
            "coverage_limit": "Each OApp/delegate can select a non-default receive library",
            "rejected_shortcut": "An OApp _lzReceive guard is per-application, not per-root",
            "implementation_status": (
                "An interface-complete Receive MessageLib existence prototype meters the "
                "plaintext PacketV1 payload before Endpoint.verify. It is not a transparent "
                "ULN302 wrapper: ULN302 exposes only payloadHash at commit time, so a wrapper "
                "cannot recover the declared value. Endpoint-owner registration and default "
                "selection are still required, and OApps may override the default."
            ),
        },
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
