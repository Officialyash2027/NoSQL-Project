"""Central configuration. Values come from environment variables prefixed ``HANF_`` or a ``.env``
file in the project root; every field has a default that works with ``docker compose up -d``."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from typing_extensions import Annotated

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HANF_", env_file=str(PROJECT_ROOT / ".env"), extra="ignore"
    )

    host: str = "127.0.0.1"
    port: int = 8000

    redis_host: str = "127.0.0.1"
    redis_ports: Annotated[list[int], NoDecode] = [7001, 7002, 7003, 7004, 7005]
    initial_shards: int = 4
    vnodes: int = 128
    redis_mode: str = "live"  # live | emulated
    redis_db: int = 0
    experiment_db: int = 1

    mongo_enabled: bool = True
    mongo_uri: str = "mongodb://127.0.0.1:27017"
    mongo_db: str = "hanf"
    cassandra_enabled: bool = True
    cassandra_hosts: Annotated[list[str], NoDecode] = ["127.0.0.1"]
    cassandra_port: int = 9042
    cassandra_keyspace: str = "hanf"
    connect_timeout_s: float = 2.0

    router_fallback_to_kv: bool = True

    security_window: int = 100
    security_batch: int = 20
    security_mode: str = "async"  # async | batched | sync | off
    security_contamination: float = 0.02
    security_seed: int = 42

    staleness_slo: float = 0.01
    telemetry_max_events: int = 5000

    # Optional overrides (used by the test-suite to keep generated files out of the project)
    state_dir: Optional[Path] = None    # models + runtime state (default: <root>/data)
    output_dir: Optional[Path] = None   # results + graphs (default: <root>)

    @field_validator("redis_ports", "cassandra_hosts", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        if isinstance(v, int):
            return [v]
        return v

    @field_validator("redis_mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in {"live", "emulated"}:
            raise ValueError("HANF_REDIS_MODE must be 'live' or 'emulated'")
        return v

    @field_validator("security_mode")
    @classmethod
    def _sec_mode(cls, v: str) -> str:
        if v not in {"async", "batched", "sync", "off"}:
            raise ValueError("HANF_SECURITY_MODE must be async, batched, sync or off")
        return v

    # Derived paths -------------------------------------------------------------------------
    @property
    def root(self) -> Path:
        return PROJECT_ROOT

    @property
    def models_dir(self) -> Path:
        return (self.state_dir or PROJECT_ROOT / "data") / "models"

    @property
    def runtime_dir(self) -> Path:
        return (self.state_dir or PROJECT_ROOT / "data") / "runtime"

    @property
    def results_dir(self) -> Path:
        return (self.output_dir or PROJECT_ROOT) / "results"

    @property
    def graphs_dir(self) -> Path:
        return (self.output_dir or PROJECT_ROOT) / "graphs"

    @property
    def configs_dir(self) -> Path:
        return PROJECT_ROOT / "configs"

    @property
    def frontend_dir(self) -> Path:
        return PROJECT_ROOT / "frontend"


@lru_cache
def get_settings() -> Settings:
    return Settings()
