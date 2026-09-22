#!/usr/bin/env python3
"""Figures for the generalization sweep and the oracle-detector baseline."""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams.update({
    "font.size": 11, "axes.labelsize": 11, "axes.titlesize": 11,
    "legend.fontsize": 11, "xtick.labelsize": 11, "ytick.labelsize": 11,
    "axes.grid": True, "grid.alpha": 0.35, "grid.linestyle": "-",
    "axes.axisbelow": True,
})

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fig")


def save(fig, name):
    p = os.path.join(OUT, name)
    fig.savefig(p, dpi=300, bbox_inches="tight", pad_inches=0.05)
    fig.savefig(os.path.splitext(p)[0] + ".pdf", bbox_inches="tight",
                pad_inches=0.05)
    plt.close(fig)
    print("wrote", os.path.relpath(p, HERE), "(+pdf)")


def top_legend(ax, ncol=2):
    leg = ax.legend(loc="lower center", bbox_to_anchor=(0, 1.02, 1, 0.12),
                    mode="expand", ncol=ncol, borderaxespad=0,
                    handlelength=1.4, columnspacing=1.0, handletextpad=0.4)
    leg.set_frame_on(False)


def panel_a(d):
    s = d["rho_sweep"]
    x = np.array([r["rho"] for r in s])
    m = np.array([r["M_mean"] for r in s])
    lo = np.array([r["M_p5"] for r in s])
    hi = np.array([r["M_p95"] for r in s])
    # The validation point sits at rho = 0: the measured correlation is
    # indistinguishable from zero and the model expresses rho >= 0.
    obs, rho_hat = d["measured"]["M"], 0.0

    fig, ax = plt.subplots(figsize=(4.6, 3.2))
    ax.fill_between(x, lo, hi, alpha=0.20, color="C0", label="synthetic, 5-95%")
    ax.plot(x, m, "-o", color="C0", ms=4, label="synthetic, mean")
    ax.axhline(1.0, ls="--", lw=1.2, color="0.35", label="no advantage")
    ax.plot([rho_hat], [obs], "*", ms=14, color="C3", label="measured trace")
    ax.annotate(f"{obs:.2f}x", (rho_hat, obs), textcoords="offset points",
                xytext=(10, 2), color="C3")
    ax.set_xlabel(r"correlation of log daily demand, $\rho$")
    ax.set_ylabel(r"multiplexing ratio $M$")
    ax.set_xlim(-0.03, 1.02)
    ax.set_ylim(0.8, max(hi.max(), obs) * 1.08)
    top_legend(ax)
    save(fig, "fig_synth_a.png")


def panel_b(d):
    s = d["n_sweep"]
    x = np.array([r["n"] for r in s], float)
    m = np.array([r["M_mean"] for r in s])
    lo = np.array([r["M_p5"] for r in s])
    hi = np.array([r["M_p95"] for r in s])

    fig, ax = plt.subplots(figsize=(4.6, 3.2))
    ax.fill_between(x, lo, hi, alpha=0.20, color="C2", label="synthetic, 5-95%")
    ax.plot(x, m, "-s", color="C2", ms=4, label="synthetic, mean")
    ax.axhline(1.0, ls="--", lw=1.2, color="0.35", label="no advantage")
    ax.plot([d["measured"]["tokens"]], [d["measured"]["M"]], "*", ms=14,
            color="C3", label="measured trace")
    ax.set_xscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{int(v)}" for v in x])
    ax.set_xlabel("groups sharing the root, $n$")
    ax.set_ylabel(r"multiplexing ratio $M$")
    ax.set_ylim(0.8, max(hi.max(), d["measured"]["M"]) * 1.08)
    top_legend(ax)
    save(fig, "fig_synth_b.png")


def panel_detector(d):
    stock, B, D = d["stock_usd"], d["B_usd"], d["D_days"]
    rows = {r["block_share"]: r for r in d["blockspace"]}
    fast, slow = rows[1.0], rows[0.05]

    dd = np.logspace(np.log10(0.25), np.log10(7 * 86400), 600)     # seconds
    days = dd / 86400.0
    guard = np.minimum(stock, B * (1 + days / D))

    fig, ax = plt.subplots(figsize=(4.6, 3.2))
    for r, c, ls, lab in ((fast, "C1", "-", "detector, 1 block"),
                          (slow, "C4", "-.", "detector, 5% of block")):
        y = np.minimum(stock, r["rate_usd_per_day"] * days)
        ax.plot(dd, y / 1e6, ls, color=c, lw=1.6, label=lab)
        xc = r["crossover_days"] * 86400.0
        ax.plot([xc], [min(stock, r["rate_usd_per_day"] * r["crossover_days"]) / 1e6],
                "o", ms=5, color=c)
    ax.plot(dd, guard / 1e6, "-", color="C0", lw=2.0, label="ExposureGuard")
    ax.axhline(stock / 1e6, ls=":", lw=1.2, color="0.35")
    ax.text(0.3, stock / 1e6 * 0.90, "collateral stock", fontsize=9, color="0.35")
    ax.annotate(f"{fast['crossover_days']*86400:.1f} s",
                (fast["crossover_days"] * 86400, 1.4), fontsize=9, color="C1",
                textcoords="offset points", xytext=(-4, 14), ha="right")
    ax.set_xscale("log")
    ax.set_xlabel("detection latency (s)")
    ax.set_ylabel("admitted value (USD, millions)")
    ax.set_ylim(0, stock / 1e6 * 1.12)
    top_legend(ax, ncol=2)
    save(fig, "fig_detector.png")


if __name__ == "__main__":
    plt.rcParams["text.usetex"] = False
    g = json.load(open(f"{HERE}/data/synth_generalization.json"))
    b = json.load(open(f"{HERE}/data/detector_baseline.json"))
    panel_a(g); panel_b(g); panel_detector(b)
