"""Online ML Security Monitor.

Subscribes to 'request' telemetry events, keeps a sliding buffer of requests per client and turns
every ``window`` consecutive requests of a client into the six-feature vector. Windows are scored
by the Isolation Forest and, for comparison, by the static rules.

Scoring modes (HANF_SECURITY_MODE)
* ``sync``    - score each window inline as soon as it is complete (blocks that request);
* ``batched`` - queue windows and score ``batch`` of them inline in one call;
* ``async``   - a background worker scores queued windows in batches, off the request path;
* ``off``     - collect nothing.

Terminology kept separate on purpose:
* ``synthetic_label`` - what the traffic generator *intended* (ground truth, synthetic traffic only);
* ``verdict``         - what the model decided (anomaly / normal) with an anomaly score;
* ``triage``          - analyst decision (unreviewed / confirmed / dismissed). Only a human marks
  a window as a confirmed malicious event; an anomaly score is not proof of an attack.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

import numpy as np

from ..telemetry.bus import TelemetryBus, TelemetryEvent
from .detector import IsolationForestDetector, StaticRuleDetector, detection_metrics
from .features import FEATURE_NAMES, RequestEvent, window_features
from .traffic import TrafficGenerator

log = logging.getLogger("hanf.security")


class SecurityMonitor:
    def __init__(self, telemetry: TelemetryBus | None, window: int = 100, batch: int = 20,
                 mode: str = "async", contamination: float = 0.02, seed: int = 42,
                 model_path: Path | None = None, history: int = 2000):
        self.telemetry = telemetry
        self.window = window
        self.batch = batch
        self.mode = mode
        self.contamination = contamination
        self.seed = seed
        self.model_path = model_path
        self.detector: IsolationForestDetector | None = None
        self.rules = StaticRuleDetector()
        self.threshold_override: float | None = None
        self._buffers: dict[str, list[RequestEvent]] = defaultdict(list)
        self._queue: deque[dict] = deque()
        self._history: deque[dict] = deque(maxlen=history)
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._next_id = 1
        self.stats = Counter()
        self._batch_latencies: deque[float] = deque(maxlen=200)
        self._stop = False
        self._worker: threading.Thread | None = None
        if telemetry is not None:
            telemetry.subscribe("request", self.on_request_event)

    # ---------------------------------------------------------------- model lifecycle
    def ensure_model(self, n_train: int = 2000) -> dict:
        """Load the persisted model if valid, otherwise train on synthetic normal windows."""
        if self.model_path and self.model_path.exists():
            try:
                self.detector = IsolationForestDetector.load(self.model_path)
                return {"loaded": str(self.model_path), "n_train": self.detector.n_train}
            except Exception as exc:  # noqa: BLE001
                log.warning("could not load security model (%s); retraining", exc)
        return self.train(n_train=n_train)

    def train(self, n_train: int = 2000, contamination: float | None = None) -> dict:
        t0 = time.perf_counter()
        gen = TrafficGenerator(seed=self.seed, window=self.window)
        X = np.array([window_features([r.offline_event() for r in gen.make_window("normal")])
                      for _ in range(n_train)])
        det = IsolationForestDetector(contamination or self.contamination, seed=self.seed).fit(X)
        meta = det.save(self.model_path) if self.model_path else {}
        with self._lock:
            self.detector = det
            self.contamination = det.contamination
        info = {"trained": True, "n_train": n_train, "contamination": det.contamination,
                "threshold": round(det.threshold, 5), "train_seconds": round(time.perf_counter() - t0, 3),
                "model_file": meta.get("file"), "sha256": meta.get("sha256")}
        if self.telemetry:
            self.telemetry.publish("security_model_trained", "security", **info)
        return info

    # ---------------------------------------------------------------- ingestion
    def on_request_event(self, ev: TelemetryEvent) -> None:
        d = ev.data
        if self.mode == "off" or not d.get("client_id") or d.get("security_skip"):
            return
        self.observe(RequestEvent(
            client_id=d["client_id"], ts=float(d.get("client_ts") or ev.ts),
            operation=d.get("operation", "?"), key=str(d.get("key") or ""),
            payload_bytes=int(d.get("payload_bytes") or 0), failed=d.get("status") != "ok",
            injection=bool(d.get("injection_signal")), label=d.get("synthetic_label")))

    def observe(self, event: RequestEvent) -> None:
        with self._lock:
            buf = self._buffers[event.client_id]
            buf.append(event)
            self.stats["requests_observed"] += 1
            if len(buf) < self.window:
                return
            self._buffers[event.client_id] = []
            labels = Counter(e.label for e in buf if e.label)
            item = {"client_id": event.client_id, "features": window_features(buf),
                    "synthetic_label": labels.most_common(1)[0][0] if labels else None,
                    "window_start": buf[0].ts, "window_end": buf[-1].ts, "queued_at": time.time()}
            self._queue.append(item)
            self.stats["windows_queued"] += 1
            if self.mode == "sync":
                self._score_pending(max_items=1)
            elif self.mode == "batched" and len(self._queue) >= self.batch:
                self._score_pending(max_items=self.batch)
            elif self.mode == "async":
                self._cv.notify()

    # ---------------------------------------------------------------- scoring
    def _score_pending(self, max_items: int | None = None) -> int:
        with self._lock:
            if self.detector is None or not self._queue:
                return 0
            n = len(self._queue) if max_items is None else min(max_items, len(self._queue))
            items = [self._queue.popleft() for _ in range(n)]
            det = self.detector
        X = np.vstack([it["features"] for it in items])
        t0 = time.perf_counter()
        scores = det.score(X)
        thr = self.threshold_override if self.threshold_override is not None else det.threshold
        rule_hits = self.rules.predict(X)
        dt = (time.perf_counter() - t0) * 1000
        rows = []
        with self._lock:
            self._batch_latencies.append(dt)
            self.stats["batches_scored"] += 1
            for it, s, rh, x in zip(items, scores, rule_hits, X):
                anomalous = bool(s > thr)
                row = {
                    "id": self._next_id, "scored_at": time.time(), "client_id": it["client_id"],
                    "features": {n: round(float(v), 4) for n, v in zip(FEATURE_NAMES, x)},
                    "anomaly_score": round(float(s), 5), "threshold": round(float(thr), 5),
                    "verdict": "anomaly" if anomalous else "normal",
                    "rule_verdict": "alert" if rh else "pass",
                    "rule_reasons": self.rules.explain(x),
                    "explanation": det.explain(x),
                    "synthetic_label": it["synthetic_label"],
                    "triage": "unreviewed" if anomalous else None,
                    "queue_delay_ms": round((time.time() - it["queued_at"]) * 1000, 2),
                    "batch_size": len(items),
                }
                self._next_id += 1
                self._history.append(row)
                self.stats["windows_scored"] += 1
                self.stats["anomalies"] += anomalous
                rows.append(row)
        if self.telemetry:
            for row in rows:
                self.telemetry.publish("security_window_scored", "security", window_id=row["id"],
                                       client_id=row["client_id"], verdict=row["verdict"],
                                       score=row["anomaly_score"])
                if row["verdict"] == "anomaly":
                    self.telemetry.publish("security_alert", "security", window_id=row["id"],
                                           client_id=row["client_id"], score=row["anomaly_score"],
                                           top_feature=row["explanation"][0]["feature"])
        return len(items)

    def flush(self) -> int:
        """Score everything queued right now (any mode)."""
        total = 0
        while True:
            n = self._score_pending(self.batch)
            if not n:
                return total
            total += n

    # ---------------------------------------------------------------- async worker
    def start(self) -> None:
        if self.mode != "async" or (self._worker and self._worker.is_alive()):
            return
        self._stop = False
        self._worker = threading.Thread(target=self._run, name="hanf-security-worker", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        if self._worker:
            self._worker.join(timeout=2)

    def _run(self) -> None:
        while True:
            with self._cv:
                # Wake when a full batch is queued, or every 0.5 s to drain partial batches.
                self._cv.wait_for(lambda: self._stop or len(self._queue) >= self.batch, timeout=0.5)
                if self._stop:
                    return
            try:
                self._score_pending(self.batch)
            except Exception:  # noqa: BLE001
                log.exception("security scoring failed")
                self.stats["scoring_errors"] += 1

    # ---------------------------------------------------------------- queries
    def set_mode(self, mode: str) -> None:
        if mode not in {"async", "batched", "sync", "off"}:
            raise ValueError("mode must be async, batched, sync or off")
        self.mode = mode
        if mode == "async":
            self.start()

    def triage(self, window_id: int, decision: str) -> dict:
        if decision not in {"confirmed", "dismissed", "unreviewed"}:
            raise ValueError("decision must be confirmed, dismissed or unreviewed")
        with self._lock:
            for row in self._history:
                if row["id"] == window_id:
                    row["triage"] = decision
                    return row
        raise KeyError(window_id)

    def history(self, limit: int = 100, only: str | None = None) -> list[dict]:
        with self._lock:
            rows = list(self._history)
        if only == "anomaly":
            rows = [r for r in rows if r["verdict"] == "anomaly"]
        return rows[-limit:][::-1]

    def clear(self) -> None:
        with self._lock:
            self._history.clear()
            self._queue.clear()
            self._buffers.clear()
            self.stats.clear()

    def live_metrics(self) -> dict[str, Any]:
        """Agreement between verdicts and generator labels for scored *synthetic* windows."""
        with self._lock:
            rows = [r for r in self._history if r["synthetic_label"]]
            lat = list(self._batch_latencies)
            out: dict[str, Any] = {
                "mode": self.mode, "window": self.window, "batch": self.batch,
                "queue_length": len(self._queue),
                "partial_windows": {c: len(b) for c, b in self._buffers.items() if b},
                "stats": dict(self.stats),
                "model": None if self.detector is None else {
                    "contamination": self.detector.contamination, "threshold": self.detector.threshold,
                    "threshold_override": self.threshold_override, "n_train": self.detector.n_train,
                    "trained_at": self.detector.trained_at},
                "batch_scoring_ms": {"mean": round(float(np.mean(lat)), 3) if lat else None,
                                     "batches": len(lat)},
            }
        if rows:
            labels = [r["synthetic_label"] for r in rows]
            ml = detection_metrics([r["verdict"] == "anomaly" for r in rows], labels)
            rl = detection_metrics([r["rule_verdict"] == "alert" for r in rows], labels)
            out["synthetic_evaluation"] = {"windows": len(rows), "isolation_forest": ml.to_dict(),
                                           "static_rules": rl.to_dict(),
                                           "note": "Computed only on windows produced by the synthetic "
                                                   "traffic generator, whose labels are known."}
        return out
