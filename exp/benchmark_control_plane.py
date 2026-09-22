#!/usr/bin/env python3
"""Benchmark the frozen-trace ExposureGuard controller and offline auditor."""
from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "ExposureGuard" / "control"))
from egctl import audit, load_events, recommend

OUT = HERE / "data/reproduced/control_plane_benchmark.json"


def host_value(command: list[str], fallback: str) -> str:
    try:
        value = subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL).strip()
        return value or fallback
    except (OSError, subprocess.CalledProcessError):
        return fallback


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def summarize(values: list[float]) -> dict:
    return {
        "median_ms": round(statistics.median(values), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
    }


def one_run() -> tuple[dict, dict]:
    t0 = time.perf_counter()
    rows = load_events()
    t1 = time.perf_counter()
    config = recommend(rows, alpha=0.8, headroom=1.07, lookback_days=60)
    t2 = time.perf_counter()
    generated = datetime.fromisoformat(config["generated_at"]).timestamp()
    report = audit(config, max_age_days=1, now=generated)
    t3 = time.perf_counter()
    if not report["ok"]:
        raise RuntimeError(report["errors"])
    timing = {
        "load_ms": (t1 - t0) * 1000,
        "recommend_ms": (t2 - t1) * 1000,
        "audit_ms": (t3 - t2) * 1000,
        "end_to_end_ms": (t3 - t0) * 1000,
    }
    return timing, {"events": len(rows), "config": config, "audit": report}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    if args.runs < 1 or args.warmup < 0:
        raise SystemExit("runs must be positive and warmup non-negative")

    for _ in range(args.warmup):
        one_run()
    samples = []
    metadata = None
    for _ in range(args.runs):
        timing, metadata = one_run()
        samples.append(timing)

    assert metadata is not None
    fields = ("load_ms", "recommend_ms", "audit_ms", "end_to_end_ms")
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": "frozen four-chain trace -> recommend -> offline audit",
        "runs": args.runs,
        "warmup_runs": args.warmup,
        "events": metadata["events"],
        "chains": len(metadata["config"]["chains"]),
        "covered_routes": sum(
            c["covered_routes"] for c in metadata["config"]["chains"].values()),
        "parameters": {"alpha": 0.8, "headroom": 1.07, "lookback_days": 60},
        "host": {
            "cpu": host_value(["sysctl", "-n", "machdep.cpu.brand_string"],
                              platform.processor() or platform.machine()),
            "model": host_value(["sysctl", "-n", "hw.model"], platform.node()),
            "memory_bytes": int(host_value(["sysctl", "-n", "hw.memsize"], "0")),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "timing": {field: summarize([s[field] for s in samples]) for field in fields},
        "audit_scope": "offline recommendation invariants; no deployment snapshot",
        "samples_ms": [{k: round(v, 3) for k, v in sample.items()} for sample in samples],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
