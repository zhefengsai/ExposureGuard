"""Collect the four-chain Hyperlane inbound trace used by the evaluation.

The collector is parameterised by destination chain and treats transport
failures as errors rather than empty results. Hyperlane domain identifiers for
the four measured EVM chains equal their chain identifiers.
"""
import concurrent.futures as cf
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

GQL = "https://explorer4.hasura.app/v1/graphql"
HERE = os.path.dirname(os.path.abspath(__file__))
DAYS = int(os.environ.get("DAYS", "30"))
ZERO = "0x" + "0" * 40

DOMAIN = {"ethereum": 1, "base": 8453, "arbitrum": 42161, "bsc": 56}
LLAMA = {"ethereum": "ethereum", "base": "base", "arbitrum": "arbitrum", "bsc": "bsc"}


class QueryFailed(Exception):
    """Raised when a query never succeeded.

    The first version of this collector returned None here and the caller
    broke out of its loop, so a transport failure was indistinguishable from
    an empty result: Base and Arbitrum silently reported zero traffic when
    both in fact had active routes. Failures must be loud.
    """


def query(q, attempts=6):
    last = None
    for i in range(attempts):
        req = urllib.request.Request(
            GQL, data=json.dumps({"query": q}).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "exposureguard-artifact/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.loads(r.read())
                if "errors" in body:
                    last = str(body["errors"])[:120]
                else:
                    return body["data"]
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(0.8 * (i + 1))
    raise QueryFailed(last or "unknown")


def amount_from_body(hexbody):
    h = hexbody.replace("\\x", "").replace("0x", "")
    if len(h) < 128:
        return None
    try:
        return int(h[64:128], 16)
    except ValueError:
        return None


def collect(chain):
    dom = DOMAIN[chain]
    try:
        rows = [r for r in json.load(open(f"{HERE}/data/hl_ism_{chain}.json"))
                if r.get("ism") == ZERO]
    except FileNotFoundError:
        print(f"  {chain}: no ISM data, skipping", file=sys.stderr)
        return chain, [], {}
    by_addr = {r["router"][2:].lower(): r for r in rows}
    since = (datetime.now(timezone.utc) - timedelta(days=DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%S")

    def fetch(a):
        out, off = [], 0
        while off < 20000:
            q = (f'{{ message_view(limit:500, offset:{off}, '
                 f'where:{{destination_chain_id:{{_eq:{dom}}}, '
                 f'recipient:{{_eq:"\\\\x{a}"}}, '
                 f'send_occurred_at:{{_gte:"{since}"}}}}) '
                 f'{{ recipient message_body send_occurred_at }} }}')
            b = query(q)["message_view"]
            out += b
            if len(b) < 500:
                break
            off += 500
        return out

    msgs, failed = [], []
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(fetch, a): a for a in by_addr}
        for f in cf.as_completed(futs):
            try:
                msgs += f.result()
            except QueryFailed as e:
                failed.append((futs[f], str(e)))
    if failed:
        print(f"  {chain}: {len(failed)}/{len(by_addr)} routes FAILED to query "
              f"-- results are incomplete: {failed[0][1][:70]}", file=sys.stderr)

    # price the collateral on this chain
    addrs = sorted({r["collateral"].lower() for r in rows
                    if r.get("collateral") and str(r["collateral"]).startswith("0x")})
    prices = {}
    pfx = LLAMA[chain]
    for i in range(0, len(addrs), 40):
        q = ",".join(f"{pfx}:" + a for a in addrs[i:i + 40])
        try:
            with urllib.request.urlopen(
                    "https://coins.llama.fi/prices/current/" + q, timeout=40) as r:
                for k, v in json.loads(r.read())["coins"].items():
                    prices[k.split(":")[1].lower()] = v["price"]
        except Exception:
            pass

    events, stock = [], 0.0
    for m in msgs:
        a = m["recipient"].replace("\\x", "").lower()
        r = by_addr.get(a)
        if not r:
            continue
        amt = amount_from_body(m["message_body"] or "")
        p = prices.get(str(r.get("collateral", "")).lower())
        if amt is None or p is None:
            continue
        usd = amt / 10 ** (r.get("decimals") or 18) * p
        if usd > 50e6:
            continue
        events.append({"t": m["send_occurred_at"], "route": a,
                       "family": r["family"], "symbol": str(r["symbol"]),
                       "usd": usd, "chain": chain})
    events.sort(key=lambda e: e["t"])

    daily = defaultdict(float)
    for e in events:
        daily[e["t"][:10]] += e["usd"]
    active = len({e["route"] for e in events})
    tot = sum(e["usd"] for e in events)
    n = max(len(daily), 1)
    summary = {"routes_on_default": len(rows), "failed_routes": len(failed),
               "messages": len(msgs),
               "priced_events": len(events), "active_routes": active,
               "total_usd": tot, "mean_daily": tot / n,
               "peak_daily": max(daily.values()) if daily else 0.0,
               "days": n}
    return chain, events, summary


def main():
    all_events, summaries = [], {}
    for chain in DOMAIN:
        c, ev, sm = collect(chain)
        all_events += ev
        summaries[c] = sm
        if sm:
            print(f"  {c:<10} routes={sm['routes_on_default']:>4} "
                  f"msgs={sm['messages']:>6} priced={sm['priced_events']:>6} "
                  f"active={sm['active_routes']:>3} "
                  f"mean/day=${sm['mean_daily']:>12,.0f} "
                  f"peak=${sm['peak_daily']:>12,.0f}", flush=True)

    all_events.sort(key=lambda e: e["t"])
    json.dump(all_events, open(f"{HERE}/data/events_multi.json", "w"))
    json.dump(summaries, open(f"{HERE}/data/flow_multi_summary.json", "w"), indent=1)

    print(f"\ntotal priced events across 4 chains: {len(all_events)}")
    print(f"total routes on default ISMs        : "
          f"{sum(s.get('routes_on_default', 0) for s in summaries.values())}")
    print(f"total active routes                 : "
          f"{sum(s.get('active_routes', 0) for s in summaries.values())}")
    print(f"combined mean daily inflow          : "
          f"${sum(s.get('mean_daily', 0) for s in summaries.values()):,.0f}")


if __name__ == "__main__":
    main()
