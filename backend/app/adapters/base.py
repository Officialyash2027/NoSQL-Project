"""Common adapter interface used by the Polyglot Router."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class AdapterUnavailable(RuntimeError):
    """The engine is not connected (raised before any operation is attempted)."""


class OperationRejected(ValueError):
    """The request is valid JSON but not acceptable for this engine (e.g. operator injection)."""


@dataclass
class Operation:
    operation: str                      # read | write | update | delete | query | scan | login
    key: str | None = None
    value: Any = None
    filter: dict | None = None
    limit: int = 50
    consistency: str | None = None      # ONE | QUORUM | ALL (applied where the engine supports it)


@dataclass
class ExecResult:
    status: str                         # ok | not_found | denied | rejected | error
    data: Any = None
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def is_failure(self) -> bool:
        return self.status != "ok"


@dataclass
class AdapterHealth:
    name: str
    engine: str
    data_model: str
    status: str                         # connected | emulated | unavailable | disabled | connecting
    detail: str = ""
    version: str | None = None
    latency_ms: float | None = None
    checked_at: float = field(default_factory=time.time)
    capabilities: list[str] = field(default_factory=list)
    verified_round_trip: bool = False   # True only after a real write+read+delete succeeded

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class StorageAdapter(ABC):
    name: str
    engine: str
    data_model: str
    capabilities: tuple[str, ...] = ()

    @abstractmethod
    def health(self, deep: bool = False) -> AdapterHealth: ...

    @abstractmethod
    def execute(self, op: Operation) -> ExecResult: ...

    @property
    @abstractmethod
    def available(self) -> bool: ...

    def connect(self) -> None:  # optional engines override
        return None

    def close(self) -> None:
        return None
