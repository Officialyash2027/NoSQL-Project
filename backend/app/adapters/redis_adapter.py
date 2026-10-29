"""Key-value adapter: Redis, sharded through the Adaptive Sharding Manager."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from ..sharding.manager import ShardManager
from .base import AdapterHealth, ExecResult, Operation, OperationRejected, StorageAdapter
from .redis_clients import server_version

CRED_PREFIX = "cred:"
MAX_SCAN = 200


def encode_value(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def decode_value(raw: bytes | None) -> Any:
    if raw is None:
        return None
    text = raw.decode("utf-8", "replace")
    if text[:1] in "{[":
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + ":" + password).encode()).hexdigest()


class RedisShardedAdapter(StorageAdapter):
    name = "redis"
    engine = "Redis"
    data_model = "key-value"
    capabilities = ("read", "write", "update", "delete", "scan", "login")

    AVAILABILITY_TTL_S = 2.0

    def __init__(self, shards: ShardManager, mode: str = "live"):
        self.shards = shards
        self.mode = mode
        self._avail_checked = 0.0
        self._avail = False

    @property
    def available(self) -> bool:
        """At least one active shard answers PING (cached for AVAILABILITY_TTL_S so the router
        does not add network round trips to every request)."""
        now = time.monotonic()
        if now - self._avail_checked < self.AVAILABILITY_TTL_S:
            return self._avail
        ok = False
        for sid in self.shards.active:
            try:
                self.shards.shards[sid].client.ping()
                ok = True
                break
            except Exception:  # noqa: BLE001
                continue
        self._avail, self._avail_checked = ok, now
        return ok

    def health(self, deep: bool = False) -> AdapterHealth:
        rows = self.shards.health()
        active = [r for r in rows if r["active"]]
        up = [r for r in active if r.get("up")]
        status = ("emulated" if self.mode == "emulated" else "connected") if len(up) == len(active) and active \
            else ("unavailable" if not up else "degraded")
        version = None
        verified = False
        lat = None
        if up:
            sh = self.shards.shards[up[0]["id"]]
            version = server_version(sh.client) if self.mode == "live" else "fakeredis (emulated)"
            if deep:
                t0 = time.perf_counter()
                k = b"hanf:healthcheck"
                sh.client.set(k, b"1")
                verified = sh.client.get(k) == b"1"
                sh.client.delete(k)
                lat = round((time.perf_counter() - t0) * 1000, 3)
        detail = f"{len(up)}/{len(active)} active shards reachable"
        if self.mode == "emulated":
            detail += " - IN-PROCESS EMULATION (fakeredis), not a live Redis server"
        return AdapterHealth(self.name, self.engine, self.data_model, status, detail, version, lat,
                             capabilities=list(self.capabilities), verified_round_trip=verified)

    # ------------------------------------------------------------------ operations
    def execute(self, op: Operation) -> ExecResult:
        if op.operation == "scan":
            return self._scan(op)
        if not op.key:
            raise OperationRejected("a key is required for this operation")
        if op.operation == "login":
            return self._login(op)
        shard = self.shards.shard_for(op.key)
        meta = {"shard": shard.id, "port": shard.port}
        key = op.key.encode("utf-8")
        c = shard.client
        if op.operation == "read":
            raw = c.get(key)
            if raw is None:
                fb = self.shards.fallback_shard_for(op.key)
                if fb is not None:
                    raw = fb.client.get(key)
                    meta["read_fallback_shard"] = fb.id
            if raw is None:
                return ExecResult("not_found", None, meta, "key not found")
            meta["response_bytes"] = len(raw)
            return ExecResult("ok", decode_value(raw), meta)
        if op.operation == "write":
            payload = encode_value(op.value)
            c.set(key, payload)
            meta["stored_bytes"], meta["response_bytes"] = len(payload), 0
            return ExecResult("ok", {"written": True}, meta)
        if op.operation == "update":
            payload = encode_value(op.value)
            updated = c.set(key, payload, xx=True)
            if not updated:
                fb = self.shards.fallback_shard_for(op.key)
                if fb is not None and fb.client.exists(key):
                    c.set(key, payload)
                    fb.client.delete(key)
                    updated = True
                    meta["moved_from_fallback"] = fb.id
            meta["stored_bytes"], meta["response_bytes"] = len(payload), 0
            if not updated:
                return ExecResult("not_found", None, meta, "update target does not exist")
            return ExecResult("ok", {"updated": True}, meta)
        if op.operation == "delete":
            n = c.delete(key)
            fb = self.shards.fallback_shard_for(op.key)
            if fb is not None:
                n += fb.client.delete(key)
            if not n:
                return ExecResult("not_found", None, meta, "key not found")
            return ExecResult("ok", {"deleted": n}, meta)
        raise OperationRejected(f"operation {op.operation!r} is not supported by the key-value engine")

    def _scan(self, op: Operation) -> ExecResult:
        pattern = op.key or "*"
        limit = max(1, min(op.limit or 50, MAX_SCAN))
        keys: list[str] = []
        per_shard: dict[str, int] = {}
        for sid in self.shards.active:
            n = 0
            for raw in self.shards.shards[sid].client.scan_iter(match=pattern.encode(), count=500):
                k = raw.decode("utf-8", "replace")
                if k.startswith(CRED_PREFIX):
                    continue  # credential records are never returned by SCAN
                keys.append(k)
                n += 1
                if len(keys) >= limit:
                    break
            per_shard[sid] = n
            if len(keys) >= limit:
                break
        return ExecResult("ok", {"keys": keys, "count": len(keys), "limit": limit},
                          {"shards_scanned": per_shard, "pattern": pattern,
                           "response_bytes": sum(len(k) for k in keys)})

    def _login(self, op: Operation) -> ExecResult:
        cred_key = CRED_PREFIX + op.key
        shard = self.shards.shard_for(cred_key)
        raw = shard.client.get(cred_key.encode())
        if raw is None:
            fb = self.shards.fallback_shard_for(cred_key)
            raw = fb.client.get(cred_key.encode()) if fb else None
        meta = {"shard": shard.id, "port": shard.port, "response_bytes": 0}
        if raw is None:
            return ExecResult("denied", {"authenticated": False}, meta, "unknown user")
        rec = json.loads(raw)
        ok = hmac.compare_digest(rec["hash"], hash_password(str(op.value or ""), rec["salt"]))
        if not ok:
            return ExecResult("denied", {"authenticated": False}, meta, "invalid credentials")
        return ExecResult("ok", {"authenticated": True}, meta)

    # ------------------------------------------------------------------ helpers
    def create_user(self, username: str, password: str, salt: str) -> None:
        key = CRED_PREFIX + username
        rec = {"salt": salt, "hash": hash_password(password, salt)}
        self.shards.shard_for(key).client.set(key.encode(), json.dumps(rec).encode())

    def raw_get(self, key: str) -> bytes | None:
        return self.shards.shard_for(key).client.get(key.encode())

