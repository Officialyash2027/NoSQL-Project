"""Shared Matplotlib style for every generated graph (one consistent look across the report).

Colours follow a validated categorical order (blue, orange, aqua, yellow); text uses ink colours,
never series colours; gridlines are recessive hairlines; bars are thin.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"], "font.size": 9,
    "text.color": INK, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
    "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.grid": True, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False,
    "grid.color": GRID, "grid.linewidth": 0.8, "grid.linestyle": "-",
    "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK_2, "ytick.labelcolor": INK_2,
    "legend.frameon": False, "legend.fontsize": 8, "lines.linewidth": 2,
    "lines.solid_capstyle": "round", "lines.markersize": 6,
})


def subplots(*args, **kwargs):
    return plt.subplots(*args, **kwargs)


def grouped_bars(ax, groups: list[str], series: dict[str, list[float]], errors: dict | None = None,
                 fmt: str = "{:.0f}", label_values: bool = False, max_width: float = 0.8):
    """Thin grouped bars with a surface gap between neighbours."""
    n = len(series)
    width = min(max_width / n, 0.22)
    xs = range(len(groups))
    for i, (name, vals) in enumerate(series.items()):
        offs = [x + (i - (n - 1) / 2) * width for x in xs]
        err = errors.get(name) if errors else None
        bars = ax.bar(offs, vals, width * 0.9, color=SERIES[i % len(SERIES)], label=name,
                      yerr=err, error_kw={"ecolor": INK_2, "elinewidth": 1, "capsize": 2})
        if label_values:
            for b, v in zip(bars, vals):
                ax.annotate(fmt.format(v), (b.get_x() + b.get_width() / 2, b.get_height()),
                            xytext=(0, 2), textcoords="offset points", ha="center", va="bottom",
                            fontsize=7, color=INK_2)
    ax.set_xticks(list(xs), groups)
    ax.grid(axis="x", visible=False)
    return ax
