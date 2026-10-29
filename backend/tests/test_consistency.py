import numpy as np
import pytest

from app.consistency.simulator import (PROFILES, AdaptiveConsistencyController, NetworkModel,
                                       level_count, run_timeline, sample)
from app.telemetry.bus import TelemetryBus

M = NetworkModel()


def test_level_counts():
    assert [level_count(l, 3) for l in ("ONE", "QUORUM", "ALL")] == [1, 2, 3]
    assert level_count("QUORUM", 5) == 3
    with pytest.raises(ValueError):
        level_count("TWO", 3)


def test_overlapping_quorums_are_never_stale():
    rng = np.random.default_rng(0)
    for w, r in [("QUORUM", "QUORUM"), ("ALL", "ONE"), ("ONE", "ALL"), ("QUORUM", "ONE"), ("ONE", "QUORUM")]:
        assert sample(M, w, r, 3.0, 5000, rng).stale.sum() == 0, (w, r)


def test_one_one_is_often_stale_cross_region():
    s = sample(M, "ONE", "ONE", 1.0, 20000, np.random.default_rng(1))
    assert 0.3 < s.stale.mean() < 0.95


def test_latency_ordering_and_congestion_effect():
    rng = np.random.default_rng(2)
    lat = {lvl: sample(M, lvl, lvl, 1.0, 5000, rng).summary()["mean_ms"] for lvl in ("ONE", "QUORUM", "ALL")}
    assert lat["ONE"] < lat["QUORUM"] < lat["ALL"]
    q1 = sample(M, "QUORUM", "QUORUM", 1.0, 5000, rng).summary()["mean_ms"]
    q6 = sample(M, "QUORUM", "QUORUM", 6.0, 5000, rng).summary()["mean_ms"]
    assert q6 > 4 * q1


def test_write_latency_is_wth_smallest_rtt_without_jitter():
    m = NetworkModel(jitter_sigma=0.0)
    s = sample(m, "QUORUM", "ONE", 1.0, 10, np.random.default_rng(0))
    assert np.allclose(s.write_ms, 30.0)       # 2nd smallest of (2, 30, 110)
    assert np.allclose(s.read_ms, 2.0)         # reader's closest replica (B)


def test_selection_respects_slo_and_prefers_low_latency():
    c = AdaptiveConsistencyController(slo=0.01, samples=2000, seed=3)
    chosen, preds, fb = c.select(1.0)
    assert not fb
    best = min((p for p in preds if p.eligible), key=lambda p: p.mean_ms)
    assert "/".join(chosen) == best.profile
    assert next(p for p in preds if p.profile == "ONE/ONE").eligible is False


def test_fallback_when_no_profile_meets_slo():
    c = AdaptiveConsistencyController(slo=0.0, samples=500, profiles=[("ONE", "ONE")])
    chosen, _, fb = c.select(1.0)
    assert fb and chosen == ("ALL", "ALL")


def test_controller_update_publishes_and_sets_levels():
    bus = TelemetryBus()
    c = AdaptiveConsistencyController(samples=500, telemetry=bus)
    st = c.update(6.0, source="test")
    assert bus.last("consistency_profile").data["profile"] == st["profile"]
    w, r = st["profile"].split("/")
    assert c.level_for("read") == r and c.level_for("write") == w
    with pytest.raises(ValueError):
        c.update(0)


def test_timeline_is_deterministic_and_complete():
    a = run_timeline(M, [1, 3, 6], 400, 0.01, 300, seed=9)
    b = run_timeline(M, [1, 3, 6], 400, 0.01, 300, seed=9)
    assert a["strategies"] == b["strategies"]
    assert set(a["strategies"]) == {"static ONE/ONE", "static QUORUM/QUORUM", "static ALL/ALL",
                                    "HANF adaptive", "adaptive (oracle)"}
    ad = a["strategies"]["HANF adaptive"]
    assert ad["stale_pct"] <= 1.0
    assert ad["mean_ms"] < a["strategies"]["static QUORUM/QUORUM"]["mean_ms"]
    assert len([p for p in a["phases"] if p["strategy"] == "HANF adaptive"]) == 3


def test_network_model_validation():
    with pytest.raises(ValueError):
        NetworkModel(regions=["A", "B"], writer_rtt_ms=[1, 2, 3], reader_rtt_ms=[1, 2])
    assert len(PROFILES) == 7
