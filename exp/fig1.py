"""Export detection-gap panels as separate PNGs for LaTeX subfigure layout.

Legend style (all panels): horizontal, ABOVE axes, width = x-axis
(mode='expand'), never over data.
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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
STOCK = 9_857_090.0
PEAK = 1_152_559.0
D = 1.0

C_PAUSE = "#c0392b"
C_1X = "#2471a3"
C_2X = "#6c3483"
C_5X = "#b9770e"


def admitted(B, hours):
    return min(STOCK, B * (1.0 + (hours / 24.0) / D))


def style_legend(leg):
    leg.set_frame_on(False)


def top_legend(ax, handles=None, labels=None, ncol=None, fontsize=None,
               handlelength=1.2, columnspacing=1.0, handletextpad=0.4,
               labelspacing=0.35):
    """Legend above axes, centered, no frame. ncol=2 → two rows when long."""
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
        fig.savefig(os.path.splitext(path)[0] + ".pdf", bbox_inches="tight",
                    pad_inches=0.05)
        print("wrote", path)


def panel_a():
    B0 = round(PEAK * 1.07, -3)
    t = np.logspace(np.log10(0.5 / 24), np.log10(60), 400)
    fig, ax = plt.subplots(figsize=(3.55, 2.85))

    h_pause, = ax.plot([], [], color=C_PAUSE, lw=2.3, label="pause alone")
    ax.axhline(STOCK, color=C_PAUSE, lw=2.3)
    h_bud, = ax.plot(t, np.minimum(STOCK, B0 + B0 * t / D), color=C_1X, lw=2.3,
                     label=rf"budget $B=\${B0/1e6:.2f}$M/day")
    ax.fill_between(t, np.minimum(STOCK, B0 + B0 * t / D), STOCK,
                     color=C_1X, alpha=0.12)
    for hr in (0.5 / 24, 6 / 24, 1, 7):
        v = admitted(B0, hr * 24)
        ax.plot([hr], [v], "o", ms=5, color=C_1X, zorder=5,
                markeredgecolor="white", markeredgewidth=0.55)

    six_v = admitted(B0, 6)
    ax.annotate(
        f"6 h: {STOCK / six_v:.1f}$\\times$",
        xy=(6 / 24, six_v), xytext=(0.7, 2.2e6),
        ha="left", va="center", color=C_1X, fontsize=11,
        arrowprops=dict(arrowstyle="->", lw=0.8, color=C_1X),
    )
    ax.axvline((STOCK / B0 - 1) * D, color="#57606a", ls=":", lw=1.0)

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(t[0], t[-1])
    ax.set_ylim(1.5e5, 5e7)
    ax.set_xlabel(r"detection delay $t_1-t_0$ (days)")
    ax.set_ylabel("value admitted (USD)")
    # Shorten budget legend so size-10 text still fits one row
    top_legend(ax, handles=[h_pause, h_bud],
               labels=["pause alone", rf"$B=\${B0/1e6:.2f}$M/day"],
               ncol=2)
    fig.subplots_adjust(left=0.16, right=0.98, bottom=0.14, top=0.82)
    save(fig, "fig_gap_a.png")
    plt.close(fig)
    return STOCK / six_v


def panel_b():
    delays = [("30 min", 0.5), ("6 h", 6.0), ("1 day", 24.0), ("7 days", 168.0)]
    mults = [
        ("pause", None, C_PAUSE, "///"),
        (r"$1.07\times$", 1.07, C_1X, "\\\\\\"),
        (r"$2\times$", 2.0, C_2X, "xx"),
        (r"$5\times$", 5.0, C_5X, ".."),
    ]
    fig, ax = plt.subplots(figsize=(3.55, 2.85))
    x = np.arange(len(delays))
    n = len(mults)
    width = 0.18
    offsets = (np.arange(n) - (n - 1) / 2) * width
    for j, (lab, mult, col, hatch) in enumerate(mults):
        vals = [STOCK if mult is None else admitted(PEAK * mult, h)
                for _, h in delays]
        ax.bar(x + offsets[j], vals, width, label=lab, color=col,
               hatch=hatch, edgecolor="black", linewidth=0.35, alpha=0.92)

    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels([d[0] for d in delays])
    ax.set_ylabel("value admitted (USD)")
    ax.set_xlabel("detection delay")
    ax.set_ylim(4e5, 2.5e7)
    # Same style as panel (a): one centered horizontal row
    top_legend(ax, ncol=4, columnspacing=0.9, handlelength=1.1)
    fig.subplots_adjust(left=0.16, right=0.98, bottom=0.14, top=0.82)
    save(fig, "fig_gap_b.png")
    plt.close(fig)


if __name__ == "__main__":
    f = panel_a()
    panel_b()
    print(f"6h containment factor = {f:.1f}x")
