"""E3 - ML security monitor vs static rules on synthetic request windows.

Protocol (improves on Review II by separating model selection from testing):
* train: ``train_windows`` normal windows (unsupervised - no attack labels used);
* validation: normal + attack windows from an independent generator seed - used ONLY to pick the
  contamination value with the best F1;
* test: normal + attack windows from a third seed (30 % subtle attacks) - all reported metrics.
The whole protocol is repeated for several seeds to show run-to-run variability. An evasion sweep
measures recall as the attack intensity shrinks towards normal behaviour, and the scoring latency
is measured for single windows and for batches.
"""
from __future__ import annotations

import time

import numpy as np

from ..security.detector import IsolationForestDetector, StaticRuleDetector, detection_metrics
from ..security.features import FEATURE_NAMES, window_features
from ..security.traffic import ATTACK_KINDS, TrafficGenerator
from . import plotstyle
from .common import ExperimentContext


def _features(windows) -> np.ndarray:
    return np.array([window_features([r.offline_event() for r in w]) for w in windows])


def _split(seed: int, n_normal: int, n_attack: int, subtle: float):
    gen = TrafficGenerator(seed=seed)
    w, labels, sub = gen.dataset(n_normal, n_attack, subtle)
    return _features(w), labels, sub


def run_seed(cfg: dict, seed: int, progress) -> dict:
    progress(f"E3: seed {seed} - generating windows")
    gen = TrafficGenerator(seed=seed)
    X_train = _features([gen.make_window("normal") for _ in range(cfg["train_windows"])])
    X_val, y_val, s_val = _split(seed + 100, cfg["val_normal"], cfg["val_per_attack"], cfg["subtle_fraction"])
    X_test, y_test, s_test = _split(seed + 200, cfg["test_normal"], cfg["test_per_attack"], cfg["subtle_fraction"])

    rules = StaticRuleDetector()
    out = {"seed": seed, "n_train": len(X_train), "n_val": len(y_val), "n_test": len(y_test),
           "static_rules": detection_metrics(rules.predict(X_test), y_test, s_test).to_dict(),
           "isolation_forest": {}}
    best_c, best_f1 = None, -1.0
    detectors = {}
    for c in cfg["contaminations"]:
        det = IsolationForestDetector(c, seed=seed).fit(X_train)
        detectors[c] = det
        val = detection_metrics(det.predict(X_val), y_val, s_val)
        test = detection_metrics(det.predict(X_test), y_test, s_test)
        out["isolation_forest"][str(c)] = {"validation": val.to_dict(), "test": test.to_dict(),
                                          "threshold": det.threshold}
        if val.f1 > best_f1:
            best_c, best_f1 = c, val.f1
    out["selected_contamination"] = best_c
    out["selected_test"] = out["isolation_forest"][str(best_c)]["test"]
    det = detectors[best_c]

    # Score distributions for the plot (test set)
    scores = det.score(X_test)
    out["score_distribution"] = {k: [float(x) for x in np.percentile(scores[np.array(y_test) == k],
                                                                    [5, 25, 50, 75, 95])]
                                 for k in ("normal",) + ATTACK_KINDS}
    out["_scores"] = scores
    out["_labels"] = y_test
    out["_threshold"] = det.threshold

    # Evasion sweep: recall as attack intensity shrinks
    sweep = []
    sgen = TrafficGenerator(seed=seed + 300)
    for kind in ATTACK_KINDS:
        for level in cfg["intensity_levels"]:
            W = _features([sgen.make_window(kind, intensity=level) for _ in range(cfg["intensity_windows"])])
            sweep.append({"attack": kind, "intensity": level,
                          "if_recall": round(float(det.predict(W).mean()), 4),
                          "rules_recall": round(float(rules.predict(W).mean()), 4)})
    out["evasion_sweep"] = sweep

    # Scoring latency
    one = X_test[:1]
    for _ in range(5):
        det.score(one)
    t = []
    for i in range(cfg["latency_calls"]):
        s = time.perf_counter()
        det.score(X_test[i % len(X_test): i % len(X_test) + 1])
        t.append((time.perf_counter() - s) * 1000)
    tb = []
    for i in range(max(10, cfg["latency_calls"] // 10)):
        s = time.perf_counter()
        det.score(X_test[:20])
        tb.append((time.perf_counter() - s) * 1000)
    out["latency_ms"] = {"single_window_mean": round(float(np.mean(t)), 3),
                         "single_window_p95": round(float(np.percentile(t, 95)), 3),
                         "batch20_call_mean": round(float(np.mean(tb)), 3),
                         "batch20_per_window": round(float(np.mean(tb)) / 20, 3)}
    return out


def run(ctx: ExperimentContext, cfg: dict) -> dict:
    writer = ctx.store.new_run("e3", cfg, ctx.env())
    seeds = [run_seed(cfg, s, ctx.progress) for s in cfg["seeds"]]

    method_rows = []
    for s in seeds:
        method_rows.append({"seed": s["seed"], "method": "Static rules", **_flat(s["static_rules"])})
        for c, v in s["isolation_forest"].items():
            method_rows.append({"seed": s["seed"], "method": f"Isolation Forest (c={c})",
                                "selected_on_validation": float(c) == s["selected_contamination"],
                                **_flat(v["test"])})
    writer.write_csv("e3_test_metrics_by_seed.csv", method_rows)

    summary = []
    for m in dict.fromkeys(r["method"] for r in method_rows):
        rs = [r for r in method_rows if r["method"] == m]
        row = {"method": m, "seeds": len(rs),
               "times_selected": sum(bool(r.get("selected_on_validation")) for r in rs)}
        for f in ("precision", "recall", "f1", "fpr", "recall_injection", "recall_exfiltration",
                  "recall_bruteforce", "subtle_recall"):
            vals = np.array([r[f] for r in rs if r.get(f) is not None], dtype=float)
            row[f] = round(float(vals.mean()), 4) if vals.size else None
            row[f + "_sd"] = round(float(vals.std(ddof=1)), 4) if vals.size > 1 else 0.0
        summary.append(row)
    writer.write_csv("e3_summary.csv", summary)

    sel = [{"seed": s["seed"], "selected_contamination": s["selected_contamination"],
            **_flat(s["selected_test"])} for s in seeds]
    writer.write_csv("e3_selected_model.csv", sel)
    sweep = [dict(r, seed=s["seed"]) for s in seeds for r in s["evasion_sweep"]]
    writer.write_csv("e3_evasion_sweep.csv", sweep)
    lat = [dict(s["latency_ms"], seed=s["seed"]) for s in seeds]
    writer.write_csv("e3_scoring_latency.csv", lat)

    plot(writer, summary, seeds[0], sweep, cfg)
    for s in seeds:
        for k in ("_scores", "_labels", "_threshold"):
            s.pop(k, None)
    return writer.finish({"summary": summary, "selected": sel, "per_seed": seeds,
                          "evasion_sweep": sweep, "scoring_latency_ms": lat,
                          "features": FEATURE_NAMES,
                          "note": "Synthetic traffic only; not evidence of production accuracy."})


def _flat(m: dict) -> dict:
    d = {k: m[k] for k in ("precision", "recall", "f1", "fpr", "accuracy", "tp", "fp", "tn", "fn",
                           "subtle_recall")}
    for k, v in m["per_attack_recall"].items():
        d[f"recall_{k}"] = v
    return d


def plot(writer, summary: list[dict], first: dict, sweep: list[dict], cfg: dict) -> None:
    rows = [r for r in summary if r["method"] == "Static rules" or
            any(f"c={c})" in r["method"] for c in (0.01, 0.02, 0.05))]
    fig, ax = plotstyle.subplots(figsize=(7.2, 3.2))
    plotstyle.grouped_bars(ax, [r["method"].replace("Isolation Forest ", "IF ") for r in rows],
                           {"Precision": [r["precision"] for r in rows],
                            "Recall": [r["recall"] for r in rows],
                            "F1-score": [r["f1"] for r in rows]},
                           {"Precision": [r["precision_sd"] for r in rows],
                            "Recall": [r["recall_sd"] for r in rows],
                            "F1-score": [r["f1_sd"] for r in rows]})
    ax.set_ylim(0, 1.05)
    ax.set_title(f"E3 - Detection on held-out synthetic test windows (mean ± SD, {len(cfg['seeds'])} seeds)")
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    writer.add_graph(fig, "e3_detection.png")

    fig, axes = plotstyle.subplots(1, 2, figsize=(8.6, 3.2))
    ax = axes[0]
    scores, labels = first["_scores"], np.array(first["_labels"])
    kinds = ("normal",) + ATTACK_KINDS
    data = [scores[labels == k] for k in kinds]
    bp = ax.boxplot(data, vert=True, patch_artist=True, widths=0.4, showfliers=False,
                    medianprops={"color": plotstyle.INK}, whiskerprops={"color": plotstyle.INK_2},
                    capprops={"color": plotstyle.INK_2})
    for patch, col in zip(bp["boxes"], plotstyle.SERIES):
        patch.set_facecolor(col)
        patch.set_alpha(0.55)
        patch.set_edgecolor(col)
    ax.set_xticks(range(1, len(kinds) + 1), [k.capitalize() for k in kinds])
    ax.axhline(first["_threshold"], color=plotstyle.INK_2, lw=1, ls=(0, (3, 2)))
    ax.annotate("threshold", (len(kinds) + 0.4, first["_threshold"]), fontsize=7, color=plotstyle.INK_2,
                va="bottom", ha="right")
    ax.set_ylabel("Anomaly score")
    ax.set_title(f"(a) Score distribution, seed {first['seed']}")
    ax = axes[1]
    for i, kind in enumerate(ATTACK_KINDS):
        lv = cfg["intensity_levels"]
        rec = [np.mean([r["if_recall"] for r in sweep if r["attack"] == kind and r["intensity"] == l]) for l in lv]
        ax.plot([100 * l for l in lv], rec, marker="o", color=plotstyle.SERIES[i], label=kind.capitalize())
    ax.set_xscale("log")
    ax.set_xticks([100 * l for l in cfg["intensity_levels"]], [f"{100 * l:g}" for l in cfg["intensity_levels"]])
    ax.minorticks_off()
    ax.set_xlabel("Attack requests per window (%, log scale)")
    ax.set_ylabel("Isolation Forest recall")
    ax.set_ylim(-0.02, 1.05)
    ax.set_title("(b) Evasion sweep: recall vs attack intensity")
    ax.legend()
    fig.tight_layout()
    writer.add_graph(fig, "e3_scores_evasion.png")
