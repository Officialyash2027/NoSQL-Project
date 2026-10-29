import numpy as np
import pytest

from app.security.detector import (IsolationForestDetector, StaticRuleDetector, detection_metrics)
from app.security.features import FEATURE_NAMES, RequestEvent, has_injection, window_features
from app.security.monitor import SecurityMonitor
from app.security.traffic import ATTACK_KINDS, DatasetSpec, TrafficGenerator
from app.telemetry.bus import TelemetryBus


def ev(i, op="read", key=None, size=100, failed=False, inj=False, ts=None):
    return RequestEvent("c1", ts if ts is not None else i * 0.1, op, key or f"k{i}", size, failed, inj)


def test_window_features_hand_computed():
    events = [ev(0, failed=True), ev(1, key="k0", inj=True), ev(2, op="scan", size=300), ev(3)]
    rate, div, payload, fail, inj, scan = window_features(events)
    assert rate == pytest.approx(3 / 0.3)
    assert div == pytest.approx(3 / 4)
    assert payload == pytest.approx(150)
    assert (fail, inj, scan) == (0.25, 0.25, 0.25)
    with pytest.raises(ValueError):
        window_features([])


@pytest.mark.parametrize("text,expected", [
    ('{"$ne": null}', True), ("1' OR '1'='1", True), ("x UNION SELECT y", True), ("a -- comment", True),
    ("plain product description", False), ("money: $5", False), ("Ships from our Union Square store", True),
])
def test_injection_signal(text, expected):
    assert has_injection(text) is expected


def test_generator_is_reproducible_and_labelled():
    a = TrafficGenerator(seed=5).make_window("injection")
    b = TrafficGenerator(seed=5).make_window("injection")
    assert [(r.operation, r.key, r.ts) for r in a] == [(r.operation, r.key, r.ts) for r in b]
    assert {r.label for r in a} == {"injection"} and len(a) == 100
    w, labels, subtle = TrafficGenerator(seed=1).dataset(10, 5, 0.4)
    assert len(w) == 25 and labels.count("normal") == 10
    assert sum(subtle) == 3 * 2  # round(0.4 * 5) subtle windows per attack type


def test_dataset_spec_values_deterministic():
    s = DatasetSpec()
    assert s.value_for("product:1") == s.value_for("product:1")
    assert 100 <= len(s.value_for("product:1")) <= 400
    assert len(s.value_for("product:7")) == s.value_size("product:7")


@pytest.fixture(scope="module")
def trained():
    gen = TrafficGenerator(seed=11)
    X = np.array([window_features([r.offline_event() for r in gen.make_window("normal")]) for _ in range(400)])
    return IsolationForestDetector(0.02, seed=11).fit(X)


def _feats(gen, kind, n, subtle=False):
    return np.array([window_features([r.offline_event() for r in gen.make_window(kind, subtle)]) for _ in range(n)])


def test_attacks_score_higher_than_normal(trained):
    gen = TrafficGenerator(seed=99)
    normal = trained.score(_feats(gen, "normal", 60))
    for kind in ATTACK_KINDS:
        attack = trained.score(_feats(gen, kind, 20))
        assert np.median(attack) > np.median(normal)
        assert trained.predict(_feats(gen, kind, 20)).mean() > 0.8
    assert trained.predict(_feats(gen, "normal", 100)).mean() < 0.1


def test_static_rules_catch_overt_but_miss_subtle():
    gen = TrafficGenerator(seed=3)
    rules = StaticRuleDetector()
    assert rules.predict(_feats(gen, "exfiltration", 20)).mean() > 0.9
    assert rules.predict(_feats(gen, "injection", 20, subtle=True)).mean() < 0.2
    assert rules.predict(_feats(gen, "normal", 50)).mean() == 0.0
    x = np.zeros(len(FEATURE_NAMES)); x[FEATURE_NAMES.index("scan_ratio")] = 0.9
    assert rules.explain(x) == ["scan_ratio = 0.9 > 0.5"]


def test_metrics_on_known_confusion():
    m = detection_metrics([True, True, False, False, True], ["injection", "normal", "normal", "bruteforce", "bruteforce"])
    assert (m.tp, m.fp, m.tn, m.fn) == (2, 1, 1, 1)
    assert m.precision == pytest.approx(2 / 3, abs=1e-4) and m.recall == pytest.approx(2 / 3, abs=1e-4)
    assert m.fpr == 0.5 and m.per_attack_recall == {"bruteforce": 0.5, "injection": 1.0}


def test_explanations_name_the_deviating_feature(trained):
    gen = TrafficGenerator(seed=4)
    x = _feats(gen, "exfiltration", 1)[0]
    assert trained.explain(x)[0]["feature"] in ("scan_ratio", "mean_payload", "key_diversity")


def test_model_save_load_and_tamper_detection(trained, tmp_path):
    p = tmp_path / "m.joblib"
    trained.save(p)
    loaded = IsolationForestDetector.load(p)
    X = _feats(TrafficGenerator(seed=8), "normal", 5)
    assert np.allclose(loaded.score(X), trained.score(X))
    p.write_bytes(p.read_bytes() + b"x")
    with pytest.raises(ValueError):
        IsolationForestDetector.load(p)


@pytest.mark.parametrize("mode", ["sync", "batched", "async"])
def test_monitor_windows_and_scores(trained, mode):
    bus = TelemetryBus()
    mon = SecurityMonitor(bus, window=100, batch=3, mode=mode)
    mon.detector = trained
    mon.start()
    gen = TrafficGenerator(seed=21)
    for kind in ("normal", "normal", "bruteforce"):
        for r in gen.make_window(kind, client_id=f"c-{kind}-{np.random.randint(1e9)}"):
            bus.publish("request", "test", client_id=r.client_id, client_ts=r.ts, operation=r.operation,
                        key=r.key, payload_bytes=r.expected_bytes,
                        status="denied" if r.expected_failed else "ok",
                        injection_signal=has_injection(r.key, r.value), synthetic_label=r.label)
    mon.flush()
    mon.stop()
    hist = mon.history()
    assert len(hist) == 3
    brute = [h for h in hist if h["synthetic_label"] == "bruteforce"][0]
    assert brute["verdict"] == "anomaly" and brute["triage"] == "unreviewed"
    assert mon.live_metrics()["synthetic_evaluation"]["windows"] == 3
    assert bus.snapshot()["security"]["windows_scored"] == 3
    mon.triage(brute["id"], "confirmed")
    assert mon.history(only="anomaly")[0]["triage"] == "confirmed"


def test_monitor_ignores_requests_without_client_id(trained):
    bus = TelemetryBus()
    mon = SecurityMonitor(bus, window=10, mode="sync")
    mon.detector = trained
    for i in range(30):
        bus.publish("request", "t", operation="read", key="k", status="ok")
    assert mon.stats["requests_observed"] == 0
