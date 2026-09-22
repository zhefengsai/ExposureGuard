"""Export alpha-sweep panels as separate PNGs for LaTeX subfigure layout.

Legend style (all panels): horizontal, ABOVE axes, width = x-axis
(mode='expand'), never over data.
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({
    "font.size": 11,
    "axes.labelsize": 11,
    "axes.titlesize": 11,
    "legend.fontsize": 11,
    "xtick.labelsize": 11,
    "ytick.labelsize": 11,
    "axes.grid": True,
    "grid.alpha": 0.35,
    "grid.linestyle": "-",
    "axes.axisbelow": True,
})

from sim import load, summarize, B_DEFAULT as B
from sim2 import run2

HERE = os.path.dirname(os.path.abspath(__file__))
PAPER = os.path.join(HERE, "fig")
ZERO = "0x" + "0" * 40

events = load()
demand = {}
for e in events:
    demand[e["route"]] = demand.get(e["route"], 0) + e["usd"]
all_ids = [r["router"][2:].lower()
           for r in json.load(open(f"{HERE}/data/hl_ism_ethereum.json"))
           if r.get("ism") == ZERO]

alphas = [i / 20 for i in range(21)]
COL = {"usage": "#2471a3", "equal": "#c0392b", "usage+min": "#6c3483"}
LBL = {"usage": "usage-weighted", "equal": "equal-share",
       "usage+min": "usage+min"}
MK = {"usage": "o", "equal": "s", "usage+min": "^"}


def style_legend(leg):
    leg.set_frame_on(False)


def top_legend(ax, handles=None, labels=None, ncol=None, fontsize=None,
               handlelength=1.2, columnspacing=1.0, handletextpad=0.4,
               labelspacing=0.35):
    """Legend above axes, centered, no frame. Use ncol=2 for two rows when
    labels are long so they never overlap."""
    if handles is None:
        handles, labels = ax.get_legend_handles_labels()
    n = len(handles)
    if ncol is None:
        # Prefer two rows when more than 2 entries (avoids overlap at 11pt).
        ncol = 2 if n >= 3 else n
    if fontsize is None:
        fontsize = plt.rcParams["axes.labelsize"]
    leg = ax.legend(
        handles=handles, labels=labels,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.02),
        ncol=ncol,
        frameon=False,
        borderaxespad=0.0,
        borderpad=0.15,
        handlelength=handlelength,
        handletextpad=handletextpad,
        columnspacing=columnspacing,
        labelspacing=labelspacing,
        prop={"size": fontsize},
    )
    style_legend(leg)
    return leg


def save(fig, name):
    for d in (PAPER,):
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        fig.savefig(path, dpi=300, bbox_inches="tight", pad_inches=0.05)
        # Journal submission wants vector line art; the PNG is kept because the
        # conference version references it.
        fig.savefig(os.path.splitext(path)[0] + ".pdf", bbox_inches="tight",
                    pad_inches=0.05)
        print("wrote", path, "(+pdf)")


def legit(adm, adv):
    return 100 * summarize(adm, demand, exclude=tuple(adv) + ("__dormant__",))[0]


def curve(fp, adversary, n_sybil=0):
    out = []
    for a in alphas:
        adm, adv = run2(events, B, a, fp, adversary, n_sybil=n_sybil,
                        all_ids=all_ids)
        out.append(legit(adm, adv))
    return out


def capture(fp, adversary, n_sybil=0):
    days = (events[-1]["ts"] - events[0]["ts"]) / 86400.0
    out = []
    for a in alphas:
        adm, adv = run2(events, B, a, fp, adversary, n_sybil=n_sybil,
                        all_ids=all_ids)
        out.append(100 * sum(adm.get(n, 0.0) for n in adv) / days / B)
    return out


def panel_ab(kind):
    data = {fp: curve(fp, "drain" if kind == "drain" else "none") for fp in COL}
    fig, ax = plt.subplots(figsize=(3.45, 3.15))
    for fp in ("usage", "equal", "usage+min"):
        ax.plot(alphas, data[fp], color=COL[fp], lw=2.0, marker=MK[fp],
                markevery=4, markersize=4.5, markeredgecolor="white",
                markeredgewidth=0.45, label=LBL[fp])
    ax.set_xlabel(r"reservation fraction $\alpha$")
    ax.set_ylabel("legitimate value admitted (%)")
    ax.set_ylim(-2, 108)
    ax.set_xlim(0.0, 1.0)
    ax.set_xticks([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    ax.axvspan(0.6, 1.0, color="#1e8449", alpha=0.07)
    # 3 items → 2 columns (two rows): no overlap
    top_legend(ax, ncol=2)
    fig.subplots_adjust(left=0.16, right=0.98, bottom=0.14, top=0.78)
    name = "fig_alpha_a.png" if kind == "drain" else "fig_alpha_b.png"
    save(fig, name)
    plt.close(fig)


def panel_c():
    fig, ax = plt.subplots(figsize=(3.45, 3.15))
    bound = [100 * (1 - a) for a in alphas]
    ax.plot(alphas, bound, color="#7f8c8d", lw=2.6, alpha=0.55,
            label=r"$(1-\alpha)B$")
    ax.plot(alphas, capture("usage", "flood"), color=COL["usage"], lw=1.7,
            marker="o", markevery=4, markersize=4.0,
            markeredgecolor="white", markeredgewidth=0.4,
            label="flood")
    ax.plot(alphas, capture("usage", "drain"), color="#b9770e", lw=1.7,
            ls="-.", marker="D", markevery=4, markersize=3.8,
            markeredgecolor="white", markeredgewidth=0.4,
            label="drain")
    ax.plot(alphas, capture("usage", "sybil", n_sybil=50),
            color="#1e8449", lw=1.6, ls="--", marker="^",
            markevery=4, markersize=4.0,
            markeredgecolor="white", markeredgewidth=0.4,
            label="50 Sybil")
    ax.set_xlabel(r"reservation fraction $\alpha$")
    ax.set_ylabel("share of budget captured (%)")
    ax.set_ylim(-2, 108)
    ax.set_xlim(0.0, 1.0)
    ax.set_xticks([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    ax.axvspan(0.6, 1.0, color="#1e8449", alpha=0.07)
    # 4 items → 2x2
    top_legend(ax, ncol=2)
    fig.subplots_adjust(left=0.16, right=0.98, bottom=0.14, top=0.76)
    save(fig, "fig_alpha_c.png")
    plt.close(fig)


if __name__ == "__main__":
    panel_ab("drain")
    panel_ab("clean")
    panel_c()
