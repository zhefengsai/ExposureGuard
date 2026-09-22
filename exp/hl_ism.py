"""Read interchainSecurityModule() for every Hyperlane warp router on a chain.

A zero return means the router defers to the Mailbox default ISM -- which is
itself a shared root, and the strongest form of sharing.
"""
import concurrent.futures as cf
import json, sys, time, urllib.request, collections

RPC = {"ethereum": "https://ethereum-rpc.publicnode.com",
       "base": "https://base-rpc.publicnode.com",
       "arbitrum": "https://arbitrum-one-rpc.publicnode.com",
       "bsc": "https://bsc-rpc.publicnode.com"}
SEL_ISM = "0xde523cf3"          # interchainSecurityModule()
CHAIN = sys.argv[1] if len(sys.argv) > 1 else "ethereum"

rows = [r for r in json.load(open("data/hl_routers.json"))
        if r["chain"] == CHAIN and r["router"] and r["router"].startswith("0x")]
# de-dup routers that appear in several config files
uniq = {}
for r in rows:
    uniq.setdefault(r["router"].lower(), r)
rows = list(uniq.values())
print(f"{CHAIN}: {len(rows)} unique routers")


def one_call(args):
    cid, to, data, rpc = args
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
               "params": [{"to": to, "data": data}, "latest"]}
    req = urllib.request.Request(
        rpc, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "exposureguard-artifact/1.0"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return cid, json.loads(r.read()).get("result")
        except Exception:
            time.sleep(0.6)
    return cid, None


def batch_call(calls, rpc):
    out = {}
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for cid, v in ex.map(one_call, [(c, t, d, rpc) for c, t, d in calls]):
            out[cid] = v
    return out


calls = [(i, r["router"], SEL_ISM) for i, r in enumerate(rows)]
res = batch_call(calls, RPC[CHAIN])

by_ism = collections.defaultdict(list)
failed = 0
for i, r in enumerate(rows):
    v = res.get(i)
    if not v or len(v) < 66:
        failed += 1
        r["ism"] = None
        continue
    ism = "0x" + v[-40:]
    r["ism"] = ism
    by_ism[ism].append(r)

print(f"resolved: {len(rows)-failed}, failed: {failed}\n")
ZERO = "0x" + "0" * 40
print(f"{'ISM':<44}{'routes':>7}  families")
for ism, rs in sorted(by_ism.items(), key=lambda x: -len(x[1])):
    fams = sorted({r['family'] for r in rs})
    tag = "  <-- MAILBOX DEFAULT ISM" if ism == ZERO else ""
    print(f"{ism:<44}{len(rs):>7}  {', '.join(fams[:6])}"
          f"{' ...' if len(fams) > 6 else ''}{tag}")

json.dump(rows, open(f"data/hl_ism_{CHAIN}.json", "w"))
