"""Architecture and pipeline diagrams for the IEEE report (drawn with Matplotlib, reproducible)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.experiments import plotstyle  # noqa: E402  (shared palette/style)
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

S = plotstyle.SERIES


def _box(ax, x, y, w, h, text, fc, ec=None, fs=7.5, bold=False, tc=None):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.006,rounding_size=0.012",
                                fc=fc, ec=ec or fc, lw=1))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            color=tc or plotstyle.INK, fontweight="bold" if bold else "normal", linespacing=1.25)


def _arrow(ax, x1, y1, x2, y2, both=False, dashed=False):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="<|-|>" if both else "-|>",
                                 mutation_scale=7, lw=0.9, color=plotstyle.INK_2,
                                 linestyle=(0, (3, 2)) if dashed else "-"))


def architecture(path: Path) -> Path:
    fig, ax = plotstyle.subplots(figsize=(7.0, 4.6))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    light = "#eef3fb"
    _box(ax, 0.20, 0.915, 0.60, 0.065, "Client applications  (dashboard · REST API · scripts · Big Data workloads)", "#f2f1ed",
         ec=plotstyle.AXIS, fs=7.5)
    ax.add_patch(FancyBboxPatch((0.03, 0.235), 0.94, 0.645, boxstyle="round,pad=0.004,rounding_size=0.015",
                                fc="none", ec=S[0], lw=1.2))
    ax.text(0.05, 0.855, "HANF decision layer", fontsize=7.5,
            color=S[0], fontweight="bold")
    _box(ax, 0.30, 0.735, 0.40, 0.085, "Request pipeline + Polyglot Router\nweighted-rule workload classifier → engine",
         S[0], fs=7.2, tc="white", bold=True)
    mods = [
        (0.06, "Adaptive Consistency\nController\nMonte-Carlo (W,R) selection\nunder staleness SLO", S[6]),
        (0.375, "ML Security Monitor\n6 window features\nIsolation Forest + rules\nasync batched scoring", S[1]),
        (0.69, "Adaptive Sharding\nManager\nconsistent hashing + vnodes\nverified live migration", S[2]),
    ]
    for x, t, c in mods:
        _box(ax, x, 0.48, 0.25, 0.17, t, c, fs=6.6, tc="white")
        _arrow(ax, 0.5, 0.735, x + 0.125, 0.652)
    _box(ax, 0.06, 0.29, 0.88, 0.09,
         "Shared Telemetry Bus\nrequest latency & status · key-load per shard · failures · "
         "window scores & alerts · profile changes · migrations", light, ec=S[0], fs=6.6)
    for x, _, _ in mods:
        _arrow(ax, x + 0.125, 0.48, x + 0.125, 0.382, both=True)
    engines = [(0.06, "Redis 7.0.15 ×5 shards\nkey-value (ports 7001–7005)"),
               (0.375, "MongoDB 7.0\ndocument"),
               (0.69, "Cassandra 4.1\nwide-column (CL applied)")]
    for x, t in engines:
        _box(ax, x, 0.05, 0.25, 0.10, t, "#f2f1ed", ec=plotstyle.AXIS, fs=6.8)
        _arrow(ax, 0.5, 0.235, x + 0.125, 0.152)
    _arrow(ax, 0.5, 0.915, 0.5, 0.822)
    ax.text(0.53, 0.19, "adapters", ha="left", fontsize=6.5, color=plotstyle.INK_2)
    fig.savefig(path, dpi=220, bbox_inches="tight")
    return path


def pipeline(path: Path) -> Path:
    fig, ax = plotstyle.subplots(figsize=(7.0, 0.9))
    ax.set_xlim(0, 1)
    ax.set_ylim(0.2, 0.8)
    ax.axis("off")
    steps = ["1 Validate\n(Pydantic)", "2 Classify\n& route", "3 Hash ring\n→ shard", "4 Consistency\nlevel",
             "5 Execute on\nadapter", "6 Publish\ntelemetry", "7 Window +\nscore (async)"]
    w, gap = 0.118, 0.0253
    for i, s in enumerate(steps):
        x = 0.005 + i * (w + gap)
        _box(ax, x, 0.25, w, 0.5, s, S[0] if i < 6 else S[1], fs=6.6, tc="white")
        if i:
            _arrow(ax, x - gap + 0.001, 0.5, x - 0.001, 0.5)
    fig.savefig(path, dpi=220, bbox_inches="tight")
    return path


if __name__ == "__main__":
    out = ROOT / "reports" / "figures"
    out.mkdir(parents=True, exist_ok=True)
    print(architecture(out / "fig_architecture.png"))
    print(pipeline(out / "fig_pipeline.png"))
