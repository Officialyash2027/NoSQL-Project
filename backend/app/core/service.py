"""HANF core service: wires the router, sharding manager, consistency controller, security monitor
and telemetry bus into one request pipeline.

Request lifecycle (``execute``)
1. Polyglot Router classifies the request and picks an engine (with fallback rules).
2. For the key-value engine, the Adaptive Sharding Manager maps the key to a Redis shard.
3. The Adaptive Consistency Controller supplies the read/write level for the operation
   (applied by Cassandra; recorded as advisory for Redis and MongoDB in this deployment).
4. The adapter executes the operation; latency is measured around the whole pipeline.
5. A 'request' telemetry event is published; the Security Monitor (a bus subscriber) adds it to
   the client's window and scores full windows according to its mode.
"""
from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from typing import Any

from ..adapters.base import AdapterUnavailable, ExecResult, Operation, OperationRejected
from ..adapters.cassandra_adapter import CassandraAdapter
from ..adapters.mongo_adapter import MongoAdapter
from ..adapters.redis_adapter import RedisShardedAdapter
from ..adapters.redis_clients import make_shards
from ..config import Settings
from ..consistency.simulator import AdaptiveConsistencyController
from ..router.classifier import WorkloadClassifier, load_rules
from ..router.router import PolyglotRouter
from ..security.features import has_injection
from ..security.monitor import SecurityMonitor
from ..security.traffic import DatasetSpec, TrafficGenerator, dataset_records, login_users
from ..sharding.manager import Shard, ShardManager
from ..telemetry.bus import TelemetryBus

log = logging.getLogger("hanf.service")

ENGINE_CONSISTENCY_NOTE = {
    "cassandra": "applied to the CQL statement",
    "redis": "advisory - Redis shards here are independent primaries without replicas",
    "mongodb": "advisory - single-node MongoDB (no replica set)",
}


def _size(data: Any) -> int:
    if data is None:
        return 0
    if isinstance(data, (bytes, str)):
        return len(data)
    try:
        return len(json.dumps(data, default=str))
    except (TypeError, ValueError):
        return 0


