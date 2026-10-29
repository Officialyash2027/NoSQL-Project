"""E4 - adaptive consistency control (Monte-Carlo SIMULATION, no live multi-region cluster).

Runs the Review II congestion timeline under static ONE/ONE, QUORUM/QUORUM, ALL/ALL and the
adaptive controller (plus an oracle variant as an upper bound), repeated over several seeds, then
two sensitivity sweeps: the read-after-write delay (which drives ONE/ONE staleness) and the
staleness SLO given to the controller.
"""
from __future__ import annotations

import numpy as np

from ..consistency.simulator import NetworkModel, run_timeline
from . import plotstyle
from .common import ExperimentContext

MAIN = ["static ONE/ONE", "static QUORUM/QUORUM", "static ALL/ALL", "HANF adaptive", "adaptive (oracle)"]


def run(ctx: ExperimentContext, cfg: dict, model: NetworkModel | None = None) -> dict:
    model = model or NetworkModel()
    writer = ctx.store.new_run("e4", cfg | {"network_model": model.to_dict()}, ctx.env())
    runs = []
    for seed in cfg["seeds"]:
        ctx.progress(f"E4: timeline seed {seed}")
        runs.append(run_timeline(model, cfg["phases"], cfg["ops_per_phase"], cfg["slo"],
                                 cfg["mc_samples"], seed))
    per_seed = [{"seed": s, "strategy": k, **v} for s, r in zip(cfg["seeds"], runs)
                for k, v in r["strategies"].items()]
    for row in per_seed:
        row.pop("profile_counts", None)
    writer.write_csv("e4_strategies_by_seed.csv", per_seed)

    summary = []
    for name in MAIN:
        rs = [r["strategies"][name] for r in runs]
        row = {"strategy": name, "seeds": len(rs)}
        for f in ("write_ms", "read_ms", "mean_ms", "p95_ms", "stale_pct", "phases_over_slo"):
            vals = np.array([x[f] for x in rs], dtype=float)
            row[f] = round(float(vals.mean()), 3)
            row[f + "_sd"] = round(float(vals.std(ddof=1)), 3) if len(vals) > 1 else 0.0
        summary.append(row)
    q = next(r for r in summary if r["strategy"] == "static QUORUM/QUORUM")
    a = next(r for r in summary if r["strategy"] == "static ALL/ALL")
    for r in summary:
        r["mean_vs_quorum_pct"] = round(100 * (r["mean_ms"] - q["mean_ms"]) / q["mean_ms"], 1)
        r["mean_vs_all_pct"] = round(100 * (r["mean_ms"] - a["mean_ms"]) / a["mean_ms"], 1)
        r["p95_vs_quorum_pct"] = round(100 * (r["p95_ms"] - q["p95_ms"]) / q["p95_ms"], 1)
    writer.write_csv("e4_summary.csv", summary)
    phases = [p for p in runs[0]["phases"] if p["strategy"] in ("HANF adaptive", "adaptive (oracle)")]
    writer.write_csv("e4_adaptive_phases.csv", phases)
    profile_counts = runs[0]["strategies"]["HANF adaptive"]["profile_counts"]

    ctx.progress("E4: sensitivity sweeps")
    sens_delay = []
    for d in cfg["read_delays_ms"]:
        m = NetworkModel(**(model.to_dict() | {"read_delay_mean_ms": float(d)}))
        r = run_timeline(m, cfg["phases"], cfg["ops_per_phase"], cfg["slo"], cfg["mc_samples"],
                         cfg["seeds"][0], include_oracle=False)
        for k, v in r["strategies"].items():
            sens_delay.append({"read_delay_ms": d, "strategy": k, "mean_ms": v["mean_ms"],
                               "stale_pct": v["stale_pct"], "phases_over_slo": v["phases_over_slo"]})
    writer.write_csv("e4_sensitivity_read_delay.csv", sens_delay)
    sens_slo = []
    for slo in cfg["slos"]:
        r = run_timeline(model, cfg["phases"], cfg["ops_per_phase"], slo, cfg["mc_samples"],
                         cfg["seeds"][0], include_oracle=False)
        v = r["strategies"]["HANF adaptive"]
        sens_slo.append({"slo_pct": slo * 100, "mean_ms": v["mean_ms"], "p95_ms": v["p95_ms"],
                         "stale_pct": v["stale_pct"], "phases_over_slo": v["phases_over_slo"],
                         "profiles": v["profile_counts"]})
    writer.write_csv("e4_sensitivity_slo.csv", sens_slo)

    plot(writer, summary, runs[0], sens_delay, cfg)
    return writer.finish({"summary": summary, "adaptive_profile_counts_seed0": profile_counts,
                          "phases_seed0": phases, "sensitivity_read_delay": sens_delay,
                          "sensitivity_slo": sens_slo,
                          "note": "Simulation with assumed RTTs, jitter and read delay; not a "
                                  "measurement of a live multi-region database."})


