#!/usr/bin/env python3
"""Drive FaultMatrix forge tests, enforce anti-vacuity pairing, write results.

Budget B is materialised from data/sim3.json (Bc.ethereum) into
data/fault_budget.json as an integer wei amount — forge's parseJsonUint cannot
read floats, and the Solidity suite must not hard-code dollar figures.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
CONTRACTS = HERE.parent / "ExposureGuard" / "contracts"
DATA = HERE / "data"
SIM3 = DATA / "sim3.json"
BUDGET_JSON = DATA / "fault_budget.json"
OUT_JSON = DATA / "fault_matrix.json"
OUT_MD = HERE / "FAULT-MATRIX.md"

REQUIRED = [
    "test_F1_SameBlockSurplusRace",
    "test_F1_NegativeControl",
    "test_F2_RefillBoundary",
    "test_F2_NegativeControl",
    "test_F3_ReplayAlreadyMetered",
    "test_F3_NegativeControl",
    "test_F4_ReorgConsistency",
    "test_F4_NegativeControl",
    "test_F5_LowerBudgetMidTraffic",
    "test_F5_NegativeControl",
    "test_F6_RaiseTimelock",
    "test_F6_NegativeControl",
]


def die(msg: str) -> None:
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def materialise_budget() -> dict:
    if not SIM3.exists():
        die(f"missing {SIM3}")
    from decimal import Decimal
    sim = json.loads(SIM3.read_text())
    b_usd = Decimal(str(sim["Bc"]["ethereum"]))
    b_wei = int(b_usd * Decimal("1000000000000000000"))
    payload = {
        "B_wei": str(b_wei),
        "B_usd": float(b_usd),
        "source": "sim3.json Bc.ethereum",
    }
    BUDGET_JSON.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {BUDGET_JSON}  B_usd={b_usd:.6f}  B_wei={b_wei}")
    return payload


def check_hardcoding() -> list[str]:
    """Flag dollar-like literals outside the Params declaration block."""
    src = (CONTRACTS / "test/FaultMatrix.t.sol").read_text()
    # Strip the Params struct initializer (between 'Params internal P' and '};')
    stripped = re.sub(
        r"Params internal P = Params\(\{.*?\}\);",
        "Params internal P = Params({});",
        src,
        count=1,
        flags=re.S,
    )
    hits = []
    for m in re.finditer(r"[0-9]{4,}e18|100_000|280_753|262_203|280752", stripped):
        # allow 0.05e18 tightness and 1e18 unit price in helpers
        frag = m.group(0)
        if frag in ("0.05e18", "1e18"):
            continue
        line = stripped[: m.start()].count("\n") + 1
        hits.append(f"L{line}: {frag}")
    return hits


def run_forge() -> tuple[str, dict]:
    cmd = [
        "forge", "test",
        "--match-contract", "FaultMatrixTest",
        "-vv",
        "--json",
    ]
    env = os.environ.copy()
    env["PATH"] = os.path.expanduser("~/.foundry/bin") + ":" + env.get("PATH", "")
    r = subprocess.run(
        cmd, cwd=CONTRACTS, capture_output=True, text=True, env=env, timeout=600,
    )
    raw = (r.stdout or "") + (r.stderr or "")
    results: dict = {}
    # forge 1.x: single JSON blob, possibly with a wrapper; tests keyed as
    # "test_Name()" under the contract path, with "status": "Success"|"Failure".
    blob = None
    try:
        blob = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except json.JSONDecodeError:
        blob = None
    if isinstance(blob, dict):
        # Either top-level contract map, or {"test_results": {...}} 
        nodes = [blob]
        if "test_results" in blob and isinstance(blob["test_results"], dict):
            nodes.append(blob["test_results"])
        for node in nodes:
            for key, val in node.items():
                if not isinstance(val, dict):
                    continue
                # contract -> {test_F1_...(): {...}}
                for tname, info in val.items():
                    if isinstance(info, dict) and (
                        tname.startswith("test_") or "status" in info
                    ):
                        base = tname.split("(")[0]
                        if base.startswith("test_") and base != "test_results":
                            results[base] = info
                # or node itself is the test map
                if key.startswith("test_") and key != "test_results" and "status" in val:
                    results[key.split("(")[0]] = val
    return raw, results


def classify(name: str, info: dict) -> str:
    status = str(info.get("status") or "").lower()
    if status in ("pass", "success"):
        return "PASS"
    if status in ("fail", "failure"):
        return "FAIL"
    if "success" in info:
        return "PASS" if info["success"] else "FAIL"
    return "UNKNOWN"


def main() -> None:
    budget = materialise_budget()

    hits = check_hardcoding()
    if hits:
        die("hardcoded constants outside Params:\n  " + "\n  ".join(hits))
    print("anti-hardcode grep: clean")

    raw, results = run_forge()
    # Also run human-readable for the artifact log
    env = os.environ.copy()
    env["PATH"] = os.path.expanduser("~/.foundry/bin") + ":" + env.get("PATH", "")
    hum = subprocess.run(
        ["forge", "test", "--match-contract", "FaultMatrixTest", "-vv"],
        cwd=CONTRACTS, capture_output=True, text=True, env=env, timeout=600,
    )
    human_out = (hum.stdout or "") + (hum.stderr or "")
    (DATA / "fault_matrix_forge.log").write_text(human_out)

    # Re-parse from human output if json parse was empty
    if len(results) < len(REQUIRED):
        for m in re.finditer(r"\[(PASS|FAIL)\]\s+(\w+)", human_out):
            results.setdefault(m.group(2), {"status": m.group(1).lower()})

    missing = [t for t in REQUIRED if t not in results]
    if missing:
        die(f"missing required tests: {missing}\nseen={sorted(results)}")

    # Pairing: every Fx positive has NegativeControl
    for i in range(1, 7):
        pos = [n for n in results if re.match(rf"test_F{i}_(?!NegativeControl)", n)]
        neg = f"test_F{i}_NegativeControl"
        if not pos:
            die(f"F{i}: no positive test")
        if neg not in results:
            die(f"F{i}: missing {neg}")

    rows = []
    failed = []
    for name in sorted(results):
        if not name.startswith("test_F"):
            continue
        st = classify(name, results[name])
        reason = results[name].get("reason") or results[name].get("reason")
        rows.append({"test": name, "status": st, "reason": reason})
        if st != "PASS":
            failed.append(name)

    # Budget consistency: scrape FAULT_MATRIX_B_WEI from forge log
    m = re.search(r"FAULT_MATRIX_B_WEI\s+(\d+)", human_out)
    reported_b = int(m.group(1)) if m else None
    expected_b = int(budget["B_wei"])
    if reported_b is None:
        die("could not scrape FAULT_MATRIX_B_WEI from forge output")
    if reported_b != expected_b:
        die(f"B mismatch: forge={reported_b} fault_budget.json={expected_b}")

    sim_usd = float(json.loads(SIM3.read_text())["Bc"]["ethereum"])
    if abs(sim_usd - budget["B_usd"]) > 1e-6:
        die("budget payload drifted from sim3.json")

    out = {
        "B_wei": expected_b,
        "B_usd": budget["B_usd"],
        "B_source": "sim3.json Bc.ethereum",
        "forge_exit": hum.returncode,
        "tests": rows,
        "required_ok": not missing and not failed,
        "hardcode_hits": hits,
    }
    OUT_JSON.write_text(json.dumps(out, indent=2) + "\n")

    # Markdown
    lines = [
        "# Fault Matrix Results",
        "",
        f"Budget $B$ = **${budget['B_usd']:,.2f}**/day "
        f"(`B_wei={expected_b}`, from `sim3.json` → `Bc.ethereum`)",
        "",
        "## Summary",
        "",
        "| Test | Status |",
        "|---|---|",
    ]
    for r in rows:
        lines.append(f"| `{r['test']}` | **{r['status']}** |")
    lines += [
        "",
        "## Fault classes",
        "",
        "| # | Fault | Invariant | Result |",
        "|---|---|---|---|",
        "| F1 | Same-block dual surplus claim | sum admitted ≤ surplus | "
        + _pair_status(rows, "F1") + " |",
        "| F2 | Refill-boundary landing | cumulative ≤ B + BΔ/D | "
        + _pair_status(rows, "F2") + " |",
        "| F3 | Relayer replay | AlreadyMetered; charge once | "
        + _pair_status(rows, "F3") + " |",
        "| F4 | Reorg (snapshot/revert) | messageMetered + budget restore | "
        + _pair_status(rows, "F4") + " |",
        "| F5 | mid-traffic lowerBudget | immediate; no refund; new cap binds | "
        + _pair_status(rows, "F5") + " |",
        "| F6 | raise before timelock | TimelockPending; succeeds after delay | "
        + _pair_status(rows, "F6") + " |",
        "| F7 | pause mid-window (optional) | Paused; restores on unpause | "
        + _pair_status(rows, "F7") + " |",
        "",
        "## Anti-vacuity",
        "",
        "Each Fx has a paired `NegativeControl`. The driver refuses to export",
        "if any pair is missing. Positive tests assert `observed > 0` before",
        "`observed ≤ bound`.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "cd pilot && python3 fault_matrix.py",
        "```",
        "",
    ]
    OUT_MD.write_text("\n".join(lines))
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_MD}")

    if failed or hum.returncode != 0:
        die(f"failures: {failed}  forge_exit={hum.returncode}")
    print("Fault matrix: ALL REQUIRED TESTS PASSED")


def _pair_status(rows: list[dict], fx: str) -> str:
    pos = [r for r in rows if r["test"].startswith(f"test_{fx}_") and "Negative" not in r["test"]]
    neg = [r for r in rows if r["test"] == f"test_{fx}_NegativeControl"]
    ok = all(r["status"] == "PASS" for r in pos + neg) and bool(pos)
    if fx == "F7" and not pos and not neg:
        return "n/a"
    return "PASS" if ok else "FAIL"


if __name__ == "__main__":
    main()
