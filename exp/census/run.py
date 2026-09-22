#!/usr/bin/env python3
"""Run full cross-stack census pipeline.

Usage:
  python3 run.py              # all stacks (LayerZero full ~1037 OApps — slow)
  python3 run.py --quick      # LZ/CCIP sample limits for dev
  python3 run.py --import-only  # Hyperlane + Wormhole + architectural only
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


def _load_adapter(name: str):
    path = os.path.join(HERE, "adapters", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"census_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="Sample LZ/CCIP (100 OApps / 40 pools)")
    ap.add_argument("--import-only", action="store_true", help="Skip on-chain LZ/CCIP probes")
    args = ap.parse_args()

    if args.quick:
        os.environ.setdefault("LZ_CENSUS_LIMIT", "100")
        os.environ.setdefault("CCIP_CENSUS_LIMIT", "40")

    hyperlane = _load_adapter("hyperlane")
    wormhole = _load_adapter("wormhole")
    architectural = _load_adapter("architectural")

    stacks = [
        hyperlane.run(chain="ethereum"),
        wormhole.run(),
        *architectural.run_all(),
    ]

    if not args.import_only:
        layerzero = _load_adapter("layerzero")
        ccip = _load_adapter("ccip")
        print("Running LayerZero census (on-chain)...", file=sys.stderr)
        stacks.append(layerzero.run())
        print("Running CCIP census (on-chain)...", file=sys.stderr)
        stacks.append(ccip.run())

    os.makedirs(DATA, exist_ok=True)
    for rec in stacks:
        path = os.path.join(DATA, f"census_{rec['stack']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2)
        print(f"wrote {path}", file=sys.stderr)

    merged = {"generated_at": stacks[0]["generated_at"], "stacks": stacks}
    with open(os.path.join(DATA, "census_merged.json"), "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)

    agg_spec = importlib.util.spec_from_file_location("census_aggregate", os.path.join(HERE, "aggregate.py"))
    agg = importlib.util.module_from_spec(agg_spec)
    assert agg_spec.loader
    agg_spec.loader.exec_module(agg)
    out = agg.aggregate(stacks)
    with open(os.path.join(DATA, "census_aggregate.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    md = agg.markdown_table(stacks, out["prevalence"])
    with open(os.path.join(HERE, "CENSUS-RESULTS.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)


if __name__ == "__main__":
    main()
