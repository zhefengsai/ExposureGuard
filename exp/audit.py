"""Measurement completeness audit.

Four times in this project a silent failure produced a plausible but wrong
headline: a collector that returned zero on a transport error understated flow
by 2x, hid Base and Arbitrum entirely, hid BSC's synthetic side, and from that
last one produced a false "the mechanism is not universal" conclusion that was
wrong by ~1300x.

The common shape was always `except: pass` or `break` on failure, which makes
"the request failed" indistinguishable from "there is no data". This script
re-derives every headline number under three rules:

  1. every call either succeeds or is counted as a failure -- never silent
  2. every number is reported with its coverage denominator
  3. every number that can be checked against an independent source is

Nothing here should be trusted more than its reported coverage.
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

RPC = {"ethereum": "https://ethereum-rpc.publicnode.com",
       "base": "https://base-rpc.publicnode.com",
       "arbitrum": "https://arbitrum-one-rpc.publicnode.com",
       "bsc": "https://bsc-rpc.publicnode.com"}
LLAMA = {"ethereum": "ethereum", "base": "base",
         "arbitrum": "arbitrum", "bsc": "bsc"}

BAL, DEC, TS = "0x70a08231", "0x313ce567", "0x18160ddd"
BLUE = {"weth", "wbtc", "usdc", "usdt", "dai", "wsteth", "cbbtc",
        "eth", "btc", "steth", "usds", "usde"}


class CallFailed(Exception):
    pass


def rpc_call(rpc, to, data, attempts=5):
    last = None
    for i in range(attempts):
        payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                   "params": [{"to": to, "data": data}, "latest"]}
        req = urllib.request.Request(
            rpc, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "exposureguard-audit/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read())
                if "error" in body:
                    last = str(body["error"])[:80]
                else:
                    return body.get("result")
        except Exception as e:
            last = type(e).__name__
        time.sleep(0.4 * (i + 1))
    raise CallFailed(last or "unknown")


def prices(chain, addrs):
    """Returns (map, n_requested, n_returned). Never silently empty."""
    out, pfx = {}, LLAMA[chain]
    for i in range(0, len(addrs), 40):
        chunk = addrs[i:i + 40]
        q = ",".join(f"{pfx}:" + a for a in chunk)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(
                        "https://coins.llama.fi/prices/current/" + q,
                        timeout=40) as r:
                    for k, v in json.loads(r.read())["coins"].items():
                        out[k.split(":")[1].lower()] = v["price"]
                break
            except Exception:
                time.sleep(0.6 * (attempt + 1))
        else:
            print(f"  [audit] price chunk failed for {chain}", file=sys.stderr)
    return out, len(addrs), len(out)


def stock(chain):
    """Value at risk on `chain`, both sides, with full coverage accounting."""
    rows = [r for r in json.load(open(f"{HERE}/data/hl_ism_{chain}.json"))
            if r.get("ism") == ZERO]
    coll = [r for r in rows
            if r.get("collateral") and str(r["collateral"]).startswith("0x")]
    syn = [r for r in rows
           if str(r.get("standard", "")).startswith("EvmHypSynthetic")]
    other = [r for r in rows if r not in coll and r not in syn]

    def probe(args):
        r, kind = args
        tok = r["collateral"] if kind == "coll" else r["router"]
        data = (BAL + "0" * 24 + r["router"][2:].lower()) if kind == "coll" else TS
        amt = rpc_call(RPC[chain], tok, data)
        dec = rpc_call(RPC[chain], tok, DEC)
        return r, kind, (int(amt, 16) if amt and amt != "0x" else 0), \
            (int(dec, 16) if dec and dec != "0x" else None)

    tasks = [(r, "coll") for r in coll] + [(r, "syn") for r in syn]
    got, failed = [], []
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(probe, t): t for t in tasks}
        for f in cf.as_completed(futs):
            try:
                got.append(f.result())
            except CallFailed as e:
                failed.append((futs[f][0]["router"], futs[f][1], str(e)))

    addrs = sorted({(r["collateral"] if k == "coll" else r["router"]).lower()
                    for r, k, _, _ in got})
    px, n_req, n_ret = prices(chain, addrs)

    res = {"coll": 0.0, "syn": 0.0, "coll_blue": 0.0, "syn_blue": 0.0}
    priced = unpriced = 0
    top = []
    for r, k, amt, dec in got:
        if not amt:
            continue
        key = (r["collateral"] if k == "coll" else r["router"]).lower()
        p = px.get(key)
        if p is None:
            unpriced += 1
            continue
        d = dec if dec is not None else (r.get("decimals") or 18)
        v = amt / 10 ** d * p
        priced += 1
        res[k] += v
        if str(r.get("symbol", "")).lower() in BLUE:
            res[k + "_blue"] += v
        top.append((str(r.get("symbol")), k, v))
    top.sort(key=lambda x: -x[2])

    total = res["coll"] + res["syn"]
    return {
        "routes_default": len(rows),
        "collateral_routes": len(coll), "synthetic_routes": len(syn),
        "unclassified_routes": len(other),
        "probed": len(tasks), "rpc_ok": len(got), "rpc_failed": len(failed),
        "price_requested": n_req, "price_returned": n_ret,
        "priced": priced, "unpriced": unpriced,
        "usd_collateral": res["coll"], "usd_synthetic": res["syn"],
        "usd_total": total,
        "usd_bluechip_only": res["coll_blue"] + res["syn_blue"],
        "rpc_coverage": len(got) / max(len(tasks), 1),
        "price_coverage": priced / max(len(got), 1),
        "route_coverage": len(tasks) / max(len(rows), 1),
        "top3": top[:3],
    }


def llama_hyperlane():
    """Independent cross-check: DefiLlama's own per-chain TVL for Hyperlane."""
    try:
        with urllib.request.urlopen(
                "https://api.llama.fi/protocol/hyperlane", timeout=40) as r:
            d = json.load(r)
        return {k.lower(): v for k, v in (d.get("currentChainTvls") or {}).items()}
    except Exception as e:
        print(f"  [audit] DefiLlama cross-check unavailable: {e}", file=sys.stderr)
        return {}


