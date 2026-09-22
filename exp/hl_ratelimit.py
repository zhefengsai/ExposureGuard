"""Probe every distinct ISM for the RateLimited interface, using verified selectors.

Selectors confirmed with `cast sig`:
  maxCapacity()  0x59b6a0c9
  filledLevel()  0x49a3e3d0
  refillRate()   0x435635a1
  recipient()    0x66d003ac   <-- immutable single-route binding
"""
import concurrent.futures as cf
import collections, json, time, urllib.request

RPC = {"ethereum": "https://ethereum-rpc.publicnode.com",
       "base": "https://base-rpc.publicnode.com",
       "arbitrum": "https://arbitrum-one-rpc.publicnode.com",
       "bsc": "https://bsc-rpc.publicnode.com"}
SEL = {"maxCapacity": "0x59b6a0c9", "filledLevel": "0x49a3e3d0",
       "refillRate": "0x435635a1", "recipient": "0x66d003ac"}
ZERO = "0x" + "0" * 40


def call(rpc, to, data):
    p = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
         "params": [{"to": to, "data": data}, "latest"]}
    r = urllib.request.Request(rpc, data=json.dumps(p).encode(),
                               headers={"Content-Type": "application/json",
                                        "User-Agent": "exposureguard-artifact/1.0"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(r, timeout=25) as x:
                return json.loads(x.read()).get("result")
        except Exception:
            time.sleep(0.4)
    return None


def probe(args):
    chain, ism = args
    rpc = RPC[chain]
    out = {"chain": chain, "ism": ism}
    for k, s in SEL.items():
        v = call(rpc, ism, s)
        out[k] = v if v not in (None, "0x") else None
    out["is_ratelimited"] = out["maxCapacity"] is not None
    return out


targets = []
for chain in RPC:
    try:
        rows = json.load(open(f"data/hl_ism_{chain}.json"))
    except FileNotFoundError:
        continue
    for i in sorted({r["ism"] for r in rows if r.get("ism") and r["ism"] != ZERO}):
        targets.append((chain, i))
    # the Mailbox default ISM of each chain is also in scope
print(f"distinct non-default ISMs to probe: {len(targets)}")

res = list(cf.ThreadPoolExecutor(max_workers=12).map(probe, targets))
hits = [r for r in res if r["is_ratelimited"]]

by_chain = collections.Counter(r["chain"] for r in res)
print("probed per chain:", dict(by_chain))
print(f"\nISMs exposing RateLimited interface: {len(hits)} / {len(res)}")
for h in hits:
    cap = int(h["maxCapacity"], 16) if h["maxCapacity"] else 0
    fil = int(h["filledLevel"], 16) if h["filledLevel"] else 0
    rec = "0x" + h["recipient"][-40:] if h["recipient"] else "none"
    print(f"  {h['chain']:<10}{h['ism']}  cap={cap}  filled={fil}  recipient={rec}")

json.dump(res, open("data/hl_ratelimit.json", "w"), indent=1)
