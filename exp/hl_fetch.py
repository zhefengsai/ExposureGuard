"""Fetch all Hyperlane warp-route configs, extract EVM routers per chain."""
import concurrent.futures as cf
import json, urllib.request, yaml

RAW = "https://raw.githubusercontent.com/hyperlane-xyz/hyperlane-registry/main/"
tree = json.load(open("/tmp/hl_tree.json"))["tree"]
paths = [e["path"] for e in tree
         if e["path"].startswith("deployments/warp_routes")
         and e["path"].endswith("-config.yaml")]
print(f"config files: {len(paths)}")

def get(p):
    try:
        with urllib.request.urlopen(RAW + p, timeout=40) as r:
            return p, yaml.safe_load(r.read())
    except Exception as e:
        return p, None

rows = []
with cf.ThreadPoolExecutor(max_workers=16) as ex:
    for p, doc in ex.map(get, paths):
        if not doc or "tokens" not in doc:
            continue
        family = p.split("/")[2]
        for t in doc["tokens"]:
            rows.append({
                "family": family, "file": p,
                "chain": t.get("chainName"),
                "router": t.get("addressOrDenom"),
                "collateral": t.get("collateralAddressOrDenom"),
                "standard": t.get("standard"),
                "symbol": t.get("symbol"),
                "decimals": t.get("decimals"),
            })

json.dump(rows, open("data/hl_routers.json", "w"))
print(f"router entries: {len(rows)}")
import collections
c = collections.Counter(r["chain"] for r in rows)
print("top chains:", c.most_common(10))
