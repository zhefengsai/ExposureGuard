#!/usr/bin/env python3
"""ExposureGuard control plane: recommend, audit, and shadow-replay configs.

The on-chain module is only the enforcement point.  This tool closes the
operational loop around it using the same frozen chain-log events as the paper:

  chain logs -> recommendation -> audit -> on-chain enforcement -> remeasure

It deliberately does not send governance transactions.  ``recommend`` emits a
reviewable configuration; ``audit`` rejects internally inconsistent or stale
configurations; ``shadow`` evaluates daily recommendations without changing
production state.  Thus historical shadow results are replay, not a claim of a
live deployment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CHAINS = ("ethereum", "base", "arbitrum", "bsc")
DAY = 86400.0


def load_events(path: Path = DATA / "events_multi.json") -> list[dict]:
    rows = json.loads(path.read_text())
    out = []
    for row in rows:
        if row.get("chain") not in CHAINS or row.get("usd") is None:
            continue
        r = dict(row)
        r["ts"] = datetime.strptime(r["t"], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc).timestamp()
        r["route"] = r["route"].lower().removeprefix("0x")
        out.append(r)
    return sorted(out, key=lambda x: x["ts"])


def default_routes(chain: str) -> set[str]:
    path = DATA / f"hl_ism_{chain}.json"
    if not path.exists():
        return set()
    zero = "0x" + "0" * 40
    return {
        r["router"].lower().removeprefix("0x")
        for r in json.loads(path.read_text()) if r.get("ism") == zero
    }


def daily_peak(rows: list[dict]) -> float:
    totals: dict[str, float] = defaultdict(float)
    for r in rows:
        totals[r["t"][:10]] += float(r["usd"])
    return max(totals.values(), default=0.0)


def trace_provenance(rows: list[dict]) -> dict:
    """Hash the exact normalized records used by a recommendation.

    ``ts`` is derived from ``t`` while loading, so it is excluded.  Sorting
    canonical JSON records makes the digest independent of collector append
    order while retaining duplicate records if they ever occur.
    """
    encoded = []
    for row in rows:
        normalized = {k: v for k, v in row.items() if k != "ts"}
        encoded.append(json.dumps(normalized, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False))
    encoded.sort()
    payload = ("\n".join(encoded) + "\n").encode()
    by_chain = {chain: sum(r["chain"] == chain for r in rows)
                for chain in CHAINS}
    return {
        "trace_sha256": hashlib.sha256(payload).hexdigest(),
        "trace_records": len(rows),
        "trace_records_by_chain": by_chain,
        "trace_first_at": min(r["t"] for r in rows),
        "trace_last_at": max(r["t"] for r in rows),
    }


def recommend(rows: list[dict], *, alpha: float, headroom: float,
              lookback_days: int, as_of: float | None = None) -> dict:
    if not rows:
        raise ValueError("cannot recommend from an empty trace")
    as_of = as_of if as_of is not None else rows[-1]["ts"] + 1
    start = as_of - lookback_days * DAY
    train = [r for r in rows if start <= r["ts"] < as_of]
    if not train:
        raise ValueError("lookback contains no events")

    chains = {}
    total_budget = 0.0
    for chain in CHAINS:
        cr = [r for r in train if r["chain"] == chain]
        peak = daily_peak(cr)
        budget = math.ceil(peak * headroom / 1000.0) * 1000.0 if peak else 0.0
        usage: dict[str, float] = defaultdict(float)
        for r in cr:
            usage[r["route"]] += float(r["usd"])
        total = sum(usage.values())
        universe = default_routes(chain) | set(usage)
        weights = {
            route: (usage.get(route, 0.0) / total if total else 0.0)
            for route in sorted(universe)
        }
        chains[chain] = {
            "budget_usd_per_day": budget,
            "measured_peak_usd_per_day": peak,
            "events": len(cr),
            "active_routes": sum(v > 0 for v in usage.values()),
            "covered_routes": len(universe),
            "floor_weights": weights,
        }
        total_budget += budget

    provenance = trace_provenance(train)
    return {
        "schema": 1,
        "generated_at": datetime.fromtimestamp(as_of, timezone.utc).isoformat(),
        **provenance,
        "window_days": lookback_days,
        "alpha": alpha,
        "headroom": headroom,
        "total_budget_usd_per_day": total_budget,
        "chains": chains,
    }


def audit(config: dict, *, max_age_days: int, now: float | None = None,
          deployment: dict | None = None, require_operational: bool = False,
          max_price_age_days: int = 7) -> dict:
    """Audit an offline recommendation and, when supplied, deployment state.

    ``deployment`` is deliberately an input snapshot rather than an RPC call:
    the snapshot collector and this deterministic validator are separate fault
    domains.  A production deployment should use ``require_operational=True``;
    historical replay may validate only the offline recommendation.
    """
    errors, warnings = [], []
    if not 0 <= float(config.get("alpha", -1)) <= 1:
        errors.append("alpha outside [0,1]")
    if float(config.get("headroom", 0)) < 1:
        errors.append("headroom below 1")
    summed = 0.0
    for chain in CHAINS:
        c = config.get("chains", {}).get(chain)
        if c is None:
            errors.append(f"missing chain {chain}")
            continue
        budget = float(c.get("budget_usd_per_day", -1))
        peak = float(c.get("measured_peak_usd_per_day", -1))
        if budget < peak:
            errors.append(f"{chain}: budget below measured peak")
        weights = c.get("floor_weights", {})
        wsum = sum(float(v) for v in weights.values())
        if wsum > 1.000001:
            errors.append(f"{chain}: floor weights sum to {wsum:.6f} > 1")
        if any(float(v) < 0 for v in weights.values()):
            errors.append(f"{chain}: negative floor weight")
        if c.get("active_routes", 0) and wsum < 0.999:
            warnings.append(f"{chain}: active floor weights sum to {wsum:.6f}")
        summed += max(budget, 0.0)
    if abs(summed - float(config.get("total_budget_usd_per_day", -1))) > 1.0:
        errors.append("total budget does not equal sum of chain budgets")

    generated = datetime.fromisoformat(config["generated_at"]).timestamp()
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    age_days = max(0.0, (now - generated) / DAY)
    if age_days > max_age_days:
        errors.append(f"configuration is stale ({age_days:.1f} days)")
    if deployment is None:
        msg = "operational snapshot absent: price, coverage, and installation unchecked"
        (errors if require_operational else warnings).append(msg)
    else:
        snap_time = datetime.fromisoformat(deployment["observed_at"]).timestamp()
        snap_age = max(0.0, ((now if now is not None else snap_time) - snap_time) / DAY)
        if snap_age > max_age_days:
            errors.append(f"deployment snapshot is stale ({snap_age:.1f} days)")
        for chain in CHAINS:
            expected = config.get("chains", {}).get(chain, {})
            observed = deployment.get("chains", {}).get(chain)
            if observed is None:
                errors.append(f"{chain}: missing operational snapshot")
                continue
            if not observed.get("guard_installed", False):
                errors.append(f"{chain}: guard is not installed")
            threshold = int(observed.get("aggregation_threshold", -1))
            modules = int(observed.get("aggregation_module_count", -1))
            if threshold != modules or modules < 2:
                errors.append(f"{chain}: unsafe aggregation threshold {threshold}/{modules}")
            covered = {str(x).lower().removeprefix("0x")
                       for x in observed.get("covered_routes", [])}
            required = set(expected.get("floor_weights", {}))
            missing = required - covered
            if missing:
                errors.append(f"{chain}: {len(missing)} configured routes bypass coverage")
            policies = {str(k).lower().removeprefix("0x"): v for k, v in
                        observed.get("recipient_policy", {}).items()}
            for route in required:
                policy = policies.get(route)
                if policy is None:
                    errors.append(f"{chain}: route {route} has no recipient policy")
                    continue
                kind = policy.get("classification")
                if kind not in ("metered", "exempt", "reject"):
                    errors.append(f"{chain}: route {route} has invalid classification")
                    continue
                if kind == "exempt":
                    warnings.append(f"{chain}: route {route} is exempt from the value bound")
                if kind != "metered":
                    continue
                price = float(policy.get("price_usd", 0))
                reference = float(policy.get("reference_upper_usd", 0))
                if price <= 0 or reference <= 0 or price + 1e-12 < reference:
                    errors.append(f"{chain}: route {route} lacks a conservative upper price")
                try:
                    priced_at = datetime.fromisoformat(policy["price_observed_at"]).timestamp()
                    price_age = max(0.0, ((now if now is not None else snap_time) - priced_at) / DAY)
                    if price_age > max_price_age_days:
                        errors.append(f"{chain}: route {route} price is stale ({price_age:.1f} days)")
                except (KeyError, TypeError, ValueError):
                    errors.append(f"{chain}: route {route} price has no valid timestamp")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "age_days": age_days}


class Bucket:
    def __init__(self, cap: float, t: float):
        self.cap, self.level, self.t = cap, cap, t

    def refill(self, t: float) -> None:
        self.level = min(self.cap, self.level + max(0.0, t - self.t) * self.cap / DAY)
        self.t = t

    def take_atomic(self, t: float, need: float) -> bool:
        self.refill(t)
        if self.level + 1e-9 < need:
            return False
        self.level -= need
        return True


def replay_day(rows: list[dict], cfg: dict) -> tuple[int, float]:
    if not rows:
        return 0, 0.0
    deferred = 0
    deferred_value = 0.0
    state = {}
    for chain in CHAINS:
        c = cfg["chains"][chain]
        b = float(c["budget_usd_per_day"])
        weights = c["floor_weights"]
        t0 = rows[0]["ts"]
        floors = {r: Bucket(cfg["alpha"] * b * float(w), t0)
                  for r, w in weights.items()}
        state[chain] = (floors, Bucket((1 - cfg["alpha"]) * b, t0))
    for e in rows:
        floors, surplus = state[e["chain"]]
        floor = floors.setdefault(e["route"], Bucket(0.0, e["ts"]))
        need = float(e["usd"])
        floor.refill(e["ts"])
        surplus.refill(e["ts"])
        available = floor.level + surplus.level
        if available + 1e-9 < need:
            deferred += 1
            deferred_value += need
            continue
        from_floor = min(floor.level, need)
        floor.level -= from_floor
        surplus.level -= need - from_floor
    return deferred, deferred_value


def l1_weight_churn(a: dict, b: dict) -> float:
    keys = set(a) | set(b)
    return 0.5 * sum(abs(float(a.get(k, 0)) - float(b.get(k, 0))) for k in keys)


def shadow(rows: list[dict], *, lookback: int, alpha: float,
           headroom: float) -> dict:
    first_day = datetime.fromtimestamp(rows[0]["ts"], timezone.utc).date()
    last_day = datetime.fromtimestamp(rows[-1]["ts"], timezone.utc).date()
    days = (last_day - first_day).days + 1
    results, previous = [], None
    for offset in range(lookback, days):
        day = first_day.fromordinal(first_day.toordinal() + offset)
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()
        cfg = recommend(rows, alpha=alpha, headroom=headroom,
                        lookback_days=lookback, as_of=start)
        today = [r for r in rows if start <= r["ts"] < start + DAY]
        deferred, value = replay_day(today, cfg)
        budget_churn = None
        weight_churn = None
        if previous:
            old_b = previous["total_budget_usd_per_day"]
            budget_churn = ((cfg["total_budget_usd_per_day"] - old_b) / old_b
                            if old_b else 0.0)
            weight_churn = sum(
                l1_weight_churn(previous["chains"][c]["floor_weights"],
                                cfg["chains"][c]["floor_weights"])
                for c in CHAINS) / len(CHAINS)
        results.append({
            "date": day.isoformat(), "messages": len(today),
            "deferred": deferred, "deferred_value_usd": value,
            "budget_usd_per_day": cfg["total_budget_usd_per_day"],
            "budget_change_fraction": budget_churn,
            "mean_weight_l1_churn": weight_churn,
        })
        previous = cfg
    nonempty = [r for r in results if r["messages"]]
    changes = [abs(r["budget_change_fraction"]) for r in results
               if r["budget_change_fraction"] is not None]
    wchanges = [r["mean_weight_l1_churn"] for r in results
                if r["mean_weight_l1_churn"] is not None]
    return {
        "mode": "historical_daily_shadow_replay",
        "live_deployment": False,
        "lookback_days": lookback,
        "alpha": alpha,
        "headroom": headroom,
        "shadow_days": len(results),
        "days_with_messages": len(nonempty),
        "messages": sum(r["messages"] for r in results),
        "deferred": sum(r["deferred"] for r in results),
        "deferral_rate": (sum(r["deferred"] for r in results) /
                          max(1, sum(r["messages"] for r in results))),
        "max_abs_daily_budget_change": max(changes, default=0.0),
        "mean_abs_daily_budget_change": sum(changes) / max(1, len(changes)),
        "mean_daily_weight_l1_churn": sum(wchanges) / max(1, len(wchanges)),
        "daily": results,
    }


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")
    print(f"wrote {path}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("recommend", "shadow"):
        q = sub.add_parser(name)
        q.add_argument("--lookback-days", type=int, default=60)
        q.add_argument("--alpha", type=float, default=0.8)
        q.add_argument("--headroom", type=float, default=1.07)
    q = sub.add_parser("audit")
    q.add_argument("--config", type=Path, default=DATA / "controller_config.json")
    q.add_argument("--max-age-days", type=int, default=30)
    q.add_argument("--deployment", type=Path,
                   help="captured on-chain/price snapshot to validate")
    q.add_argument("--require-operational", action="store_true",
                   help="fail rather than warn when no deployment snapshot is supplied")
    q.add_argument("--max-price-age-days", type=int, default=7)
    args = p.parse_args()
    rows = load_events()
    if args.cmd == "recommend":
        cfg = recommend(rows, alpha=args.alpha, headroom=args.headroom,
                        lookback_days=args.lookback_days)
        write_json(DATA / "controller_config.json", cfg)
        result = audit(cfg, max_age_days=30,
                       now=datetime.fromisoformat(cfg["generated_at"]).timestamp())
        if not result["ok"]:
            raise SystemExit(json.dumps(result, indent=2))
    elif args.cmd == "audit":
        deployment = (json.loads(args.deployment.read_text())
                      if args.deployment else None)
        result = audit(json.loads(args.config.read_text()),
                       max_age_days=args.max_age_days, deployment=deployment,
                       require_operational=args.require_operational,
                       max_price_age_days=args.max_price_age_days)
        print(json.dumps(result, indent=2))
        if not result["ok"]:
            raise SystemExit(1)
    else:
        result = shadow(rows, lookback=args.lookback_days, alpha=args.alpha,
                        headroom=args.headroom)
        write_json(DATA / "shadow_controller.json", result)
        print(json.dumps({k: v for k, v in result.items() if k != "daily"}, indent=2))


if __name__ == "__main__":
    main()
