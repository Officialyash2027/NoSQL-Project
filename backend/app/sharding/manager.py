"""Adaptive Sharding Manager for live Redis shards.

Responsibilities
* map keys to shards with a consistent-hash ring (``hashring.ConsistentHashRing``);
* add / remove shards and migrate exactly the keys whose owner changes;
* adapt virtual-node weights from observed per-shard request load (Review II rule);
* report key distribution, health and migration results.

Migration protocol (per key whose owner changes)
1. ``DUMP`` the key on the source shard (and its remaining TTL).
2. ``RESTORE`` it on the destination *without* REPLACE. If the destination already holds the key,
   that value was written after the ring switched (all writes go to the new owner), so it is newer
   and is kept; the stale source copy is dropped. Re-running a migration is therefore idempotent.
3. Verify: the destination ``DUMP`` must be byte-identical to the source ``DUMP`` (or be the newer
   value described above).
4. Only after successful verification is the source copy deleted.
Keys that fail any step stay on the source; the previous ring is kept for read fallback so they
remain readable, and the report lists them. ``retry_migration`` re-runs the remaining work.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..telemetry.bus import TelemetryBus
from .hashring import ConsistentHashRing, adaptive_vnode_update, coefficient_of_variation, imbalance

log = logging.getLogger("hanf.sharding")


@dataclass
class Shard:
    id: str
    host: str
    port: int
    client: Any  # redis.Redis (bytes responses)

    def describe(self) -> dict:
        return {"id": self.id, "host": self.host, "port": self.port}


@dataclass
class MigrationReport:
    action: str
    from_shards: list[str]
    to_shards: list[str]
    started_at: float = field(default_factory=time.time)
    elapsed_s: float = 0.0
    scanned: int = 0
    to_move: int = 0
    moved: int = 0
    verified: int = 0
    already_present: int = 0
    kept_newer_destination: int = 0
    failed: int = 0
    failed_keys: list[str] = field(default_factory=list)
    keys_before: dict[str, int] = field(default_factory=dict)
    keys_after: dict[str, int] = field(default_factory=dict)
    total_before: int = 0
    total_after: int = 0
    moves_by_route: dict[str, int] = field(default_factory=dict)
    sample_checks: list[dict] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    status: str = "running"

    @property
    def remapped_pct(self) -> float:
        return round(100.0 * self.to_move / self.total_before, 2) if self.total_before else 0.0

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["remapped_pct"] = self.remapped_pct
        d["count_reconciled"] = self.total_before == self.total_after
        return d


class ShardManager:
    BATCH = 500

    def __init__(self, shards: list[Shard], initial_active: int, vnodes: int,
                 telemetry: TelemetryBus | None = None, topology_path: Path | None = None,
                 key_match: str = "*"):
        if not 1 <= initial_active <= len(shards):
            raise ValueError("initial_active must be between 1 and the number of shards")
        self.shards = {s.id: s for s in shards}
        self.order = [s.id for s in shards]
        self.vnodes_default = vnodes
        self.telemetry = telemetry
        self.topology_path = topology_path
        self.key_match = key_match
        self._lock = threading.RLock()
        self.epoch = 0
        self.previous_ring: ConsistentHashRing | None = None
        self.last_report: MigrationReport | None = None
        self.history: list[dict] = []
        self._load_baseline: dict[str, int] = {}
        self.ring = ConsistentHashRing(self.order[:initial_active], vnodes)
        self._initial_active = initial_active
        self._load_topology()

    # ---------------------------------------------------------------- topology persistence
    def _load_topology(self) -> None:
        if not self.topology_path or not self.topology_path.exists():
            return
        try:
            data = json.loads(self.topology_path.read_text())
            weights = {n: int(v) for n, v in data["vnodes"].items() if n in self.shards}
            if weights:
                self.ring = ConsistentHashRing(weights, self.vnodes_default)
                self.epoch = int(data.get("epoch", 0))
                log.info("loaded shard topology epoch %s: %s", self.epoch, list(weights))
        except Exception:  # corrupt file -> fall back to defaults
            log.exception("ignoring unreadable topology file %s", self.topology_path)

    def _save_topology(self) -> None:
        if not self.topology_path:
            return
        self.topology_path.parent.mkdir(parents=True, exist_ok=True)
        self.topology_path.write_text(json.dumps(
            {"epoch": self.epoch, "vnodes": self.ring.vnodes, "saved_at": time.time()}, indent=2))

    # ---------------------------------------------------------------- routing
    @property
    def active(self) -> list[str]:
        return self.ring.nodes

    @property
    def spare(self) -> list[str]:
        return [s for s in self.order if s not in self.ring.vnodes]

    @property
    def migrating(self) -> bool:
        return self.previous_ring is not None

    def shard_for(self, key: str) -> Shard:
        return self.shards[self.ring.get_node(key)]

    def fallback_shard_for(self, key: str) -> Shard | None:
        """Previous owner while a migration is incomplete (read fallback)."""
        prev = self.previous_ring
        if prev is None:
            return None
        sid = prev.get_node(key)
        return self.shards[sid] if sid != self.ring.get_node(key) else None

    # ---------------------------------------------------------------- inspection
    def count_keys(self, shard_id: str) -> int:
        client = self.shards[shard_id].client
        if self.key_match == "*":
            return int(client.dbsize())
        return sum(1 for _ in client.scan_iter(match=self.key_match, count=1000))

    def distribution(self) -> dict[str, int]:
        out = {}
        for sid in self.order:
            try:
                out[sid] = self.count_keys(sid)
            except Exception:  # unreachable shard
                out[sid] = -1
        return out

    def health(self) -> list[dict]:
        rows = []
        for sid in self.order:
            sh = self.shards[sid]
            row = sh.describe() | {"active": sid in self.ring.vnodes,
                                   "vnodes": self.ring.vnodes.get(sid, 0)}
            t0 = time.perf_counter()
            try:
                sh.client.ping()
                row["ping_ms"] = round((time.perf_counter() - t0) * 1000, 3)
                row["up"] = True
                row["keys"] = self.count_keys(sid)
            except Exception as exc:  # noqa: BLE001
                row["up"], row["error"], row["keys"] = False, str(exc)[:200], None
            rows.append(row)
        return rows

    def topology(self) -> dict:
        own = self.ring.ownership()
        load = self._load_since_baseline()
        return {
            "epoch": self.epoch,
            "active": self.active,
            "spare": self.spare,
            "vnodes": self.ring.vnodes,
            "ownership": {k: round(v, 4) for k, v in own.items()},
            "migrating": self.migrating,
            "request_load_since_rebalance": load,
            "ring_tokens": self.ring.tokens_sample(360),
            "last_migration": self.last_report.to_dict() if self.last_report else None,
            "history": self.history[-20:],
        }

    def _load_since_baseline(self) -> dict[str, int]:
        if not self.telemetry:
            return {}
        cur = self.telemetry.shard_load()
        return {s: cur.get(s, 0) - self._load_baseline.get(s, 0) for s in self.active}

    # ---------------------------------------------------------------- topology changes
    def add_shard(self, shard_id: str | None = None) -> MigrationReport:
        with self._lock:
            if shard_id is None:
                if not self.spare:
                    raise ValueError("no spare shard available; all configured shards are active")
                shard_id = self.spare[0]
            if shard_id not in self.shards:
                raise KeyError(f"unknown shard {shard_id}")
            if shard_id in self.ring.vnodes:
                raise ValueError(f"{shard_id} is already active")
            self.shards[shard_id].client.ping()  # fail fast if the new shard is down
            new_ring = self.ring.copy()
            new_ring.add_node(shard_id, self.vnodes_default)
            # The new shard is also scanned so stray keys on it end up with their real owner.
            return self._switch_and_migrate(new_ring, f"add {shard_id}", extra_sources=[shard_id])

    def remove_shard(self, shard_id: str) -> MigrationReport:
        with self._lock:
            if shard_id not in self.ring.vnodes:
                raise ValueError(f"{shard_id} is not active")
            if len(self.ring.nodes) == 1:
                raise ValueError("cannot remove the last active shard")
            new_ring = self.ring.copy()
            new_ring.remove_node(shard_id)
            return self._switch_and_migrate(new_ring, f"remove {shard_id}", extra_sources=[shard_id])

    def reset_to_initial(self) -> MigrationReport | None:
        """Shrink back to the initial shard set (used to repeat the 4 -> 5 demo)."""
        with self._lock:
            target = self.order[: self._initial_active]
            report = None
            for sid in [s for s in self.active if s not in target]:
                report = self.remove_shard(sid)
            if self.ring.vnodes != {s: self.vnodes_default for s in target}:
                new_ring = ConsistentHashRing(target, self.vnodes_default)
                report = self._switch_and_migrate(new_ring, "reset vnode weights")
            return report

    def rebalance_by_load(self, load: dict[str, float] | None = None, alpha: float = 0.5,
                          floor: int = 16, min_requests: int = 200) -> dict:
        """Adapt vnode weights from observed request load, then migrate affected keys."""
        with self._lock:
            load = load if load is not None else self._load_since_baseline()
            total = sum(load.get(s, 0) for s in self.active)
            if total < min_requests:
                raise ValueError(f"only {total} requests observed since the last rebalance; "
                                 f"need at least {min_requests} to estimate load skew")
            before = [load.get(s, 0) for s in self.active]
            vnodes_before = self.ring.vnodes
            new_w = adaptive_vnode_update(vnodes_before, load, alpha, floor)
            new_ring = ConsistentHashRing(new_w, self.vnodes_default)
            report = self._switch_and_migrate(new_ring, "adaptive rebalance by load")
            return {
                "observed_load": load,
                "imbalance_before": round(imbalance(before), 4),
                "cv_before": round(coefficient_of_variation(before), 4),
                "vnodes_before": vnodes_before,
                "vnodes_after": new_w,
                "migration": report.to_dict(),
            }

    def _switch_and_migrate(self, new_ring: ConsistentHashRing, action: str,
                            extra_sources: Iterable[str] = ()) -> MigrationReport:
        old_ring = self.ring
        # Extra sources (a new or removed shard) are scanned first, so keys that migrate *into*
        # a shard are never scanned a second time.
        sources = list(dict.fromkeys(list(extra_sources) + list(old_ring.nodes)))
        report = MigrationReport(action, list(old_ring.nodes), list(new_ring.nodes))
        report.keys_before = {s: self.count_keys(s) for s in self.order}
        report.total_before = sum(report.keys_before.values())
        self._publish("shard_migration_started", action=action, sources=sources,
                      targets=new_ring.nodes)
        # From here on writes go to the new owners; reads fall back to the old owner.
        self.previous_ring = old_ring
        self.ring = new_ring
        self.epoch += 1
        self._migrate(sources, report)
        report.keys_after = {s: self.count_keys(s) for s in self.order}
        report.total_after = sum(report.keys_after.values())
        report.elapsed_s = round(time.time() - report.started_at, 3)
        if report.failed == 0:
            self.previous_ring = None
            report.status = "completed"
        else:
            report.status = "partial"  # previous ring retained for read fallback; retry possible
        report.log.append(f"{report.status}: moved {report.moved}/{report.to_move} keys "
                          f"in {report.elapsed_s}s; total {report.total_before} -> {report.total_after}")
        self.last_report = report
        self.history.append({"epoch": self.epoch, "action": action, "status": report.status,
                             "moved": report.moved, "failed": report.failed,
                             "elapsed_s": report.elapsed_s, "at": report.started_at})
        if self.telemetry:
            self._load_baseline = self.telemetry.shard_load()
        self._save_topology()
        self._publish("shard_migration_finished", **{k: v for k, v in report.to_dict().items()
                                                     if k not in ("log", "failed_keys", "sample_checks")})
        return report

    def retry_migration(self) -> MigrationReport:
        with self._lock:
            if self.previous_ring is None:
                raise ValueError("no incomplete migration to retry")
            report = MigrationReport("retry", self.previous_ring.nodes, self.ring.nodes)
            report.keys_before = {s: self.count_keys(s) for s in self.order}
            report.total_before = sum(report.keys_before.values())
            self._migrate(self.previous_ring.nodes, report)
            report.keys_after = {s: self.count_keys(s) for s in self.order}
            report.total_after = sum(report.keys_after.values())
            report.elapsed_s = round(time.time() - report.started_at, 3)
            report.status = "completed" if report.failed == 0 else "partial"
            if report.failed == 0:
                self.previous_ring = None
            self.last_report = report
            self._save_topology()
            return report

    # ---------------------------------------------------------------- migration core
    def _migrate(self, sources: list[str], report: MigrationReport) -> None:
        for src_id in sources:
            src = self.shards[src_id]
            batch: list[bytes] = []
            for raw_key in src.client.scan_iter(match=self.key_match, count=1000):
                batch.append(raw_key)
                if len(batch) >= self.BATCH:
                    self._migrate_batch(src, batch, report)
                    batch = []
            if batch:
                self._migrate_batch(src, batch, report)
            report.log.append(f"scanned {src_id}")

    def _migrate_batch(self, src: Shard, raw_keys: list[bytes], report: MigrationReport) -> None:
        keys = [k.decode("utf-8", "replace") for k in raw_keys]
        owners = self.ring.map_keys(keys)
        report.scanned += len(keys)
        moving: dict[str, list[tuple[bytes, str]]] = {}
        for raw, key, owner in zip(raw_keys, keys, owners):
            if owner != src.id:
                moving.setdefault(owner, []).append((raw, key))
        for dest_id, items in moving.items():
            report.to_move += len(items)
            route = f"{src.id}->{dest_id}"
            try:
                self._move_items(src, self.shards[dest_id], items, report)
                report.moves_by_route[route] = report.moves_by_route.get(route, 0) + len(items)
            except Exception as exc:  # noqa: BLE001 - whole batch failed (e.g. shard down)
                report.failed += len(items)
                report.failed_keys.extend(k for _, k in items[: max(0, 50 - len(report.failed_keys))])
                report.log.append(f"batch {route} failed: {exc}")
                log.warning("migration batch %s failed: %s", route, exc)

    def _move_items(self, src: Shard, dst: Shard, items: list[tuple[bytes, str]],
                    report: MigrationReport) -> None:
        raws = [r for r, _ in items]
        p = src.client.pipeline(transaction=False)
        for r in raws:
            p.dump(r)
            p.pttl(r)
        res = p.execute()
        dumps, ttls = res[0::2], res[1::2]
        dp = dst.client.pipeline(transaction=False)
        for r in raws:
            dp.dump(r)
        existing = dp.execute()
        restore = dst.client.pipeline(transaction=False)
        plan = []
        for raw, key, payload, ttl, cur in zip(raws, [k for _, k in items], dumps, ttls, existing):
            if payload is None:
                plan.append("gone")          # deleted on source meanwhile
            elif cur is not None and cur == payload:
                plan.append("present")       # earlier (partial) run already copied it
            elif cur is not None:
                plan.append("newer")         # written at destination after the switch
            else:
                plan.append("copy")
                restore.restore(raw, max(int(ttl), 0) if ttl and ttl > 0 else 0, payload)
        if any(x == "copy" for x in plan):
            restore.execute()
        # Verify every key at the destination before deleting the source copy.
        vp = dst.client.pipeline(transaction=False)
        for raw in raws:
            vp.dump(raw)
        after = vp.execute()
        delete_ok: list[bytes] = []
        for raw, key, payload, action, dst_dump in zip(raws, [k for _, k in items], dumps, plan, after):
            if action == "gone":
                continue
            ok = dst_dump is not None and (dst_dump == payload or action == "newer")
            if ok:
                delete_ok.append(raw)
                report.verified += 1
                if action == "copy":
                    report.moved += 1
                elif action == "present":
                    report.already_present += 1
                    report.moved += 1
                else:
                    report.kept_newer_destination += 1
                    report.moved += 1
                if len(report.sample_checks) < 10 and action == "copy":
                    report.sample_checks.append({"key": key, "from": src.id, "to": dst.id,
                                                 "bytes": len(payload), "match": True})
            else:
                report.failed += 1
                if len(report.failed_keys) < 50:
                    report.failed_keys.append(key)
        if delete_ok:
            src.client.delete(*delete_ok)

    # ---------------------------------------------------------------- verification helper
    def verify_sample(self, expected: dict[str, bytes], getter: Callable[[str], bytes | None]) -> dict:
        mismatches = [k for k, v in expected.items() if getter(k) != v]
        return {"checked": len(expected), "mismatches": len(mismatches), "examples": mismatches[:10]}

    def _publish(self, event_type: str, **data) -> None:
        if self.telemetry:
            self.telemetry.publish(event_type, "sharding", epoch=self.epoch, **data)
