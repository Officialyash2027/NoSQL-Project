"""Isolation Forest detector, static-rule baseline, evaluation metrics and safe persistence."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import joblib
import numpy as np

from .features import FEATURE_NAMES

STATIC_RULES = {"injection_ratio": 0.2, "failure_ratio": 0.5, "scan_ratio": 0.5, "request_rate": 150.0}


class StaticRuleDetector:
    """Conventional threshold rules (Review II baseline): alert if any threshold is exceeded."""

    def __init__(self, thresholds: dict[str, float] | None = None):
        self.thresholds = thresholds or dict(STATIC_RULES)
        self._idx = {name: FEATURE_NAMES.index(name) for name in self.thresholds}

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(X)
        hit = np.zeros(len(X), dtype=bool)
        for name, thr in self.thresholds.items():
            hit |= X[:, self._idx[name]] > thr
        return hit

    def explain(self, x: np.ndarray) -> list[str]:
        return [f"{n} = {x[i]:.3g} > {self.thresholds[n]:g}" for n, i in self._idx.items()
                if x[i] > self.thresholds[n]]


class IsolationForestDetector:
    """z-score standardisation (training mean/std) followed by an Isolation Forest.

    A window is flagged when its anomaly score (``-score_samples``; higher = more anomalous)
    exceeds ``threshold``. By default the threshold is the one implied by ``contamination`` on the
    training data; it can be overridden for a different precision/recall trade-off.
    """

    def __init__(self, contamination: float = 0.02, n_estimators: int = 100, seed: int = 42):
        self.contamination = contamination
        self.n_estimators = n_estimators
        self.seed = seed
        self.mean_ = self.std_ = None
        self.model = None  # sklearn.ensemble.IsolationForest (imported lazily: SciPy is slow to load)
        self.threshold: float | None = None
        self.trained_at: float | None = None
        self.n_train = 0

    def fit(self, X: np.ndarray) -> "IsolationForestDetector":
        from sklearn.ensemble import IsolationForest

        X = np.asarray(X, dtype=float)
        self.mean_ = X.mean(axis=0)
        self.std_ = X.std(axis=0)
        self.std_[self.std_ < 1e-9] = 1.0
        self.model = IsolationForest(n_estimators=self.n_estimators, contamination=self.contamination,
                                     random_state=self.seed)
        self.model.fit(self._z(X))
        self.threshold = float(-self.model.offset_)
        self.trained_at = time.time()
        self.n_train = len(X)
        return self

    def _z(self, X: np.ndarray) -> np.ndarray:
        return (np.atleast_2d(X) - self.mean_) / self.std_

    @property
    def fitted(self) -> bool:
        return self.model is not None

    def score(self, X: np.ndarray) -> np.ndarray:
        return -self.model.score_samples(self._z(X))

    def predict(self, X: np.ndarray, threshold: float | None = None) -> np.ndarray:
        thr = self.threshold if threshold is None else threshold
        return self.score(X) > thr

    def explain(self, x: np.ndarray, top: int = 3) -> list[dict]:
        """Features that deviate most from the training distribution (|z|), largest first.
        This is a deviation summary, not a causal attribution of the forest's decision."""
        z = self._z(x)[0]
        order = np.argsort(-np.abs(z))[:top]
        return [{"feature": FEATURE_NAMES[i], "value": round(float(x[i]), 4),
                 "z": round(float(z[i]), 2), "direction": "high" if z[i] > 0 else "low"}
                for i in order]

    # ------------------------------------------------------------ persistence
    def save(self, path: Path) -> dict:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"mean": self.mean_, "std": self.std_, "model": self.model,
                     "threshold": self.threshold, "contamination": self.contamination,
                     "n_estimators": self.n_estimators, "seed": self.seed,
                     "trained_at": self.trained_at, "n_train": self.n_train}, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        meta = {"file": path.name, "sha256": digest, "features": FEATURE_NAMES,
                "contamination": self.contamination, "threshold": self.threshold,
                "n_train": self.n_train, "trained_at": self.trained_at}
        path.with_suffix(".json").write_text(json.dumps(meta, indent=2))
        return meta

    @classmethod
    def load(cls, path: Path) -> "IsolationForestDetector":
        """Load a model written by :meth:`save`. joblib uses pickle, so only files whose SHA-256
        matches the sidecar written by this application are accepted."""
        meta_path = path.with_suffix(".json")
        if not path.exists() or not meta_path.exists():
            raise FileNotFoundError(path)
        meta = json.loads(meta_path.read_text())
        if hashlib.sha256(path.read_bytes()).hexdigest() != meta.get("sha256"):
            raise ValueError("model checksum mismatch - refusing to load")
        if meta.get("features") != FEATURE_NAMES:
            raise ValueError("model was trained on a different feature set")
        d = joblib.load(path)
        det = cls(d["contamination"], d["n_estimators"], d["seed"])
        det.mean_, det.std_, det.model = d["mean"], d["std"], d["model"]
        det.threshold, det.trained_at, det.n_train = d["threshold"], d["trained_at"], d["n_train"]
        return det


# ---------------------------------------------------------------- metrics
@dataclass
class DetectionMetrics:
    precision: float
    recall: float
    f1: float
    fpr: float
    accuracy: float
    tp: int
    fp: int
    tn: int
    fn: int
    per_attack_recall: dict[str, float]
    subtle_recall: float | None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def detection_metrics(pred: Sequence[bool], labels: Sequence[str],
                      subtle: Sequence[bool] | None = None) -> DetectionMetrics:
    pred = np.asarray(pred, dtype=bool)
    labels = np.asarray(labels)
    y = labels != "normal"
    tp = int((pred & y).sum()); fp = int((pred & ~y).sum())
    tn = int((~pred & ~y).sum()); fn = int((~pred & y).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    per = {}
    for kind in sorted(set(labels) - {"normal"}):
        m = labels == kind
        per[kind] = round(float(pred[m].mean()), 4) if m.any() else None
    sub = None
    if subtle is not None:
        s = np.asarray(subtle, dtype=bool) & y
        sub = round(float(pred[s].mean()), 4) if s.any() else None
    return DetectionMetrics(round(precision, 4), round(recall, 4), round(f1, 4), round(fpr, 4),
                            round(float((pred == y).mean()), 4), tp, fp, tn, fn, per, sub)
