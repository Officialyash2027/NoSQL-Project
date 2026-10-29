"""Load the experiment runs used by the report and derive every number quoted in the text.

All numbers in docs/EXPERIMENTS_AND_RESULTS.md and the IEEE paper come from here, i.e. from the
results.json files written by the experiment runner - nothing is typed in by hand.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.experiments.store import ExperimentStore  # noqa: E402

STORE = ExperimentStore(ROOT / "results", ROOT / "graphs")
DATA_DIR = ROOT / "reports" / "data"


def latest(exp: str, profile: str = "default") -> dict:
    for r in STORE.list_runs(exp):
        if r["status"] == "completed" and r["profile"] == profile:
            return STORE.get_run(r["run_id"])
    raise SystemExit(f"no completed '{profile}' run of {exp}; run scripts/run_experiments first")


def snapshot(runs: dict[str, dict], extra_run_ids=()) -> None:
    """Copy the CSV/JSON/PNG files of the runs used into reports/data/<run_id>/ (kept with the report)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for run_id in [d["run_id"] for d in runs.values()] + list(extra_run_ids):
        src = STORE.run_dir(run_id)
        dst = DATA_DIR / run_id
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    (DATA_DIR / "runs_used.json").write_text(json.dumps(
        {k: {"run_id": v["run_id"], "profile": v["config"]["profile"], "evidence": v["evidence"],
             "started_at": v["started_at"], "duration_s": v["duration_s"]} for k, v in runs.items()},
        indent=2))


E1_MAX_DIRECT_CV = 0.20  # selection rule fixed before the third E1 run was inspected


def _direct_cv(doc: dict) -> float:
    rows = [r for r in doc["results"]["summary"] if r["config"] == "direct"]
    return max(r["throughput_sd"] / r["throughput_mean"] for r in rows)


def e1_runs() -> list[dict]:
    out = []
    for r in STORE.list_runs("e1"):
        if r["status"] == "completed" and r["profile"] == "default":
            out.append(STORE.get_run(r["run_id"]))
    return out  # newest first


def select_e1() -> tuple[dict, list[dict]]:
    """Latest default E1 run whose direct-Redis throughput CV (SD/mean, worst workload) is below
    E1_MAX_DIRECT_CV; every default run is returned too, for the variability table."""
    runs = e1_runs()
    if not runs:
        raise SystemExit("no completed default E1 run")
    rows = []
    for d in runs:
        s = {(r["workload"], r["config"]): r for r in d["results"]["summary"]}
        rows.append({"run_id": d["run_id"], "direct_cv_max": round(_direct_cv(d), 3),
                     "duration_s": d["duration_s"],
                     **{f"{c}_{w}": s[(w, c)]["delta_vs_direct_pct"] for w in d["config"]["workloads"]
                        for c in ("hanf", "hanf_sync_ml", "hanf_batched_ml", "hanf_async_ml") if (w, c) in s}})
    chosen = next((d for d in runs if _direct_cv(d) < E1_MAX_DIRECT_CV), runs[0])
    for row, d in zip(rows, runs):
        row["used_in_report"] = row["run_id"] == chosen["run_id"]
        s = d["results"]["summary"]
        # robustness checks evaluated on every run
        row["sync_has_highest_p99_everywhere"] = all(
            max((r for r in s if r["workload"] == w), key=lambda r: r["p99_us"])["config"] == "hanf_sync_ml"
            for w in d["config"]["workloads"])
        row["hanf_p50_above_direct_everywhere"] = all(
            next(r for r in s if r["workload"] == w and r["config"] == "hanf")["p50_us"]
            > next(r for r in s if r["workload"] == w and r["config"] == "direct")["p50_us"]
            for w in d["config"]["workloads"])
    return chosen, rows


