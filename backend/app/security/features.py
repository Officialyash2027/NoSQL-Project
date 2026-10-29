"""Request-window features used by the ML Security Monitor (the six features from Review II)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

FEATURE_NAMES = [
    "request_rate",      # requests per second inside the window
    "key_diversity",     # distinct keys / requests
    "mean_payload",      # mean request + response bytes
    "failure_ratio",     # requests that failed (not found, denied, rejected, error)
    "injection_ratio",   # requests containing injection-style operators or SQL/NoSQL keywords
    "scan_ratio",        # SCAN / KEYS style enumeration requests
]

FEATURE_HELP = {
    "request_rate": "Requests per second in the window (from request timestamps).",
    "key_diversity": "Distinct keys divided by requests in the window.",
    "mean_payload": "Mean bytes per request (request value + response).",
    "failure_ratio": "Share of requests that failed: not found, denied, rejected or error.",
    "injection_ratio": "Share of requests whose key/value/filter contains operators such as $ne, "
                       "$where or keywords such as DROP, UNION, SELECT.",
    "scan_ratio": "Share of SCAN/KEYS enumeration requests.",
}

INJECTION_RE = re.compile(
    r"\$(?:ne|where|gt|gte|lt|lte|regex|or|nin|exists|expr)\b"
    r"|\b(?:drop|union|select|sleep)\b"
    r"|'\s*or\s*'"
    r"|\bor\s+1\s*=\s*1\b"
    r"|--",
    re.IGNORECASE,
)
SCAN_OPS = {"scan", "keys"}


def has_injection(*parts: Any) -> bool:
    for p in parts:
        if p is None:
            continue
        text = p if isinstance(p, str) else json.dumps(p, default=str)
        if INJECTION_RE.search(text):
            return True
    return False


@dataclass
class RequestEvent:
    """What the monitor keeps per request (derived from the 'request' telemetry event)."""
    client_id: str
    ts: float
    operation: str
    key: str
    payload_bytes: int
    failed: bool
    injection: bool
    label: str | None = None   # ground truth, only for generated synthetic traffic


def window_features(events: Sequence[RequestEvent]) -> np.ndarray:
    n = len(events)
    if n == 0:
        raise ValueError("empty window")
    ts = [e.ts for e in events]
    span = max(ts) - min(ts)
    rate = (n - 1) / span if span > 1e-6 else float(n) * 1000.0
    return np.array([
        rate,
        len({e.key for e in events}) / n,
        float(np.mean([e.payload_bytes for e in events])),
        sum(e.failed for e in events) / n,
        sum(e.injection for e in events) / n,
        sum(e.operation in SCAN_OPS for e in events) / n,
    ], dtype=float)
