#!/usr/bin/env python3
"""Atomic replay under common availability targets, with no held-out tuning.

Policies share frozen USD valuations, a one-day refill period, full initial
buckets, arrival-order processing, 600-second retries and a seven-day tail.
The first 60 days fit capacities and select the smallest grid budget
meeting BOTH immediate message and immediate value service targets on that
training prefix. Freeze, then evaluate the suffix. The full fixed grid is
also reported as a descriptive frontier, never used to choose test settings.
Per-token groups use the frozen trace's symbol labels, not Celer deployment
parameters or an independently verified canonical asset registry.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
D = 86400.0
GRID = (0.25, 0.5, 0.75, 1.0, 1.07, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)
TARGETS = (0.95, 0.99, 1.0)
POLICIES = ("guard", "per_token", "per_route", "pooled",
            "coordinated_token_usage", "coordinated_token_peak")


def load(chain):
    events = json.loads((HERE / "data" / f"events_{chain}.json").read_text())
    for e in events:
        e["ts"] = datetime.fromisoformat(e["t"]).replace(tzinfo=timezone.utc).timestamp()
        assert math.isfinite(e["usd"]) and e["usd"] >= 0
        assert e.get("symbol"), "token grouping must not silently merge missing symbols"
    return sorted(events, key=lambda e: e["ts"])


def fit(events, policy, multiple):
    daily = defaultdict(lambda: defaultdict(float))
    use = defaultdict(float)
    root_daily = defaultdict(float)
    for e in events:
        group = e["symbol"] if policy in ("per_token", "coordinated_token_usage",
                                          "coordinated_token_peak") else e["route"]
        if policy in ("guard", "pooled"):
            group = "root"
        daily[group][int(e["ts"] // D)] += e["usd"]
        root_daily[int(e["ts"] // D)] += e["usd"]
        use[e["route"]] += e["usd"]
    caps = {g: multiple * max(days.values()) for g, days in daily.items()}
    if policy.startswith("coordinated_token_"):
        budget = multiple * max(root_daily.values())
        basis = ({g: sum(days.values()) for g, days in daily.items()}
                 if policy.endswith("usage") else
                 {g: max(days.values()) for g, days in daily.items()})
        denominator = sum(basis.values())
        caps = {g: budget * v / denominator for g, v in basis.items()}
        # Keep the intended common envelope exact despite floating-point sums.
        last = next(reversed(caps))
        caps[last] += budget - sum(caps.values())
    total = sum(use.values())
    # Match the contract's basis-point weights, rounding down, not floating
    # renormalization. Unallocated reservation is not given to surplus.
    weights = {r: math.floor(10000 * v / total) / 10000 for r, v in use.items()}
    return {"policy": policy, "multiple": multiple, "caps": caps,
            "weights": weights, "budget": sum(caps.values())}


class Ledger:
    def __init__(self, config, t):
        self.policy = config["policy"]
        self.caps = dict(config["caps"])
        if self.policy == "guard":
            budget = config["budget"]
            self.caps = {r: 0.8 * budget * w for r, w in config["weights"].items()}
            self.caps["__surplus__"] = 0.2 * budget
        self.level = dict(self.caps)
        self.last = {g: t for g in self.caps}

    def groups(self, e):
        if self.policy == "guard":
            return (e["route"], "__surplus__")
        if self.policy == "pooled":
            return ("root",)
        return (e["symbol"] if self.policy == "per_token" or
                self.policy.startswith("coordinated_token_") else e["route"],)

    def possible(self, e):
        return sum(self.caps.get(g, 0.0) for g in self.groups(e)) + 1e-8 >= e["usd"]

    def admit(self, e, t):
        groups = self.groups(e)
        available = {g: min(self.caps.get(g, 0.0), self.level.get(g, 0.0)
                            + (t - self.last.get(g, t)) * self.caps.get(g, 0.0) / D)
                     for g in groups}
        if sum(available.values()) + 1e-8 < e["usd"]:
            return False  # no bucket changes on revert
        need = e["usd"]
        for g in groups:
            take = min(available[g], need)
            self.level[g] = available[g] - take
            self.last[g] = t
            need -= take
        return True


def replay(events, config, retry_step=600.0):
    ledger = Ledger(config, events[0]["ts"])
    pending, delivered = [], []
    idx = delayed = impossible = immediate_n = 0
    immediate_value = 0.0
    end = events[-1]["ts"] + 7 * D
    tick = math.inf
    while idx < len(events) or pending:
        arrival = events[idx]["ts"] if idx < len(events) else math.inf
        t = min(arrival, tick)
        if t > end:
            break
        # Pending messages retry on a fixed cadence, before same-time arrivals.
        if tick <= arrival:
            still = []
            for e in pending:
                if ledger.admit(e, t):
                    delivered.append((e, t - e["ts"]))
                else:
                    still.append(e)
            pending = still
            tick = t + retry_step if pending else math.inf
        while idx < len(events) and events[idx]["ts"] == t:
            e = events[idx]
            idx += 1
            if ledger.admit(e, t):
                immediate_n += 1
                immediate_value += e["usd"]
                delivered.append((e, 0.0))
            else:
                delayed += 1
                if not ledger.possible(e):
                    # Retrying an oversized message cannot change its outcome.
                    impossible += 1
                else:
                    pending.append(e)
                    if math.isinf(tick):
                        tick = t + retry_step
    delays = sorted(delay for _, delay in delivered if delay > 0)
    total_value = sum(e["usd"] for e in events)
    result = {"messages": len(events), "value_usd": total_value,
              "immediate_messages": immediate_n, "deferred_on_arrival": delayed,
              "immediate_message_fraction": immediate_n / len(events),
              "immediate_value_fraction": immediate_value / total_value,
              "delayed_then_delivered": len(delays),
              "undelivered_tail": len(pending) + impossible,
              "oversized_messages": impossible,
              "delivered_value_fraction": sum(e["usd"] for e, _ in delivered) / total_value,
              "p95_positive_delay_h": delays[math.ceil(.95 * len(delays)) - 1] / 3600 if delays else 0.0}
    assert immediate_n + len(delays) + result["undelivered_tail"] == len(events)
    return result


def experiment(chain="ethereum"):
    events = load(chain)
    t0 = events[0]["ts"]
    train = [e for e in events if e["ts"] < t0 + 60 * D]
    test = [e for e in events if e["ts"] >= t0 + 60 * D]
    assert train and test
    curves, selections = {}, []
    for policy in POLICIES:
        curve = []
        for multiple in GRID:
            frozen = fit(train, policy, multiple)
            calibration = replay(train, frozen)
            measured = replay(test, frozen)
            curve.append({"multiple": multiple, "budget_usd": frozen["budget"],
                          "six_hour_envelope_usd": frozen["budget"] * 1.25,
                          "calibration": calibration, "test": measured})
        curves[policy] = curve
        for target in TARGETS:
            feasible = [r for r in curve
                        if r["calibration"]["immediate_message_fraction"] >= target - 1e-12
                        and r["calibration"]["immediate_value_fraction"] >= target - 1e-12]
            selected = min(feasible, key=lambda r: r["multiple"]) if feasible else None
            selections.append({"policy": policy, "target": target,
                               "selected": selected})
    unseen = {}
    for key in ("route", "symbol"):
        known = {e[key] for e in train}
        missing = [e for e in test if e[key] not in known]
        unseen[key] = {"messages": len(missing), "value_usd": sum(e["usd"] for e in missing)}
    # Preserve a stricter temporal calibration check, including infeasible
    # targets; do not silently expand the grid until every policy passes.
    inner = [e for e in train if e["ts"] < t0 + 40 * D]
    valid = [e for e in train if e["ts"] >= t0 + 40 * D]
    nested = []
    for policy in POLICIES:
        metrics = [(m, replay(valid, fit(inner, policy, m))) for m in GRID]
        for target in TARGETS:
            feasible = [m for m, r in metrics
                        if r["immediate_message_fraction"] >= target - 1e-12
                        and r["immediate_value_fraction"] >= target - 1e-12]
            nested.append({"policy": policy, "target": target,
                           "selected_multiple": min(feasible) if feasible else None,
                           "max_grid_validation": metrics[-1][1]})
    return {"chain": chain, "train_messages": len(train), "test_messages": len(test),
            "unseen_groups": unseen,
            "temporal_calibration_check": {"fit_days": 40, "validation_days": 20, "rows": nested},
            "split_utc": datetime.fromtimestamp(t0 + 60 * D, timezone.utc).isoformat(),
            "curves": curves, "selections": selections}


def plot(result):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(7.1, 2.55), constrained_layout=True)
    names = {"guard": "ExposureGuard", "per_token": "Per-token", "per_route": "Per-route", "pooled": "Pooled (no floors)"}
    for policy, color in zip(POLICIES[:4], ("#1764ab", "#b24b16", "#755299", "#39794b")):
        curve = result["curves"][policy]
        for ax, metric in zip(axes, ("immediate_value_fraction", "immediate_message_fraction")):
            ax.plot([100*r["test"][metric] for r in curve],
                    [r["six_hour_envelope_usd"]/1e6 for r in curve],
                    ".-", color=color, linewidth=1, markersize=3, label=names[policy])
            chosen = next(r["selected"] for r in result["selections"] if r["policy"] == policy and r["target"] == 1.0)
            if chosen:
                ax.scatter([100*chosen["test"][metric]], [chosen["six_hour_envelope_usd"]/1e6],
                           s=55, marker="*", color=color, zorder=4)
    for ax, label in zip(axes, ("Held-out value admitted on arrival (%)", "Held-out messages admitted on arrival (%)")):
        ax.set_xlabel(label, fontsize=8)
        ax.set_yscale("log")
        ax.tick_params(labelsize=8)
        ax.grid(alpha=.2)
    axes[0].set_ylabel("Six-hour envelope (million USD)", fontsize=8)
    axes[1].legend(fontsize=7, loc="upper left")
    dest = HERE / "fig"
    dest.mkdir(exist_ok=True)
    fig.savefig(dest / "fig_matched_availability.pdf")
    fig.savefig(dest / "fig_matched_availability.png", dpi=180)
    plt.close(fig)


def main():
    result = experiment()
    result.update({"schema": 1, "grid": GRID, "targets": TARGETS,
                   "input_sha256": hashlib.sha256((HERE / "data/events_ethereum.json").read_bytes()).hexdigest(),
                   "semantics": "atomic USD accounting; no partial debit; 600s retries; 7-day tail; full initialization; floor weights rounded down to basis points",
                   "selection": "first 60 days fit and calibrate BOTH arrival-count and arrival-value target; suffix test; smallest feasible fixed-grid multiple; training feasibility is not held-out validation",
                   "limits": "clean-load target matching is not equal realized held-out availability; token symbol grouping; no new-group capacity in independent policies; no new token prices; not bit-exact EVM arithmetic"})
    path = HERE / "data/matched_availability.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    plot(result)
    for row in result["selections"]:
        s = row["selected"]
        if s:
            t = s["test"]
            print(row["policy"], row["target"], s["multiple"],
                  f"envelope=${s['six_hour_envelope_usd']:,.0f}",
                  f"immediate value={100*t['immediate_value_fraction']:.2f}%",
                  f"arrival deferral={t['deferred_on_arrival']}/{t['messages']}",
                  f"pending={t['undelivered_tail']}")
        else:
            print(row["policy"], row["target"], "INFEASIBLE ON GRID")


if __name__ == "__main__":
    main()
