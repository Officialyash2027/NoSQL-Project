"""Shared fixtures.

Unit and API tests use in-process fakeredis shards (isolation, no external services). Tests marked
``live`` use the real Redis containers but on dedicated logical DBs (14/15) so the demo data in DB 0
is never touched; they are skipped when Redis is not reachable.
"""
from __future__ import annotations

import socket

import fakeredis
import pytest

from app.config import Settings
from app.sharding.manager import Shard, ShardManager
from app.telemetry.bus import TelemetryBus

LIVE_DB, LIVE_EXPERIMENT_DB = 14, 15


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture
def fake_shards():
    return [Shard(f"shard-{i}", "emulated", 7000 + i, fakeredis.FakeRedis(server=fakeredis.FakeServer()))
            for i in range(1, 6)]


@pytest.fixture
def telemetry():
    return TelemetryBus(max_events=1000)


@pytest.fixture
def manager(fake_shards, telemetry):
    return ShardManager(fake_shards, initial_active=4, vnodes=64, telemetry=telemetry)


@pytest.fixture
def emulated_settings(tmp_path):
    return Settings(redis_mode="emulated", mongo_enabled=False, cassandra_enabled=False,
                    security_mode="sync", state_dir=tmp_path / "state", output_dir=tmp_path / "out",
                    _env_file=None)


@pytest.fixture
def live_settings(tmp_path):
    if not all(port_open(p) for p in (7001, 7002, 7003, 7004, 7005)):
        pytest.skip("live Redis shards on 7001-7005 are not reachable")
    return Settings(redis_mode="live", redis_db=LIVE_DB, experiment_db=LIVE_EXPERIMENT_DB,
                    mongo_enabled=False, cassandra_enabled=False, security_mode="sync",
                    state_dir=tmp_path / "state", output_dir=tmp_path / "out", _env_file=None)


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """FastAPI TestClient running the full app on emulated Redis."""
    from fastapi.testclient import TestClient

    from app import config

    env = {"HANF_REDIS_MODE": "emulated", "HANF_MONGO_ENABLED": "false",
           "HANF_CASSANDRA_ENABLED": "false", "HANF_SECURITY_MODE": "sync",
           "HANF_STATE_DIR": str(tmp_path / "state"), "HANF_OUTPUT_DIR": str(tmp_path / "out")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        # the security model loads in a background thread; wait for it
        svc = app.state.service
        for _ in range(600):
            if svc.security.detector is not None:
                break
            import time
            time.sleep(0.05)
        yield client
    config.get_settings.cache_clear()
