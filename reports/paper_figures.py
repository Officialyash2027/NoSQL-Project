"""Column-width (3.4 in) figures for the IEEE paper, drawn from the same results as the docs."""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import report_data  # noqa: F401  (sets sys.path)
from app.experiments import plotstyle

S = plotstyle.SERIES
W = 3.45
LABELS = {"direct": "Direct Redis", "hanf": "HANF routing", "hanf_sync_ml": "+ sync ML",
          "hanf_batched_ml": "+ batched ML", "hanf_async_ml": "+ async ML"}


def _style():
    plt.rcParams.update({"font.size": 7, "axes.titlesize": 7.5, "axes.labelsize": 7, "xtick.labelsize": 6.5,
                         "ytick.labelsize": 6.5, "legend.fontsize": 6.3, "font.family": ["Times New Roman", "DejaVu Serif"],
                         "lines.linewidth": 1.4, "lines.markersize": 3.5})


def _bars(ax, groups, series, errs=None, width_total=0.8):
    n = len(series)
    w = width_total / n
    for i, (name, vals) in enumerate(series.items()):
        xs = np.arange(len(groups)) + (i - (n - 1) / 2) * w
        ax.bar(xs, vals, w * 0.88, color=S[i % len(S)], label=name,
               yerr=(errs or {}).get(name), error_kw={"elinewidth": 0.7, "capsize": 1.5, "ecolor": plotstyle.INK_2})
    ax.set_xticks(range(len(groups)), groups)
    ax.grid(axis="x", visible=False)


