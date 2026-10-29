"""Helpers shared by the experiment modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import yaml

from ..adapters.redis_clients import make_shards, server_version
from ..config import Settings
from .store import ExperimentStore, environment

Progress = Callable[[str], None]


def load_profile(settings: Settings, profile: str, experiment: str,
                 overrides: dict | None = None) -> dict:
    data = yaml.safe_load((settings.configs_dir / "experiments.yaml").read_text())
    if profile not in data:
        raise ValueError(f"unknown profile {profile!r}; choose from {list(data)}")
    cfg = dict(data[profile][experiment])
    cfg.update(overrides or {})
    cfg["profile"] = profile
    return cfg


@dataclass
class ExperimentContext:
    settings: Settings
    progress: Progress = field(default=lambda msg: None)

    @property
    def store(self) -> ExperimentStore:
        return ExperimentStore(self.settings.results_dir, self.settings.graphs_dir)

    def experiment_shards(self):
        """Redis clients on the dedicated experiment DB (never the live application DB)."""
        if self.settings.experiment_db == self.settings.redis_db:
            raise RuntimeError("experiment_db must differ from redis_db to protect live data")
        return make_shards(self.settings.redis_host, self.settings.redis_ports,
                           self.settings.experiment_db, self.settings.redis_mode,
                           self.settings.connect_timeout_s)

    def engine_info(self, shards=None) -> dict[str, Any]:
        info: dict[str, Any] = {"redis_mode": self.settings.redis_mode}
        if shards:
            info["redis_version"] = (server_version(shards[0].client)
                                     if self.settings.redis_mode == "live" else "fakeredis (emulated)")
            info["redis_endpoints"] = [f"{s.host}:{s.port}/db{self.settings.experiment_db}" for s in shards]
        return info

    def env(self, shards=None) -> dict:
        return environment(self.settings.root, self.engine_info(shards))


def flush_experiment_db(shards) -> None:
    for s in shards:
        try:
            s.client.flushdb()
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass


def zipf_sampler(n_items: int, theta: float, seed: int):
    """Scrambled Zipfian sampler over item indices 0..n-1 (YCSB-style key popularity)."""
    ranks = np.arange(1, n_items + 1, dtype=float)
    p = 1.0 / ranks**theta
    cdf = np.cumsum(p / p.sum())
    perm = np.random.default_rng(seed).permutation(n_items)

    def draw(rng: np.random.Generator, size: int) -> np.ndarray:
        idx = np.searchsorted(cdf, rng.random(size))
        return perm[np.minimum(idx, n_items - 1)]

    return draw


def summarize_latencies(lat_us: np.ndarray) -> dict[str, float]:
    return {"p50_us": float(np.percentile(lat_us, 50)), "p95_us": float(np.percentile(lat_us, 95)),
            "p99_us": float(np.percentile(lat_us, 99)), "mean_us": float(lat_us.mean())}