def plot(writer, summary, first, sens_delay, cfg) -> None:
    names = [r["strategy"] for r in summary if r["strategy"] != "adaptive (oracle)"]
    short = [n.replace("static ", "").replace("HANF adaptive", "HANF adaptive") for n in names]
    rows = [next(r for r in summary if r["strategy"] == n) for n in names]
    fig, axes = plotstyle.subplots(1, 2, figsize=(8.6, 3.1))
    for ax, field, title, ylab in ((axes[0], "mean_ms", "(a) Mean operation latency", "ms"),
                                   (axes[1], "stale_pct", "(b) Stale reads", "% of reads")):
        bars = ax.bar(range(len(rows)), [r[field] for r in rows], 0.5,
                      color=[plotstyle.SERIES[0]] * len(rows),
                      yerr=[r[field + "_sd"] for r in rows],
                      error_kw={"ecolor": plotstyle.INK_2, "elinewidth": 1, "capsize": 2})
        for b, r in zip(bars, rows):
            ax.annotate(f"{r[field]:.3g}", (b.get_x() + b.get_width() / 2, b.get_height()),
                        xytext=(0, 2), textcoords="offset points", ha="center", fontsize=7,
                        color=plotstyle.INK_2)
        ax.set_xticks(range(len(rows)), short, fontsize=7)
        ax.grid(axis="x", visible=False)
        ax.set_title(title)
        ax.set_ylabel(ylab)
    fig.suptitle("E4 - Static vs adaptive consistency (simulation)", x=0.01, ha="left",
                 fontsize=10, fontweight="bold")
    fig.tight_layout()
    writer.add_graph(fig, "e4_consistency.png")

    ph = [p for p in first["phases"] if p["strategy"] == "HANF adaptive"]
    qp = [p for p in first["phases"] if p["strategy"] == "static QUORUM/QUORUM"]
    fig, axes = plotstyle.subplots(2, 1, figsize=(8.0, 4.4), sharex=True,
                                   gridspec_kw={"height_ratios": [1, 2]})
    x = [p["phase"] for p in ph]
    axes[0].step(x, [p["congestion"] for p in ph], where="mid", color=plotstyle.MUTED)
    axes[0].set_ylabel("Congestion ×")
    axes[0].set_title("E4 - Phase timeline: congestion, chosen profile and observed latency")
    axes[1].plot(x, [p["observed_mean_ms"] for p in qp], marker="o", color=plotstyle.SERIES[1],
                 label="static QUORUM/QUORUM")
    axes[1].plot(x, [p["observed_mean_ms"] for p in ph], marker="o", color=plotstyle.SERIES[0],
                 label="HANF adaptive")
    for p in ph:
        axes[1].annotate(p["profile"].replace("QUORUM", "Q"), (p["phase"], p["observed_mean_ms"]),
                         xytext=(0, 7), textcoords="offset points", ha="center", fontsize=6,
                         color=plotstyle.INK_2)
    axes[1].set_ylim(0, max(p["observed_mean_ms"] for p in qp) * 1.15)
    axes[1].set_ylabel("Mean latency (ms)")
    axes[1].set_xlabel("Phase (adaptive profile W/R labelled above each point; Q = QUORUM)")
    axes[1].legend()
    fig.tight_layout()
    writer.add_graph(fig, "e4_timeline.png")

    fig, ax = plotstyle.subplots(figsize=(5.6, 3.0))
    for i, s in enumerate(["static ONE/ONE", "HANF adaptive"]):
        pts = [r for r in sens_delay if r["strategy"] == s]
        ax.plot([r["read_delay_ms"] for r in pts], [r["stale_pct"] for r in pts], marker="o",
                color=plotstyle.SERIES[i], label=s)
    ax.set_xlabel("Mean read-after-write delay (ms)")
    ax.set_ylabel("Stale reads (%)")
    ax.set_title("E4 - Sensitivity of staleness to the read delay")
    ax.legend()
    writer.add_graph(fig, "e4_sensitivity.png")
