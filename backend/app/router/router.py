"""Workload-aware Polyglot Router: turns a classification into an engine choice."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..adapters.base import AdapterUnavailable, StorageAdapter
from .classifier import Classification, WorkloadClassifier

KV_OPS = {"read", "write", "update", "delete", "scan", "login"}


@dataclass
class RoutingDecision:
    classification: Classification
    preferred_engine: str
    engine: str
    fallback_used: bool = False
    reason: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"classification": self.classification.to_dict(),
                "preferred_engine": self.preferred_engine, "engine": self.engine,
                "fallback_used": self.fallback_used, "reason": self.reason, "notes": self.notes}


class PolyglotRouter:
    def __init__(self, classifier: WorkloadClassifier, adapters: dict[str, StorageAdapter],
                 class_to_engine: dict[str, str], fallback_to_kv: bool = True, kv_engine: str = "redis"):
        self.classifier = classifier
        self.adapters = adapters
        self.class_to_engine = class_to_engine
        self.fallback_to_kv = fallback_to_kv
        self.kv_engine = kv_engine

    def route(self, operation: str, key: str | None, value=None, filter=None,
              hint: str | None = None, engine_override: str | None = None) -> RoutingDecision:
        cls = self.classifier.classify(operation, key, value, filter, hint)
        preferred = self.class_to_engine[cls.workload]
        if engine_override:
            if engine_override not in self.adapters:
                raise ValueError(f"unknown engine {engine_override!r}")
            preferred = engine_override
            cls.reasons.append(f"engine override -> {engine_override}")
        adapter = self.adapters.get(preferred)
        top = ", ".join(r.split(": ", 1)[1] for r in cls.reasons[:2] if ": " in r)
        if adapter is not None and adapter.available:
            if operation not in adapter.capabilities:
                raise ValueError(f"{adapter.engine} does not support operation {operation!r}")
            return RoutingDecision(cls, preferred, preferred,
                                   reason=f"classified as {cls.workload} ({top}); {adapter.engine} is available")
        status = adapter.health().status if adapter else "not configured"
        kv = self.adapters.get(self.kv_engine)
        if (self.fallback_to_kv and preferred != self.kv_engine and kv is not None and kv.available
                and operation in KV_OPS and operation in kv.capabilities):
            return RoutingDecision(
                cls, preferred, self.kv_engine, fallback_used=True,
                reason=f"classified as {cls.workload}, but {preferred} is {status}; "
                       f"fell back to the key-value engine (value stored as JSON)",
                notes=["Fallback writes stay in Redis; they are not copied to the preferred "
                       "engine when it becomes available."])
        raise AdapterUnavailable(f"preferred engine {preferred} is {status} and no fallback applies")
