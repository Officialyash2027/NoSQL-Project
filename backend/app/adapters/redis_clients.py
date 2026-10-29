"""Factory for Redis shard clients.

``live`` mode connects to real Redis servers (docker compose, ports 7001-7005).
``emulated`` mode creates one independent in-process fakeredis server per shard so the UI and the
algorithms can be shown on a machine without Redis. Emulated mode is always reported as such in
/health and in experiment metadata; it is never presented as a live Redis measurement.
"""
from __future__ import annotations

import redis

from ..sharding.manager import Shard


def make_shards(host: str, ports: list[int], db: int = 0, mode: str = "live",
                timeout_s: float = 2.0) -> list[Shard]:
    shards = []
    if mode == "emulated":
        import fakeredis

        servers = {}
        for i, port in enumerate(ports, start=1):
            servers.setdefault(port, fakeredis.FakeServer())
            client = fakeredis.FakeRedis(server=servers[port], db=db)
            shards.append(Shard(f"shard-{i}", "emulated", port, client))
        return shards
    for i, port in enumerate(ports, start=1):
        client = redis.Redis(host=host, port=port, db=db, socket_timeout=timeout_s,
                             socket_connect_timeout=timeout_s, decode_responses=False,
                             health_check_interval=30)
        shards.append(Shard(f"shard-{i}", host, port, client))
    return shards


def server_version(client) -> str | None:
    try:
        return client.info("server").get("redis_version")
    except Exception:  # noqa: BLE001
        return None
