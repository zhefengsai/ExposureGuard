"""Wormhole Token Bridge custody across every chain we can enumerate.

Audit rules apply: loud failures, coverage denominators, independent
cross-validation. Chains without a public indexer are NOT estimated -- they are
reported with DefiLlama as the stated source and marked unverified.
"""
import json, os, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

# Wormhole Token Bridge, from the official SDK constants
BRIDGE = {
    "ethereum": ("0x3ee18B2214AFF97000D974cf647E7C347E8fa585", "https://eth.blockscout.com"),
    "base":     ("0x8d2de8d2f73F1F4cAB472AC9A881C9b123C79627", "https://base.blockscout.com"),
    "arbitrum": ("0x0b2402144Bb366A632D14B83F244D2e0e21bD39c", "https://arbitrum.blockscout.com"),
    "polygon":  ("0x5a58505a96D1dbf8dF91cB21B54419FC36e93fdE", "https://polygon.blockscout.com"),
    "optimism": ("0x1D68124e65faFC907325e3EDbF8c4d84499DAa8b", "https://optimism.blockscout.com"),
}
LLAMA_KEY = {"ethereum": "ethereum", "base": "base", "arbitrum": "arbitrum",
             "polygon": "polygon", "optimism": "optimism"}
# chains we cannot enumerate with the tooling at hand
UNVERIFIED = ["Algorand", "Binance", "Solana", "Sui", "Near", "Avalanche",
              "Aptos", "Celo", "Klaytn"]


class FetchFailed(Exception):
    pass


def get(url, attempts=5, allow_redirect=True):
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "exposureguard-audit/1.0",
                              "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read())
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(0.7 * (i + 1))
    raise FetchFailed(last or "unknown")


def enumerate_chain(chain):
    addr, base = BRIDGE[chain]
    data = get(f"{base}/api/v2/addresses/{addr}/token-balances")
    holdings = []
    for e in data:
        t = e.get("token") or {}
        if (t.get("type") or "").upper().replace("-", "") != "ERC20":
            continue
        try:
            raw, dec = int(e.get("value") or 0), int(t.get("decimals") or 18)
        except (TypeError, ValueError):
            continue
        if raw == 0:
            continue
        holdings.append({
            "address": (t.get("address_hash") or t.get("address") or "").lower(),
            "symbol": t.get("symbol"), "amount": raw / 10 ** dec,
            "indexer_rate": float(t["exchange_rate"]) if t.get("exchange_rate") else None,
        })
    addrs = sorted({h["address"] for h in holdings if h["address"]})
    px, pfx = {}, LLAMA_KEY[chain]
    for i in range(0, len(addrs), 40):
        q = ",".join(f"{pfx}:" + a for a in addrs[i:i + 40])
        try:
            d = get("https://coins.llama.fi/prices/current/" + q, attempts=3)
            for k, v in d["coins"].items():
                px[k.split(":")[1].lower()] = v["price"]
        except FetchFailed:
            print(f"  [{chain}] price chunk {i} failed", file=sys.stderr)
    # Two figures, because the price source matters. DefiLlama-priced is the
    # conservative, cross-validatable number; falling back to the indexer's own
    # rate adds tokens DefiLlama refuses to price, which is exactly where an
    # unverifiable valuation can creep in (Arbitrum's LUA was 62% of that chain
    # on an indexer-only rate).
    strict, loose, unpriced = [], [], 0
    for h in holdings:
        p = px.get(h["address"])
        if p is not None:
            strict.append((h["symbol"], h["amount"] * p))
            loose.append((h["symbol"], h["amount"] * p))
        elif h["indexer_rate"] is not None:
            loose.append((h["symbol"], h["amount"] * h["indexer_rate"]))
        else:
            unpriced += 1
    strict.sort(key=lambda r: -r[1])
    loose.sort(key=lambda r: -r[1])
    return {"holdings": len(holdings), "priced_strict": len(strict),
            "priced_loose": len(loose), "unpriced": unpriced,
            "usd_strict": sum(r[1] for r in strict),
            "usd_loose": sum(r[1] for r in loose), "top": strict[:3]}


def main():
    llama = get("https://api.llama.fi/protocol/wormhole")["currentChainTvls"]
    NAME = {"ethereum": "Ethereum", "base": "Base", "arbitrum": "Arbitrum",
            "polygon": "Polygon", "optimism": "Optimism"}

    print("独立枚举（Blockscout + DefiLlama 计价）\n")
    print(f"{'chain':<10}{'持仓':>7}{'严格USD':>16}{'比值':>7}"
          f"{'宽松USD':>16}{'比值':>7}{'DefiLlama':>15}")
    strict_tot = loose_tot = 0.0
    out = {}
    for c in BRIDGE:
        try:
            r = enumerate_chain(c)
        except FetchFailed as e:
            print(f"{c:<10}  枚举失败: {str(e)[:60]}", file=sys.stderr)
            continue
        ext = llama.get(NAME[c], 0)
        strict_tot += r["usd_strict"]
        loose_tot += r["usd_loose"]
        out[c] = r | {"defillama": ext}
        print(f"{c:<10}{r['holdings']:>7}{r['usd_strict']:>16,.0f}"
              f"{(r['usd_strict']/ext if ext else 0):>6.2f}x"
              f"{r['usd_loose']:>16,.0f}"
              f"{(r['usd_loose']/ext if ext else 0):>6.2f}x{ext:>15,.0f}")

    print(f"\n{'合计':<10}{'':>7}{strict_tot:>16,.0f}{'':>6}{loose_tot:>16,.0f}")
    print("  严格 = 仅 DefiLlama 计价（可交叉验证）")
    print("  宽松 = 允许回退到索引器报价（含 DefiLlama 拒绝定价的代币）")
    verified = strict_tot

    print(f"\n未能独立枚举的链（来源：DefiLlama，**未验证**）")
    unv = 0.0
    for k in UNVERIFIED:
        v = llama.get(k, 0)
        if v > 0:
            unv += v
            print(f"  {k:<12}${v:>15,.0f}   {'无公共索引器' if k in ('Binance','Avalanche') else '非 EVM'}")
    print(f"  {'小计':<12}${unv:>15,.0f}")

    tot_llama = sum(v for k, v in llama.items() if not k.endswith("-borrowed"))
    print(f"\n--- 覆盖 ---")
    print(f"  独立枚举覆盖 DefiLlama 总额的: "
          f"{100*sum(out[c]['defillama'] for c in out)/tot_llama:.1f}%")
    print(f"  未验证部分:                   {100*unv/tot_llama:.1f}%")
    print(f"\n  在险价值（枚举口径 + 未验证外部来源）: "
          f"${verified + unv:,.0f}")
    json.dump({"verified": out, "unverified_usd": unv,
               "verified_usd_strict": strict_tot, "verified_usd_loose": loose_tot,
               "llama_total": tot_llama},
              open(f"{HERE}/data/wh_custody_multi.json", "w"), indent=1)


if __name__ == "__main__":
    main()
