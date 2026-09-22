"""Value at risk held by default-ISM routes on each of the four chains.

Only Ethereum was measured before, so the per-chain budget split in sim3.py had
to be proportional to FLOW. Exposure is a stock, not a flow, so the split should
arguably follow stock instead -- this makes that comparison possible.

Two lessons from earlier rounds are applied here:
  * decimals come from the token contract, never from the registry (a registry
    mismatch once produced a $135 trillion figure)
  * transport failures are counted and reported, never silently treated as zero
"""
import concurrent.futures as cf
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ZERO = "0x" + "0" * 40
PRICE_CAP = 50e6

RPC = {"ethereum": "https://ethereum-rpc.publicnode.com",
       "base": "https://base-rpc.publicnode.com",
       "arbitrum": "https://arbitrum-one-rpc.publicnode.com",
       "bsc": "https://bsc-rpc.publicnode.com"}
LLAMA = {"ethereum": "ethereum", "base": "base",
         "arbitrum": "arbitrum", "bsc": "bsc"}

BAL = "0x70a08231"
DEC = "0x313ce567"


class CallFailed(Exception):
    pass


def call(rpc, to, data, attempts=5):
    last = None
    for i in range(attempts):
        payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                   "params": [{"to": to, "data": data}, "latest"]}
        req = urllib.request.Request(
            rpc, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "exposureguard-artifact/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read())
                if "error" in body:
                    last = str(body["error"])[:90]
                else:
                    return body.get("result")
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(0.5 * (i + 1))
    raise CallFailed(last or "unknown")


def measure(chain):
    rpc = RPC[chain]
    rows = [r for r in json.load(open(f"{HERE}/data/hl_ism_{chain}.json"))
            if r.get("ism") == ZERO]
    coll = [r for r in rows
            if r.get("collateral") and str(r["collateral"]).startswith("0x")]

    def probe(r):
        router, tok = r["router"], r["collateral"]
        b = call(rpc, tok, BAL + "0" * 24 + router[2:].lower())
        d = call(rpc, tok, DEC)
        bal = int(b, 16) if b and b != "0x" else 0
        dec = int(d, 16) if d and d != "0x" else None
        return r, bal, dec

    got, failed = [], []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(probe, r): r for r in coll}
        for f in cf.as_completed(futs):
            try:
                got.append(f.result())
            except CallFailed as e:
                failed.append((futs[f]["router"], str(e)))
    if failed:
        print(f"  {chain}: {len(failed)}/{len(coll)} routers FAILED "
              f"-- stock is incomplete: {failed[0][1][:60]}", file=sys.stderr)

    addrs = sorted({r["collateral"].lower() for r, _, _ in got})
    prices, pfx = {}, LLAMA[chain]
    for i in range(0, len(addrs), 40):
        q = ",".join(f"{pfx}:" + a for a in addrs[i:i + 40])
        try:
            with urllib.request.urlopen(
                    "https://coins.llama.fi/prices/current/" + q, timeout=40) as r:
                for k, v in json.loads(r.read())["coins"].items():
                    prices[k.split(":")[1].lower()] = v["price"]
        except Exception as e:
            print(f"  {chain}: price fetch failed: {e}", file=sys.stderr)

    per_route, total, unpriced, capped = {}, 0.0, 0, 0
    for r, bal, dec in got:
        if not bal:
            continue
        p = prices.get(r["collateral"].lower())
        if p is None:
            unpriced += 1
            continue
        d = dec if dec is not None else (r.get("decimals") or 18)
        usd = bal / 10 ** d * p
        if usd > PRICE_CAP:
            capped += 1
            continue
        per_route[r["router"][2:].lower()] = usd
        total += usd

    return {"chain": chain, "routes_on_default": len(rows),
            "with_collateral": len(coll), "failed": len(failed),
            "unpriced": unpriced, "capped_out": capped,
            "total_usd": total, "per_route": per_route}


def main():
    out = {}
    for c in RPC:
        s = measure(c)
        out[c] = s
        top = sorted(s["per_route"].items(), key=lambda x: -x[1])[:1]
        print(f"  {c:<10} default={s['routes_on_default']:>4} "
              f"withColl={s['with_collateral']:>4} "
              f"failed={s['failed']:>2} unpriced={s['unpriced']:>3} "
              f"capped={s['capped_out']:>2}  "
              f"stock=${s['total_usd']:>14,.0f}"
              f"  top1={100*top[0][1]/s['total_usd'] if top and s['total_usd'] else 0:>5.1f}%",
              flush=True)

    tot = sum(v["total_usd"] for v in out.values())
    print(f"\n四链 default-ISM route 在险存量合计: ${tot:,.0f}")
    print("\n按存量的预算切分比例:")
    for c, v in out.items():
        print(f"  {c:<10} {100*v['total_usd']/tot if tot else 0:>6.2f}%")
    json.dump({c: {k: val for k, val in v.items() if k != "per_route"}
               for c, v in out.items()} | {"per_route": {c: v["per_route"] for c, v in out.items()}},
              open(f"{HERE}/data/stock_multi.json", "w"), indent=1)


if __name__ == "__main__":
    main()