class HANFService:
    def __init__(self, settings: Settings, shards: list[Shard] | None = None,
                 telemetry: TelemetryBus | None = None, enable_optional: bool = True,
                 security_mode: str | None = None, topology_file: bool = True):
        self.settings = settings
        self.started_at = time.time()
        self.telemetry = telemetry or TelemetryBus(settings.telemetry_max_events)
        shards = shards or make_shards(settings.redis_host, settings.redis_ports, settings.redis_db,
                                       settings.redis_mode, settings.connect_timeout_s)
        topo = settings.runtime_dir / f"topology_{settings.redis_mode}.json" if topology_file else None
        self.shards = ShardManager(shards, min(settings.initial_shards, len(shards)), settings.vnodes,
                                   self.telemetry, topo)
        self.redis = RedisShardedAdapter(self.shards, settings.redis_mode)
        self.mongo = MongoAdapter(settings.mongo_uri, settings.mongo_db,
                                  enabled=enable_optional and settings.mongo_enabled,
                                  timeout_s=settings.connect_timeout_s)
        self.cassandra = CassandraAdapter(settings.cassandra_hosts, settings.cassandra_port,
                                          settings.cassandra_keyspace,
                                          enabled=enable_optional and settings.cassandra_enabled,
                                          timeout_s=settings.connect_timeout_s)
        self.adapters = {"redis": self.redis, "mongodb": self.mongo, "cassandra": self.cassandra}
        rules = load_rules(settings.configs_dir / "routing.yaml")
        self.router = PolyglotRouter(WorkloadClassifier(rules), self.adapters, rules["classes"],
                                     settings.router_fallback_to_kv)
        self.consistency = AdaptiveConsistencyController(slo=settings.staleness_slo, samples=2000,
                                                         telemetry=self.telemetry)
        self.security = SecurityMonitor(self.telemetry, settings.security_window, settings.security_batch,
                                        security_mode or settings.security_mode,
                                        settings.security_contamination, settings.security_seed,
                                        settings.models_dir / "security_iforest.joblib")
        self._ids = itertools.count(1)
        self._traffic_seq = itertools.count(1000)
        self.dataset = DatasetSpec()

    # ------------------------------------------------------------------ lifecycle
    def start(self, train_security: bool = True) -> None:
        for adapter in (self.mongo, self.cassandra):
            threading.Thread(target=self._connect_adapter, args=(adapter,), daemon=True,
                             name=f"connect-{adapter.name}").start()
        if train_security:
            # Loading scikit-learn/SciPy can take several seconds on a cold disk, so the model is
            # loaded (or trained) in the background; windows queue up until it is ready.
            threading.Thread(target=self._load_security_model, daemon=True,
                             name="security-model").start()
        self.security.start()
        self.consistency.update(1.0, source="startup default")

    def _load_security_model(self) -> None:
        try:
            info = self.security.ensure_model()
            self.telemetry.publish("security_model_ready", "security", **info)
        except Exception as exc:  # noqa: BLE001
            log.exception("security model could not be loaded or trained")
            self.telemetry.publish("security_model_error", "security", error=str(exc))

    def _connect_adapter(self, adapter) -> None:
        adapter.connect()
        h = adapter.health()
        self.telemetry.publish("adapter_health", "router", adapter=adapter.name, status=h.status,
                               detail=h.detail, verified_round_trip=h.verified_round_trip)

    def reconnect(self, name: str) -> dict:
        adapter = self.adapters[name]
        if name == "redis":
            return adapter.health(deep=True).to_dict()
        self._connect_adapter(adapter)
        return adapter.health(deep=True).to_dict()

    def stop(self) -> None:
        self.security.stop()
        for a in self.adapters.values():
            a.close()

    # ------------------------------------------------------------------ request pipeline
    def execute(self, operation: str, key: str | None = None, value: Any = None,
                filter: dict | None = None, limit: int = 50, hint: str | None = None,
                engine: str | None = None, client_id: str | None = None,
                client_ts: float | None = None, synthetic_label: str | None = None,
                explain: bool = True, consistency: str | None = None) -> dict[str, Any]:
        rid = next(self._ids)
        t0 = time.perf_counter()
        decision = self.router.route(operation, key, value, filter, hint, engine)
        level = (consistency or self.consistency.level_for(operation)).upper()
        adapter = self.adapters[decision.engine]
        stored_value = value
        if decision.fallback_used and not isinstance(value, (str, int, float, bool, type(None))):
            stored_value = json.dumps(value)
        op = Operation(operation, key, stored_value, filter, limit, level)
        error = None
        try:
            res = adapter.execute(op)
        except OperationRejected as exc:
            res = ExecResult("rejected", None, {}, str(exc))
        except AdapterUnavailable as exc:
            res = ExecResult("error", None, {}, str(exc))
        except Exception as exc:  # noqa: BLE001 - engine failure is reported, not raised
            log.warning("request %s failed on %s: %s", rid, decision.engine, exc)
            res = ExecResult("error", None, {}, f"{type(exc).__name__}: {exc}")
        latency_ms = (time.perf_counter() - t0) * 1000
        error = res.error
        payload_bytes = _size(value) + int(res.meta.get("response_bytes",
                                                         _size(res.data) if res.status == "ok" else 0))
        shard = res.meta.get("shard")
        injection = has_injection(key, value, filter)
        self.telemetry.publish(
            "request", "pipeline", request_id=rid, client_id=client_id, client_ts=client_ts,
            synthetic_label=synthetic_label, operation=operation, key=key, engine=decision.engine,
            workload=decision.classification.workload, fallback=decision.fallback_used, shard=shard,
            status=res.status, latency_ms=round(latency_ms, 4), payload_bytes=payload_bytes,
            injection_signal=injection, consistency_level=level)
        out: dict[str, Any] = {
            "request_id": rid, "status": res.status, "data": res.data, "error": error,
            "engine": decision.engine, "latency_ms": round(latency_ms, 4),
        }
        if explain:
            out["routing"] = decision.to_dict()
            out["consistency"] = {"level": level, "profile": self.consistency.state()["profile"],
                                  "applied": ENGINE_CONSISTENCY_NOTE.get(decision.engine, "")}
            out["meta"] = res.meta
            if decision.engine == "redis" and key and operation != "scan":
                loc = self.shards.ring.locate(key if operation != "login" else "cred:" + key)
                out["shard"] = {"id": loc["node"], "ring_position": round(loc["ring_position"], 6),
                                "token_position": round(loc["token_position"], 6),
                                "port": self.shards.shards[loc["node"]].port,
                                "migration_in_progress": self.shards.migrating}
            out["security"] = {"client_id": client_id, "injection_signal": injection,
                               "monitor_mode": self.security.mode,
                               "note": "requests without client_id are not windowed" if not client_id else
                               f"added to window of {client_id}"}
        return out

    # ------------------------------------------------------------------ seed data
    def seed(self, reset: bool = False, include_optional: bool = True) -> dict[str, Any]:
        t0 = time.perf_counter()
        report: dict[str, Any] = {"reset": reset}
        if reset:
            for sid in self.shards.order:
                try:
                    self.shards.shards[sid].client.flushdb()
                except Exception as exc:  # noqa: BLE001
                    report.setdefault("reset_errors", {})[sid] = str(exc)
        per_shard: dict[str, int] = {}
        pipes: dict[str, Any] = {}
        n = 0
        for k, v in dataset_records(self.dataset):
            sh = self.shards.shard_for(k)
            p = pipes.setdefault(sh.id, sh.client.pipeline(transaction=False))
            p.set(k.encode(), v.encode())
            per_shard[sh.id] = per_shard.get(sh.id, 0) + 1
            n += 1
            if len(p) >= 1000:
                p.execute()
        for p in pipes.values():
            p.execute()
        for i, (user, pw) in enumerate(login_users(self.dataset)):
            self.redis.create_user(user, pw, salt=f"salt-{i}")
        report["redis"] = {"records": n, "login_users": self.dataset.n_login_users,
                           "per_shard_written": per_shard}
        if include_optional and self.mongo.available:
            from ..adapters.base import Operation as Op
            for i in range(50):
                self.mongo.execute(Op("write", f"order:{i}", {
                    "customer": f"customer:{i % 10}", "status": ["new", "paid", "shipped"][i % 3],
                    "items": [{"sku": f"product:{(i * 7) % 5000}", "qty": 1 + i % 3}],
                    "total": round(10 + i * 3.5, 2)}))
            report["mongodb"] = {"documents": 50}
        else:
            report["mongodb"] = {"skipped": self.mongo.health().status}
        if include_optional and self.cassandra.available:
            from ..adapters.base import Operation as Op
            for s in range(5):
                for t in range(20):
                    self.cassandra.execute(Op("write", f"sensor:{s}", {
                        "ts": f"2026-10-18T10:{t:02d}:00", "temp_c": round(20 + s + t * 0.1, 2),
                        "humidity": 40 + t % 7}, consistency="ONE"))
            report["cassandra"] = {"rows": 100, "partitions": 5}
        else:
            report["cassandra"] = {"skipped": self.cassandra.health().status}
        report["elapsed_s"] = round(time.perf_counter() - t0, 3)
        self.telemetry.publish("seed", "service", **{k: v for k, v in report.items() if k != "reset_errors"})
        return report

    def is_seeded(self) -> bool:
        try:
            if self.redis.raw_get("product:0") is not None:
                return True
            fb = self.shards.fallback_shard_for("product:0")
            return fb is not None and fb.client.get(b"product:0") is not None
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ synthetic traffic (demo)
    def generate_traffic(self, normal_windows: int = 6, attack_windows: int = 2,
                         attack_kinds: tuple[str, ...] = ("injection", "exfiltration", "bruteforce"),
                         subtle_fraction: float = 0.3, seed: int | None = None) -> dict[str, Any]:
        """Execute synthetic request windows through the real pipeline (controlled, local only).

        Every request really runs against the engines; the monitor sees real outcomes and byte
        counts. Each request carries its generator label so detection quality can be measured.
        """
        if seed is None:
            seed = next(self._traffic_seq)
        gen = TrafficGenerator(self.dataset, seed=seed, window=self.security.window)
        plan: list[tuple[str, bool]] = [("normal", False)] * normal_windows
        for kind in attack_kinds:
            n_sub = int(round(subtle_fraction * attack_windows))
            plan += [(kind, j < n_sub) for j in range(attack_windows)]
        statuses: dict[str, dict[str, int]] = {}
        windows = []
        t0 = time.perf_counter()
        for i, (kind, subtle) in enumerate(plan):
            cid = f"demo-{kind}-{seed}-{i}"
            reqs = gen.make_window(kind, subtle, client_id=cid)
            st: dict[str, int] = {}
            for r in reqs:
                res = self.execute(r.operation, r.key, r.value, limit=r.limit, client_id=r.client_id,
                                   client_ts=r.ts, synthetic_label=r.label, explain=False)
                st[res["status"]] = st.get(res["status"], 0) + 1
            for k, v in st.items():
                statuses.setdefault(kind, {})[k] = statuses.setdefault(kind, {}).get(k, 0) + v
            windows.append({"client_id": cid, "synthetic_label": kind, "subtle": subtle,
                            "requests": len(reqs), "statuses": st})
        out = {"seed": seed, "windows": windows, "requests": sum(w["requests"] for w in windows),
               "statuses_by_kind": statuses, "elapsed_s": round(time.perf_counter() - t0, 3),
               "monitor_mode": self.security.mode, "queue_length": len(self.security._queue),
               "seeded": self.is_seeded(),
               "note": "synthetic_label is the generator's ground truth, not a detection."}
        self.telemetry.publish("synthetic_traffic", "security", seed=seed, windows=len(windows),
                               requests=out["requests"])
        return out

    # ------------------------------------------------------------------ status
    def adapters_health(self, deep: bool = False) -> list[dict]:
        return [a.health(deep).to_dict() for a in self.adapters.values()]

    def status(self) -> dict[str, Any]:
        return {
            "service": "HANF", "uptime_s": round(time.time() - self.started_at, 1),
            "redis_mode": self.settings.redis_mode, "seeded": self.is_seeded(),
            "adapters": self.adapters_health(),
            "shards": self.shards.health(),
            "topology": {"epoch": self.shards.epoch, "active": self.shards.active,
                         "spare": self.shards.spare, "migrating": self.shards.migrating},
            "security": {k: v for k, v in self.security.live_metrics().items() if k != "synthetic_evaluation"},
            "consistency": {k: v for k, v in self.consistency.state().items() if k != "predictions"},
            "telemetry": self.telemetry.snapshot(),
        }