def load() -> dict:
    e1_doc, e1_all = select_e1()
    runs = {"e1": e1_doc} | {e: latest(e) for e in ("e2", "e3", "e4")}
    v: dict = {"runs": runs, "e1_all_runs": e1_all, "e1_rule": E1_MAX_DIRECT_CV}

    # ---------------------------------------------------------------- E1
    e1 = runs["e1"]["results"]
    v["e1_summary"] = e1["summary"]
    v["e1_overhead"] = e1.get("pipeline_overhead_us") or {}
    v["e1_overhead_source"] = runs["e1"]["run_id"]
    if "full_pipeline_read_no_io_us" not in v["e1_overhead"]:
        # older runs predate the no-I/O measurement: take it from the newest run that has it
        for d in e1_runs():
            o = d["results"].get("pipeline_overhead_us") or {}
            if "full_pipeline_read_no_io_us" in o:
                v["e1_overhead"], v["e1_overhead_source"] = o, d["run_id"]
                break
    by = {(r["workload"], r["config"]): r for r in e1["summary"]}
    v["e1_by"] = by
    wls = runs["e1"]["config"]["workloads"]
    v["e1_wls"] = wls

    def rng(cfg, field="delta_vs_direct_pct"):
        xs = [by[(w, cfg)][field] for w in wls if (w, cfg) in by]
        return min(xs), max(xs)

    v["e1_delta"] = {c: rng(c) for c in ("hanf", "hanf_sync_ml", "hanf_batched_ml", "hanf_async_ml")}
    v["e1_p99"] = {c: rng(c, "p99_us") for c in ("direct", "hanf", "hanf_sync_ml", "hanf_batched_ml", "hanf_async_ml")}
    v["e1_p50"] = {c: rng(c, "p50_us") for c in ("direct", "hanf")}
    v["e1_direct_tp"] = rng("direct", "throughput_mean")
    v["e1_batched_beats_sync"] = sum(by[(w, "hanf_batched_ml")]["throughput_mean"] > by[(w, "hanf_sync_ml")]["throughput_mean"] for w in wls)
    v["e1_async_beats_sync"] = sum(by[(w, "hanf_async_ml")]["throughput_mean"] > by[(w, "hanf_sync_ml")]["throughput_mean"] for w in wls)
    v["e1_backlog"] = max(by[(w, "hanf_async_ml")].get("mean_backlog_at_end", 0) for w in wls)
    env = dict(runs["e1"]["environment"])
    m = __import__("re").match(r"Windows 10 \((\d+)\.(\d+)\.(\d+)\)", env["os"])
    if m and int(m.group(3)) >= 22000:  # older metadata: platform.release() says '10' on Windows 11
        env["os"] = env["os"].replace("Windows 10", "Windows 11", 1)
    v["e1_env"] = env
    v["e1_cfg"] = runs["e1"]["config"]

    # ---------------------------------------------------------------- E2
    e2 = runs["e2"]["results"]
    v["e2_remap"] = e2["remap"]
    rm = {(r["scheme"], r["nodes"]): r for r in e2["remap"]}
    v["e2_rm"] = rm
    v["e2_reb"] = e2["rebalance"]
    v["e2_live"] = e2["live_migration"]
    v["e2_cfg"] = runs["e2"]["config"]

    def reb(exp):
        pts = [r for r in e2["rebalance"] if r["zipf_exponent"] == exp]
        return pts[0], pts[-1], min(pts, key=lambda r: r["max_over_mean"]), pts

    v["e2_reb07"] = reb(0.7)
    v["e2_reb099"] = reb(0.99)

    # ---------------------------------------------------------------- E3
    e3 = runs["e3"]["results"]
    v["e3_summary"] = {r["method"]: r for r in e3["summary"]}
    v["e3_selected"] = e3["selected"]
    v["e3_sel_c"] = sorted({s["selected_contamination"] for s in e3["selected"]})
    v["e3_latency"] = e3["scoring_latency_ms"]
    v["e3_sweep"] = e3["evasion_sweep"]
    v["e3_cfg"] = runs["e3"]["config"]
    import numpy as np
    sel = e3["selected"]
    v["e3_sel_mean"] = {k: float(np.mean([s[k] for s in sel])) for k in
                        ("precision", "recall", "f1", "fpr", "subtle_recall", "recall_injection",
                         "recall_exfiltration", "recall_bruteforce")}
    v["e3_sel_sd"] = {k: float(np.std([s[k] for s in sel], ddof=1)) if len(sel) > 1 else 0.0 for k in
                      ("precision", "recall", "f1", "fpr")}
    lat = e3["scoring_latency_ms"]
    v["e3_single_ms"] = float(np.mean([x["single_window_mean"] for x in lat]))
    v["e3_batch_per_window_ms"] = float(np.mean([x["batch20_per_window"] for x in lat]))

    def sweep(kind, level):
        xs = [r["if_recall"] for r in e3["evasion_sweep"] if r["attack"] == kind and r["intensity"] == level]
        return float(np.mean(xs))
    v["e3_sweep_fn"] = sweep

    # ---------------------------------------------------------------- E4
    e4 = runs["e4"]["results"]
    v["e4_summary"] = {r["strategy"]: r for r in e4["summary"]}
    v["e4_sens_delay"] = e4["sensitivity_read_delay"]
    v["e4_sens_slo"] = e4["sensitivity_slo"]
    v["e4_profiles"] = e4["adaptive_profile_counts_seed0"]
    v["e4_phases"] = e4["phases_seed0"]
    v["e4_cfg"] = runs["e4"]["config"]
    return v


def f(x, d=1):
    return f"{x:,.{d}f}"
