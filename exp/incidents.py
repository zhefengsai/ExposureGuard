"""Export incident-counterfactual panels (and optional combined figure).

Legend style (all panels): horizontal, ABOVE axes, width = x-axis
(mode='expand'), never over data.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np

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

HERE = os.path.dirname(os.path.abspath(__file__))
PAPER = os.path.join(HERE, "fig")
D = 1.0

INCIDENTS = [
    ("Wormhole\nFeb 2022", 326e6, 0.5),
    ("Nomad\nAug 2022", 190e6, 8.28),
    ("Ronin\nMar 2022", 625e6, 144.0),
]
TURNOVER_LO, TURNOVER_HI = 0.01235, 0.01454


def admitted(loss, hours, r):
    B = r * loss
    return min(loss, B * (1.0 + (hours / 24.0) / D))


def style_legend(leg):
    leg.set_frame_on(False)


def top_legend(ax, handles=None, labels=None, ncol=None, fontsize=None,
               handlelength=1.2, columnspacing=1.0, handletextpad=0.4,
               labelspacing=0.35):
    if handles is None:
        handles, labels = ax.get_legend_handles_labels()
    n = len(handles)
    if ncol is None:
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
        print("wrote", path)


def panel_a(ax=None, standalone=True):
    r0 = 0.02
    names = [i[0] for i in INCIDENTS]
    actual = [i[1] / 1e6 for i in INCIDENTS]
    cf = [admitted(i[1], i[2], r0) / 1e6 for i in INCIDENTS]
    own = ax is None
    if own:
        fig, ax = plt.subplots(figsize=(3.55, 2.95))
    x = np.arange(len(names))
    w = 0.36
    ax.bar(x - w / 2, actual, w, color="#c0392b", hatch="///",
           edgecolor="#7b241c", linewidth=0.55, label="actually lost", zorder=3)
    ax.bar(x + w / 2, cf, w, color="#2471a3", hatch="\\\\\\",
           edgecolor="#1a5276", linewidth=0.55,
           label=r"$B=2\%$ stock/day", zorder=3)
    for i, (a, c) in enumerate(zip(actual, cf)):
        ax.text(i - w / 2, a * 1.18, f"${a:,.0f}M", ha="center",
                fontsize=11, color="#922b21")
        ax.text(i + w / 2, max(c * 1.18, 1.2), f"${c:,.1f}M", ha="center",
                fontsize=11, color="#1a5276")
        ax.text(i, max(a, c) * 4.0, f"{a / c:.0f}$\\times$", ha="center",
                fontsize=11, fontweight="bold", color="#2c3e50")
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.set_ylabel("USD (log)")
    ax.set_ylim(0.8, 8e4)
    ax.grid(True, axis="y", which="both")
    top_legend(ax, ncol=2)
    if own:
        fig.subplots_adjust(left=0.16, right=0.98, bottom=0.14, top=0.78)
        save(fig, "fig_incidents_a.png")
        plt.close(fig)


def panel_b(ax=None, standalone=True):
    rs = np.logspace(np.log10(0.001), np.log10(0.2), 200)
    colors = ("#2471a3", "#6c3483", "#b9770e")
    markers = ("o", "s", "^")
    own = ax is None
    if own:
        fig, ax = plt.subplots(figsize=(3.55, 2.95))
    short = {
        "Wormhole\nFeb 2022": "Wormhole (0.5h)",
        "Nomad\nAug 2022": "Nomad (8.28h)",
        "Ronin\nMar 2022": "Ronin (144h)",
    }
    for (name, loss, hrs), col, mk in zip(INCIDENTS, colors, markers):
        y = [100 * (1 - admitted(loss, hrs, r) / loss) for r in rs]
        ax.plot(100 * rs, y, lw=2.0, color=col, marker=mk, markevery=28,
                markersize=4.5, markeredgecolor="white", markeredgewidth=0.45,
                label=short[name])
    ax.axvspan(100 * TURNOVER_LO, 100 * TURNOVER_HI, color="#1e8449",
               alpha=0.15, zorder=0)
    ax.axvline(1.0, color="#57606a", ls=":", lw=1.0)
    ax.annotate(r"$r=1\%$", xy=(1.0, 55), xytext=(2.2, 42),
                fontsize=11, color="#57606a",
                arrowprops=dict(arrowstyle="->", lw=0.7, color="#57606a"))
    ax.set_xscale("log")
    ax.set_xlabel(r"budget $B$ as % of stock / day")
    ax.set_ylabel("loss avoided (%)")
    ax.set_ylim(-2, 105)
    handles, labels = ax.get_legend_handles_labels()
    handles.append(Patch(facecolor="#1e8449", alpha=0.35,
                         label="turnover"))
    labels.append("turnover")
    top_legend(ax, handles=handles, labels=labels, ncol=2)
    if own:
        fig.subplots_adjust(left=0.15, right=0.98, bottom=0.14, top=0.76)
        save(fig, "fig_incidents_b.png")
        plt.close(fig)


def combined():
    """Also refresh the combined PNG the user may still open."""
    fig, ax = plt.subplots(1, 2, figsize=(7.4, 3.50))
    panel_a(ax=ax[0], standalone=False)
    panel_b(ax=ax[1], standalone=False)
    ax[0].text(0.5, 1.34, "(a) Actual vs counterfactual",
               transform=ax[0].transAxes, ha="center", va="bottom", fontsize=10)
    ax[1].text(0.5, 1.34, "(b) Sensitivity to budget sizing",
               transform=ax[1].transAxes, ha="center", va="bottom", fontsize=10)
    fig.subplots_adjust(left=0.07, right=0.99, bottom=0.12, top=0.68,
                        wspace=0.30)
    save(fig, "fig_incidents.png")
    plt.close(fig)


def main():
    panel_a()
    panel_b()
    combined()


if __name__ == "__main__":
    main()
