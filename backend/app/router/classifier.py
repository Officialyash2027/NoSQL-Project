"""Workload classifier for the Polyglot Router.

This is a deterministic, explainable weighted-rule scorer. Each rule that fires adds a weight to
one workload class (key-value, document, wide-column) and records a human-readable reason. The
class with the highest total wins; confidence = winning score / sum of scores. Rules and weights
live in ``configs/routing.yaml`` so they can be changed without touching code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CLASSES = ("key-value", "document", "wide-column")

DEFAULT_RULES: dict[str, Any] = {
    "classes": {"key-value": "redis", "document": "mongodb", "wide-column": "cassandra"},
    "weights": {"hint": 10.0, "key_prefix": 3.0, "op_only_kv": 6.0, "op_query": 6.0,
                "scalar_value": 2.0, "nested_document": 2.5, "flat_object": 1.0,
                "clustering_field": 3.5, "large_payload": 0.5, "default_kv": 0.5},
    "large_payload_bytes": 1024,
    "key_prefixes": {
        "key-value": ["user", "session", "cache", "cred", "product", "counter", "item", "kv", "ycsb"],
        "document": ["doc", "order", "profile", "article", "catalog", "customer", "invoice"],
        "wide-column": ["sensor", "ts", "metric", "event", "log", "reading", "timeseries"],
    },
}


@dataclass
class Classification:
    workload: str
    scores: dict[str, float]
    confidence: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"workload": self.workload, "scores": self.scores,
                "confidence": round(self.confidence, 3), "reasons": self.reasons}


def load_rules(path: Path | None) -> dict[str, Any]:
    if path and path.exists():
        data = yaml.safe_load(path.read_text()) or {}
        rules = json.loads(json.dumps(DEFAULT_RULES))
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(rules.get(k), dict):
                rules[k].update(v)
            else:
                rules[k] = v
        return rules
    return DEFAULT_RULES


def _depth(v: Any) -> int:
    if isinstance(v, dict):
        return 1 + max((_depth(x) for x in v.values()), default=0)
    if isinstance(v, list):
        return 1 + max((_depth(x) for x in v), default=0)
    return 0


class WorkloadClassifier:
    def __init__(self, rules: dict[str, Any] | None = None):
        self.rules = rules or DEFAULT_RULES
        self.w = self.rules["weights"]

    def classify(self, operation: str, key: str | None, value: Any = None,
                 filter: dict | None = None, hint: str | None = None) -> Classification:
        scores = {c: 0.0 for c in CLASSES}
        reasons: list[str] = []

        def add(cls: str, weight: float, why: str) -> None:
            scores[cls] += weight
            reasons.append(f"+{weight:g} {cls}: {why}")

        if hint:
            if hint not in CLASSES:
                raise ValueError(f"hint must be one of {CLASSES}")
            add(hint, self.w["hint"], f"client supplied hint={hint!r}")
        if operation in ("scan", "login"):
            add("key-value", self.w["op_only_kv"], f"operation {operation!r} exists only on the key-value engine")
        if operation == "query":
            add("document", self.w["op_query"], "field-filter query needs a document engine")
        if key:
            prefix = key.split(":", 1)[0].lower() if ":" in key else None
            if prefix:
                for cls, prefixes in self.rules["key_prefixes"].items():
                    if prefix in prefixes:
                        add(cls, self.w["key_prefix"], f"key prefix {prefix!r} is mapped to {cls}")
        if value is not None:
            if isinstance(value, (str, int, float, bool)):
                add("key-value", self.w["scalar_value"], "value is a scalar")
            elif isinstance(value, dict):
                if "ts" in value or "ck" in value:
                    add("wide-column", self.w["clustering_field"],
                        "object has a clustering field (ts/ck) - ordered rows in a partition")
                if _depth(value) >= 2:
                    add("document", self.w["nested_document"], "object contains nested objects/arrays")
                else:
                    add("document", self.w["flat_object"], "value is a flat JSON object")
            elif isinstance(value, list):
                add("document", self.w["nested_document"], "value is a JSON array")
            try:
                size = len(json.dumps(value))
            except (TypeError, ValueError):
                size = 0
            if size > self.rules["large_payload_bytes"]:
                add("document", self.w["large_payload"], f"payload is large ({size} bytes)")
        add("key-value", self.w["default_kv"], "default tie-breaker")

        workload = max(CLASSES, key=lambda c: (scores[c], -CLASSES.index(c)))
        total = sum(scores.values()) or 1.0
        return Classification(workload, {k: round(v, 2) for k, v in scores.items()},
                              scores[workload] / total, reasons)
