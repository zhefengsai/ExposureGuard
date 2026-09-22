#!/usr/bin/env python3
"""Do production relayers supply metadata for every sub-module of an aggregation?

Section V shows that an aggregation whose threshold is below its module count
lets any relayer omit a sub-module unilaterally: a module whose metadata range
has start == 0 is SKIPPED. On a fork we build the metadata ourselves, so that
demonstration cannot say what real relayers do. This does.

The deployed Hyperlane default ISM on Ethereum is itself an aggregation, and
production relayers deliver through it continuously. We decode the metadata
argument of real Mailbox.process() calls and check, for every sub-module,
whether the relayer gave it a non-zero range.

The question that matters for us is the module that needs no payload: does the
relayer still reserve a range for it, or drop it? Our module ignores metadata
content but must not be skipped.

Discipline: an RPC failure is recorded, never counted as a pass.

  export ETH_RPC_URL=...
  python3 relayer_metadata.py --chain ethereum --n 200
"""
from __future__ import annotations
import argparse, json, os, random, subprocess, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
PROCESS_SEL = "0x7c39d130"          # process(bytes,bytes), confirmed via cast sig
AGG3_SEL = "0x82ad56cb"             # Multicall3.aggregate3 — relayers batch deliveries
CAST = os.path.expanduser("~/.foundry/bin/cast")


def cast_tx(url: str, txh: str):
    r = subprocess.run([CAST if os.path.isfile(CAST) else "cast", "tx",
                        "--rpc-url", url, txh, "--json"],
                       capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        return None, (r.stderr or "").strip()[:140]
    try:
        return json.loads(r.stdout), None
    except json.JSONDecodeError as e:
        return None, str(e)[:140]


def unwrap_multicall(inp: str) -> str:
    """Relayers batch deliveries through Multicall3. Pull out the process()
    call so batched deliveries are not silently dropped from the sample."""
    body = inp[10:]
    off = int(body[0:64], 16) * 2
    n = int(body[off:off + 64], 16)
    heads = off + 64
    for i in range(n):
        rel = int(body[heads + i * 64: heads + (i + 1) * 64], 16) * 2
        st = heads + rel
        cd_off = int(body[st + 128: st + 192], 16) * 2      # target, allowFailure, callData
        cs = st + cd_off
        ln = int(body[cs:cs + 64], 16)
        call = "0x" + body[cs + 64: cs + 64 + ln * 2]
        if call.startswith(PROCESS_SEL):
            return call
    raise ValueError("multicall contained no process() call")


def decode_metadata(inp: str):
    """Return (n_modules, [(start,end)...]) or raise ValueError."""
    if inp.startswith(AGG3_SEL):
        inp = unwrap_multicall(inp)
    if not inp.startswith(PROCESS_SEL):
        raise ValueError(f"not process(): selector {inp[:10]}")
    body = inp[10:]
    off_md = int(body[0:64], 16)
    base = off_md * 2
    ln = int(body[base:base + 64], 16)
    md = bytes.fromhex(body[base + 64: base + 64 + ln * 2])
    if len(md) < 8:
        raise ValueError("metadata shorter than one range entry")
    first_start = int.from_bytes(md[0:4], "big")
    if first_start % 8 or first_start == 0:
        raise ValueError(f"range table length {first_start} not a multiple of 8")
    n = first_start // 8            # the table occupies bytes [0, first_start)
    ranges = [(int.from_bytes(md[i * 8:i * 8 + 4], "big"),
               int.from_bytes(md[i * 8 + 4:i * 8 + 8], "big")) for i in range(n)]
    return n, ranges


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", default="ethereum")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    url = os.environ.get("ETH_RPC_URL") or sys.exit("need ETH_RPC_URL")

    src = (f"{HERE}/data/chainlog_events_{a.chain}.json" if a.chain != "ethereum"
           else f"{HERE}/data/chainlog_events.json")
    ev = json.load(open(src))
    ev = ev["events"] if isinstance(ev, dict) else ev
    random.seed(a.seed)
    sample = random.sample(ev, min(a.n, len(ev)))

    ok = skipped = 0
    empty_payload_present = 0
    unresolved: list[dict] = []
    shapes = collections.Counter()
    examples: list[dict] = []

    for i, e in enumerate(sample):
        tx, err = cast_tx(url, e["tx"])
        if tx is None:
            unresolved.append({"tx": e["tx"], "error": err})
            continue
        try:
            n, ranges = decode_metadata(tx.get("input", ""))
        except ValueError as ve:
            unresolved.append({"tx": e["tx"], "error": str(ve)})
            continue
        shapes[n] += 1
        zero = [k for k, (s, _) in enumerate(ranges) if s == 0]
        empties = [k for k, (s, en) in enumerate(ranges) if s != 0 and en == s]
        if zero:
            skipped += 1
            if len(examples) < 5:
                examples.append({"tx": e["tx"], "ranges": ranges,
                                 "skipped_modules": zero})
        else:
            ok += 1
        if empties:
            empty_payload_present += 1
        if (i + 1) % 25 == 0:
            print(f"  … {i+1}/{len(sample)}  all-present={ok} skipped={skipped}",
                  flush=True)

    checked = ok + skipped
    out = {
        "chain": a.chain,
        "sampled": len(sample),
        "decoded": checked,
        "unresolved": unresolved,
        "module_count_distribution": dict(shapes),
        "all_submodules_present": ok,
        "some_submodule_skipped": skipped,
        "present_but_zero_length_payload": empty_payload_present,
        "skipped_examples": examples,
        "claim": ("A sub-module whose metadata range has start == 0 is skipped by "
                  "StaticAggregationIsm. Counting deliveries where every "
                  "sub-module received a non-zero start tests whether a "
                  "production relayer would skip a module that needs no payload."),
    }
    json.dump(out, open(f"{HERE}/data/relayer_metadata_{a.chain}.json", "w"),
              indent=1)

    print(f"\nchain {a.chain}: decoded {checked}/{len(sample)} sampled deliveries")
    print(f"  sub-module counts seen      : {dict(shapes)}")
    print(f"  every sub-module given range: {ok}")
    print(f"  some sub-module skipped     : {skipped}")
    print(f"  ranges with zero-length payload but non-zero start: "
          f"{empty_payload_present}")
    if unresolved:
        print(f"\n  {len(unresolved)} unresolved — NOT counted as passes:")
        for u in unresolved[:4]:
            print(f"    {u['tx'][:14]}… {u['error'][:70]}")
    print(f"\nwrote data/relayer_metadata_{a.chain}.json")


if __name__ == "__main__":
    main()