def e1(v, out: Path) -> Path:
    _style()
    by, wls = v["e1_by"], v["e1_wls"]
    cfgs = [c for c in ("direct", "hanf", "hanf_sync_ml", "hanf_batched_ml", "hanf_async_ml") if (wls[0], c) in by]
    fig, axes = plt.subplots(2, 1, figsize=(W, 3.5))
    ser = {LABELS[c]: [by[(w, c)]["throughput_mean"] / 1000 for w in wls] for c in cfgs}
    err = {LABELS[c]: [by[(w, c)]["throughput_sd"] / 1000 for w in wls] for c in cfgs}
    _bars(axes[0], [f"Workload {w}" for w in wls], ser, err)
    axes[0].set_ylabel("Throughput (k ops/s)")
    axes[0].set_title("(a) Throughput, mean ± SD of 3 runs")
    axes[0].legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.42), frameon=False)
    ser = {LABELS[c]: [by[(w, c)]["p99_us"] / 1000 for w in wls] for c in cfgs}
    _bars(axes[1], [f"Workload {w}" for w in wls], ser)
    axes[1].set_ylabel("p99 latency (ms)")
    axes[1].set_title("(b) Tail latency (p99)")
    fig.tight_layout(h_pad=0.6)
    p = out / "paper_e1.png"
    fig.savefig(p, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return p


def e2(v, out: Path) -> Path:
    _style()
    rs = v["e2_remap"]
    pairs = sorted({r["nodes"] for r in rs}, key=lambda x: int(x.split("->")[0]))
    labels = [r["scheme"].replace("consistent v=", "CH v=").replace("modulo", "mod N") for r in rs if r["nodes"] == pairs[0]]
    fig, axes = plt.subplots(2, 1, figsize=(W, 3.6))
    ser = {f"{p.replace('->', '→')} nodes": [r["remapped_pct_mean"] for r in rs if r["nodes"] == p] for p in pairs}
    err = {f"{p.replace('->', '→')} nodes": [r["remapped_pct_sd"] for r in rs if r["nodes"] == p] for p in pairs}
    _bars(axes[0], labels, ser, err)
    for i, p in enumerate(pairs):
        axes[0].axhline(100 / int(p.split("->")[1]), color=S[i], lw=0.8, ls=(0, (1, 1.5)))
    axes[0].set_ylabel("Keys remapped (%)")
    axes[0].set_title("(a) Keys moved when one node is added (dotted: ideal)")
    axes[0].legend(frameon=False)
    for i, e in enumerate(sorted({r["zipf_exponent"] for r in v["e2_reb"]})):
        pts = [r for r in v["e2_reb"] if r["zipf_exponent"] == e]
        axes[1].plot([r["round"] for r in pts], [r["max_over_mean"] for r in pts], marker="o", color=S[i],
                     label=f"Zipf s = {e} (hottest key {pts[0]['hottest_key_share_pct']:.1f}% of requests)")
    axes[1].axhline(1, color=plotstyle.MUTED, lw=0.7)
    axes[1].set_xlabel("Rebalancing round")
    axes[1].set_ylabel("Max / mean node load")
    axes[1].set_title("(b) Adaptive vnode rebalancing, 8 nodes")
    axes[1].legend(frameon=False)
    fig.tight_layout(h_pad=0.6)
    p = out / "paper_e2.png"
    fig.savefig(p, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return p


def e3(v, out: Path) -> Path:
    _style()
    summ = v["e3_summary"]
    methods = ["Static rules"] + [m for m in summ if m.startswith("Isolation")]
    short = ["Rules"] + [m.replace("Isolation Forest (c=", "IF ").rstrip(")") for m in methods[1:]]
    fig, axes = plt.subplots(2, 1, figsize=(W, 3.6))
    ser = {k.capitalize() if k != "f1" else "F1": [summ[m][k] for m in methods] for k in ("precision", "recall", "f1")}
    err = {k.capitalize() if k != "f1" else "F1": [summ[m][k + "_sd"] for m in methods] for k in ("precision", "recall", "f1")}
    _bars(axes[0], short, ser, err)
    axes[0].set_ylim(0.5, 1.16)
    axes[0].set_yticks([0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    axes[0].set_title(f"(a) Held-out test windows, mean ± SD of {len(v['e3_cfg']['seeds'])} seeds")
    axes[0].legend(ncol=3, frameon=False, loc="upper center")
    levels = v["e3_cfg"]["intensity_levels"]
    for i, kind in enumerate(("injection", "exfiltration", "bruteforce")):
        axes[1].plot([100 * l for l in levels], [v["e3_sweep_fn"](kind, l) for l in levels], marker="o",
                     color=S[i], label=kind.capitalize())
    axes[1].set_xscale("log")
    axes[1].set_xticks([100 * l for l in levels], [f"{100 * l:g}" for l in levels])
    axes[1].minorticks_off()
    axes[1].set_xlabel("Attack requests per window (%)")
    axes[1].set_ylabel("IF recall")
    axes[1].set_ylim(-0.03, 1.05)
    axes[1].set_title("(b) Evasion sweep (static rules: 0 below 30%)")
    axes[1].legend(frameon=False)
    fig.tight_layout(h_pad=0.6)
    p = out / "paper_e3.png"
    fig.savefig(p, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return p


def e4(v, out: Path) -> Path:
    _style()
    s = v["e4_summary"]
    names = ["static ONE/ONE", "static QUORUM/QUORUM", "static ALL/ALL", "HANF adaptive"]
    short = ["ONE", "QUORUM", "ALL", "Adaptive"]
    fig = plt.figure(figsize=(W, 3.7))
    gs = fig.add_gridspec(2, 2, height_ratios=[1, 1.15])
    a1, a2, a3 = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[1, :])
    for ax, field, title in ((a1, "mean_ms", "(a) Mean latency (ms)"), (a2, "stale_pct", "(b) Stale reads (%)")):
        bars = ax.bar(range(4), [s[n][field] for n in names], 0.55, color=S[0])
        for b, n in zip(bars, names):
            ax.annotate(f"{s[n][field]:.3g}", (b.get_x() + b.get_width() / 2, b.get_height()), xytext=(0, 1),
                        textcoords="offset points", ha="center", fontsize=5.8, color=plotstyle.INK_2)
        ax.set_xticks(range(4), short, fontsize=6)
        ax.grid(axis="x", visible=False)
        ax.set_title(title)
        ax.margins(y=0.15)
    ph = [p for p in v["e4_phases"] if p["strategy"] == "HANF adaptive"]
    x = [p["phase"] for p in ph]
    a3.plot(x, [p["observed_mean_ms"] for p in ph], marker="o", color=S[0], label="HANF adaptive (observed)")
    a3.plot(x, [p["predicted_mean_ms"] for p in ph], marker="x", ls="--", lw=1, color=S[1], label="controller prediction")
    a3.set_xticks(x, [f"{p['congestion']:g}×" for p in ph], fontsize=6)
    a3.set_xlabel("Phase 0-15, labelled with its congestion multiplier")
    a3.set_ylabel("Mean latency (ms)")
    a3.set_title("(c) Per-phase latency of the adaptive controller (one-phase lag)")
    a3.set_ylim(0, max(p["observed_mean_ms"] for p in ph) * 1.35)
    a3.legend(frameon=False, loc="upper left", ncol=2)
    fig.tight_layout(h_pad=0.6)
    p = out / "paper_e4.png"
    fig.savefig(p, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return p
