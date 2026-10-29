"""Pydantic request models (input validation for the REST API)."""
from __future__ import annotations

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

Operation = Literal["read", "write", "update", "delete", "query", "scan", "login"]
Workload = Literal["key-value", "document", "wide-column"]
Engine = Literal["redis", "mongodb", "cassandra"]
Level = Literal["ONE", "QUORUM", "ALL"]
MAX_VALUE_BYTES = 64 * 1024


class ExecuteRequest(BaseModel):
    operation: Operation
    key: Optional[str] = Field(None, max_length=512)
    value: Any = None
    filter: Optional[dict[str, Any]] = None
    limit: int = Field(50, ge=1, le=200)
    hint: Optional[Workload] = Field(None, description="Optional workload hint for the router")
    engine: Optional[Engine] = Field(None, description="Force an engine (bypasses classification)")
    consistency: Optional[Level] = Field(None, description="Override the controller's level")
    client_id: Optional[str] = Field(None, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$",
                                     description="Enables per-client security windows")
    client_ts: Optional[float] = Field(None, description="Client timestamp (synthetic traffic)")
    synthetic_label: Optional[str] = Field(None, max_length=32)

    @model_validator(mode="after")
    def _check(self):
        if self.operation not in ("scan", "query") and not self.key:
            raise ValueError(f"'key' is required for operation {self.operation!r}")
        if self.operation in ("write", "update", "login") and self.value is None:
            raise ValueError(f"'value' is required for operation {self.operation!r}")
        if self.operation == "query" and not self.filter:
            raise ValueError("'filter' is required for operation 'query'")
        if self.value is not None and len(json.dumps(self.value, default=str)) > MAX_VALUE_BYTES:
            raise ValueError(f"value larger than {MAX_VALUE_BYTES} bytes")
        return self


class SeedRequest(BaseModel):
    reset: bool = Field(False, description="FLUSHDB the HANF Redis DB on all shards first")
    include_optional: bool = True


class ShardActionRequest(BaseModel):
    action: Literal["add_shard", "remove_shard", "rebalance_by_load", "reset", "retry"] = "add_shard"
    shard_id: Optional[str] = Field(None, pattern=r"^shard-\d+$")
    alpha: float = Field(0.5, gt=0, le=1)
    min_requests: int = Field(200, ge=1)


class TrafficRequest(BaseModel):
    normal_windows: int = Field(6, ge=0, le=100)
    attack_windows: int = Field(2, ge=0, le=50, description="Windows per selected attack type")
    attack_kinds: list[Literal["injection", "exfiltration", "bruteforce"]] = \
        ["injection", "exfiltration", "bruteforce"]
    subtle_fraction: float = Field(0.3, ge=0, le=1)
    seed: Optional[int] = None


class TrainRequest(BaseModel):
    contamination: float = Field(0.02, gt=0, le=0.5)
    n_train: int = Field(2000, ge=100, le=20000)


class SecuritySettingsRequest(BaseModel):
    mode: Optional[Literal["async", "batched", "sync", "off"]] = None
    threshold_override: Optional[float] = Field(None, description="null restores the model threshold")
    clear_threshold: bool = False


class TriageRequest(BaseModel):
    decision: Literal["confirmed", "dismissed", "unreviewed"]


class NetworkModelIn(BaseModel):
    regions: list[str] = ["A", "B", "C"]
    writer_rtt_ms: list[float] = [2.0, 30.0, 110.0]
    reader_rtt_ms: list[float] = [30.0, 2.0, 80.0]
    jitter_sigma: float = Field(0.25, ge=0, le=2)
    read_delay_mean_ms: float = Field(15.0, gt=0, le=10_000)

    @model_validator(mode="after")
    def _lens(self):
        n = len(self.regions)
        if not 1 <= n <= 9:
            raise ValueError("between 1 and 9 regions")
        if len(self.writer_rtt_ms) != n or len(self.reader_rtt_ms) != n:
            raise ValueError("writer_rtt_ms and reader_rtt_ms need one value per region")
        if any(x <= 0 for x in self.writer_rtt_ms + self.reader_rtt_ms):
            raise ValueError("RTTs must be positive")
        return self


class ConsistencySimRequest(BaseModel):
    phases: list[float] = [1, 1, 1, 3, 3, 6, 6, 3, 1, 1, 1, 6, 1, 1, 3, 1]
    ops_per_phase: int = Field(4000, ge=100, le=20000)
    mc_samples: int = Field(2000, ge=100, le=10000)
    slo: float = Field(0.01, ge=0, le=0.5)
    seed: int = 2024
    include_oracle: bool = True
    network: NetworkModelIn = NetworkModelIn()

    @field_validator("phases")
    @classmethod
    def _phases(cls, v):
        if not 1 <= len(v) <= 64 or any(not 0.1 <= x <= 20 for x in v):
            raise ValueError("1-64 phases, each congestion multiplier between 0.1 and 20")
        return v


class CongestionRequest(BaseModel):
    congestion: float = Field(..., gt=0, le=20)


class BenchmarkRequest(BaseModel):
    experiment: Literal["e1", "e2", "e3", "e4", "all"] = "e4"
    profile: Literal["quick", "default", "large"] = "quick"
    overrides: dict[str, Any] = {}