def main():
    print("=" * 96)
    print("测量完整性审计 —— 每个数字都带覆盖率分母")
    print("=" * 96)

    llama = llama_hyperlane()
    NAME = {"ethereum": "ethereum", "base": "base",
            "arbitrum": "arbitrum", "bsc": "binance"}

    rows = {}
    print(f"\n{'chain':<10}{'route':>7}{'探测':>6}{'RPC ok':>8}{'失败':>6}"
          f"{'有价格':>8}{'路由覆盖':>10}{'RPC覆盖':>9}{'计价覆盖':>10}")
    for c in RPC:
        s = stock(c)
        rows[c] = s
        print(f"{c:<10}{s['routes_default']:>7}{s['probed']:>6}{s['rpc_ok']:>8}"
              f"{s['rpc_failed']:>6}{s['priced']:>8}"
              f"{100*s['route_coverage']:>9.0f}%{100*s['rpc_coverage']:>8.0f}%"
              f"{100*s['price_coverage']:>9.0f}%")

    print(f"\n{'chain':<10}{'抵押侧':>15}{'合成侧':>17}{'合计':>17}{'仅蓝筹':>15}")
    for c, s in rows.items():
        print(f"{c:<10}{s['usd_collateral']:>15,.0f}{s['usd_synthetic']:>17,.0f}"
              f"{s['usd_total']:>17,.0f}{s['usd_bluechip_only']:>15,.0f}")
    tot = sum(s["usd_total"] for s in rows.values())
    blue = sum(s["usd_bluechip_only"] for s in rows.values())
    print(f"{'合计':<10}{'':>15}{'':>17}{tot:>17,.0f}{blue:>15,.0f}")

    print("\n--- 独立交叉验证：DefiLlama 报的 Hyperlane 各链 TVL ---")
    print(f"{'chain':<10}{'本次实测':>17}{'DefiLlama':>15}{'比值':>9}")
    for c, s in rows.items():
        ext = llama.get(NAME[c], 0)
        r = s["usd_total"] / ext if ext else 0
        print(f"{c:<10}{s['usd_total']:>17,.0f}{ext:>15,.0f}{r:>8.2f}x")
    print("  注：DefiLlama 统计口径含非 default-ISM route，故比值 <1 属预期；"
          "\n      比值 >1 说明我们把某些不该算的算进来了，须解释。")

    print("\n--- 各链头部持仓（用于判断集中度与估值风险）---")
    for c, s in rows.items():
        t = ", ".join(f"{sym}({kind}) ${v:,.0f}" for sym, kind, v in s["top3"])
        share = (s["top3"][0][2] / s["usd_total"] * 100) if s["top3"] and s["usd_total"] else 0
        print(f"  {c:<10} top1 占 {share:>5.1f}%   {t}")

    json.dump(rows, open(f"{HERE}/data/audit.json", "w"), indent=1, default=str)
    print(f"\n写入 data/audit.json")


if __name__ == "__main__":
    main()
