"""Shared telemetry bus.

An in-process publish/subscribe bus. Every module publishes timestamped events here and the
dashboard reads metrics that are *derived from those events* (nothing on the dashboard is a
separate counter that could drift from what actually happened).

Retention policy
----------------
* The last ``max_events`` events (default 5,000) are kept in a ring buffer; older ones are dropped.
* Request latencies used for percentiles are kept for the last ``latency_window`` requests.
* Cumulative counters (requests, errors, per-engine and per-shard counts) live for the lifetime of
  the process and are cleared by :meth:`reset`.
* Experiment results are persisted separately under ``results/`` by the experiment store.

Subscribers are called synchronously in the publisher's thread; an exception in a subscriber is
counted and logged but never propagates to the publisher.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import numpy as np

log = logging.getLogger("hanf.telemetry")


@dataclass
class TelemetryEvent:
    id: int
    ts: float
    type: str
    source: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Subscriber = Callable[[TelemetryEvent], None]

METRIC_SOURCES = {
    "total_requests": "Count of 'request' events published by the HANF pipeline since start/reset.",
    "error_rate": "'request' events with status=error divided by all 'request' events.",
    "throughput_10s": "'request' events with timestamp in the last 10 s, divided by 10.",
    "throughput_60s": "'request' events with timestamp in the last 60 s, divided by 60.",
    "latency_ms": "Percentiles of latency_ms over the last N 'request' events (N = latency window).",
    "by_engine": "Count of 'request' events grouped by the engine that executed them.",
    "shard_load": "Count of 'request' events grouped by Redis shard id (key-load signal).",
    "security": "Counts of 'security_window_scored' and 'security_alert' events.",
    "consistency": "Most recent 'consistency_profile' event published by the controller.",
}


class TelemetryBus:
    def __init__(self, max_events: int = 5000, latency_window: int = 2000):
        self._lock = threading.RLock()
        self._max_events = max_events
        self._latency_window = latency_window
        self._subscribers: dict[str, list[Subscriber]] = defaultdict(list)
        self.reset()

    # ------------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        with getattr(self, "_lock", threading.RLock()):
            self._events: deque[TelemetryEvent] = deque(maxlen=self._max_events)
            self._next_id = 1
            self._started = time.time()
            self._request_ts: deque[float] = deque(maxlen=50_000)
            self._latencies: deque[float] = deque(maxlen=self._latency_window)
            self._counts_by_type: Counter[str] = Counter()
            self._requests = 0
            self._errors = 0
            self._by_engine: Counter[str] = Counter()
            self._by_operation: Counter[str] = Counter()
            self._shard_load: Counter[str] = Counter()
            self._subscriber_errors = 0
            self._last: dict[str, TelemetryEvent] = {}

    # ------------------------------------------------------------------ pub/sub
    def subscribe(self, event_type: str, callback: Subscriber) -> None:
        """Register ``callback`` for ``event_type`` ('*' receives every event)."""
        with self._lock:
            self._subscribers[event_type].append(callback)

    def publish(self, event_type: str, source: str, **data: Any) -> TelemetryEvent:
        with self._lock:
            ev = TelemetryEvent(self._next_id, time.time(), event_type, source, data)
            self._next_id += 1
            self._events.append(ev)
            self._counts_by_type[event_type] += 1
            self._last[event_type] = ev
            if event_type == "request":
                self._requests += 1
                self._request_ts.append(ev.ts)
                if data.get("status") == "error":
                    self._errors += 1
                if "latency_ms" in data and data["latency_ms"] is not None:
                    self._latencies.append(float(data["latency_ms"]))
                if data.get("engine"):
                    self._by_engine[data["engine"]] += 1
                if data.get("operation"):
                    self._by_operation[data["operation"]] += 1
                if data.get("shard"):
                    self._shard_load[data["shard"]] += 1
            subs = list(self._subscribers.get(event_type, ())) + list(self._subscribers.get("*", ()))
        for cb in subs:  # outside the lock so subscribers may publish
            try:
                cb(ev)
            except Exception:  # noqa: BLE001 - a subscriber must never break the publisher
                self._subscriber_errors += 1
                log.exception("telemetry subscriber failed for %s", event_type)
        return ev

    # ------------------------------------------------------------------ queries
    def events(self, limit: int = 100, event_type: str | None = None,
               since_id: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            evs = list(self._events)
        if event_type:
            types = set(event_type.split(","))
            evs = [e for e in evs if e.type in types]
        if since_id is not None:
            evs = [e for e in evs if e.id > since_id]
        return [e.to_dict() for e in evs[-limit:]][::-1]

    def last(self, event_type: str) -> TelemetryEvent | None:
        with self._lock:
            return self._last.get(event_type)

    def shard_load(self) -> dict[str, int]:
        with self._lock:
            return dict(self._shard_load)

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            ts = np.fromiter(self._request_ts, dtype=float) if self._request_ts else np.empty(0)
            lat = np.fromiter(self._latencies, dtype=float) if self._latencies else np.empty(0)
            last_cons = self._last.get("consistency_profile")
            snap = {
                "uptime_s": round(now - self._started, 1),
                "total_requests": self._requests,
                "errors": self._errors,
                "error_rate": round(self._errors / self._requests, 4) if self._requests else 0.0,
                "throughput_10s": round(float((ts > now - 10).sum()) / 10, 2),
                "throughput_60s": round(float((ts > now - 60).sum()) / 60, 2),
                "latency_ms": {
                    "samples": int(lat.size),
                    "p50": round(float(np.percentile(lat, 50)), 3) if lat.size else None,
                    "p95": round(float(np.percentile(lat, 95)), 3) if lat.size else None,
                    "p99": round(float(np.percentile(lat, 99)), 3) if lat.size else None,
                    "mean": round(float(lat.mean()), 3) if lat.size else None,
                },
                "by_engine": dict(self._by_engine),
                "by_operation": dict(self._by_operation),
                "shard_load": dict(self._shard_load),
                "security": {
                    "windows_scored": self._counts_by_type.get("security_window_scored", 0),
                    "alerts": self._counts_by_type.get("security_alert", 0),
                },
                "consistency": last_cons.data if last_cons else None,
                "events_by_type": dict(self._counts_by_type),
                "events_retained": len(self._events),
                "retention_max_events": self._max_events,
                "subscriber_errors": self._subscriber_errors,
                "metric_sources": METRIC_SOURCES,
            }
        return snap
