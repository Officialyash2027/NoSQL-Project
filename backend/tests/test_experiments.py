"""Experiment modules run end-to-end on tiny configurations and write their artefacts."""
import json

from app.experiments import e2_sharding, e3_security, e4_consistency
from app.experiments.common import ExperimentContext, load_profile, zipf_sampler

import numpy as np


def test_profiles_load(emulated_settings):
    for exp in ("e1", "e2", "e3", "e4"):
        for prof in ("quick", "default", "large"):
            assert load_profile(emulated_settings, prof, exp)["profile"] == prof


def test_zipf_sampler_is_skewed_and_in_range():
    draw = zipf_sampler(1000, 0.99, 1)
    x = draw(np.random.default_rng(0), 20000)
    assert x.min() >= 0 and x.max() < 1000
    counts = np.bincount(x, minlength=1000)
    assert counts.max() > 20 * np.median(counts)


def test_e2_runs_on_emulated_redis(emulated_settings):
    cfg = load_profile(emulated_settings, "quick", "e2", {
        "remap_keys": 3000, "ring_seeds": 1, "vnode_options": [16, 128], "rebalance_keys": 1000,
        "rebalance_requests": 5000, "rebalance_rounds": 3, "zipf_exponents": [0.7], "live_keys": 1000})
    doc = e2_sharding.run(ExperimentContext(emulated_settings), cfg)
    assert doc["status"] == "completed", doc["failures"]
    lm = doc["results"]["live_migration"]
    assert lm["sample_verified"]["mismatches"] == 0 and lm["keys_not_on_ring_owner"] == 0
    assert lm["total_reconciled"]
    remap = {r["scheme"]: r for r in doc["results"]["remap"] if r["nodes"] == "4->5"}
    assert remap["modulo"]["remapped_pct_mean"] > 60 > remap["consistent v=128"]["remapped_pct_mean"]
    run_dir = emulated_settings.results_dir / "e2" / doc["run_id"]
    assert (run_dir / "e2_sharding.png").exists() and (run_dir / "metadata.json").exists()
    meta = json.loads((run_dir / "metadata.json").read_text())
    assert meta["engines"]["redis_mode"] == "emulated"


def test_e3_runs(emulated_settings):
    cfg = load_profile(emulated_settings, "quick", "e3", {
        "train_windows": 150, "val_normal": 60, "val_per_attack": 10, "test_normal": 100,
        "test_per_attack": 15, "seeds": [42], "latency_calls": 10, "intensity_levels": [0.05, 0.3],
        "intensity_windows": 5, "contaminations": [0.02, 0.05]})
    doc = e3_security.run(ExperimentContext(emulated_settings), cfg)
    assert doc["status"] == "completed"
    sel = doc["results"]["selected"][0]
    assert sel["selected_contamination"] in (0.02, 0.05)
    assert sel["recall"] > 0.7
    assert len(doc["graphs"]) == 2


def test_e4_runs(emulated_settings):
    cfg = load_profile(emulated_settings, "quick", "e4", {
        "phases": [1, 3, 1], "ops_per_phase": 200, "mc_samples": 200, "seeds": [1, 2],
        "read_delays_ms": [5, 15], "slos": [0.01]})
    doc = e4_consistency.run(ExperimentContext(emulated_settings), cfg)
    s = {r["strategy"]: r for r in doc["results"]["summary"]}
    assert s["static QUORUM/QUORUM"]["stale_pct"] == 0.0
    assert s["static ONE/ONE"]["stale_pct"] > 0
    assert s["HANF adaptive"]["seeds"] == 2
