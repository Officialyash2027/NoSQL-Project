"""Wide-column adapter: Apache Cassandra via the DataStax python driver.

Table ``<keyspace>.wide_rows(pk text, ck text, value text, PRIMARY KEY (pk, ck))``:
* the request key is the partition key (e.g. ``sensor:12``);
* the clustering key comes from ``value["ts"]`` (or ``value["ck"]``), so a partition holds an
  ordered series of rows - the typical wide-column / time-series access pattern;
* the remaining columns are stored as JSON text.

The consistency level chosen by the Adaptive Consistency Controller (ONE / QUORUM / ALL) is applied
to every statement. On the single-node demo cluster (RF = 1) every level is satisfiable, so this
demonstrates the control path, not geo-replicated behaviour.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from .base import (AdapterHealth, AdapterUnavailable, ExecResult, Operation, OperationRejected,
                   StorageAdapter)


class CassandraAdapter(StorageAdapter):
    name = "cassandra"
    engine = "Cassandra"
    data_model = "wide-column"
    capabilities = ("read", "write", "update", "delete", "consistency-levels")

    def __init__(self, hosts: list[str], port: int, keyspace: str, enabled: bool = True,
                 timeout_s: float = 2.0):
        self.hosts, self.port, self.keyspace, self.enabled = hosts, port, keyspace, enabled
        self.timeout_s = timeout_s
        self._cluster = self._session = None
        self._stmts: dict[str, Any] = {}
        self._status = "disabled" if not enabled else "connecting"
        self._detail = "disabled by configuration (HANF_CASSANDRA_ENABLED=false)" if not enabled else ""
        self._version = None
        self._verified = False
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self._status == "connected"

    def connect(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._cluster is not None:  # reconnect: release the previous cluster first
                try:
                    self._cluster.shutdown()
                except Exception:  # noqa: BLE001
                    pass
                self._cluster = self._session = None
            try:
                from cassandra.cluster import Cluster

                cluster = Cluster(self.hosts, port=self.port, connect_timeout=self.timeout_s,
                                  control_connection_timeout=self.timeout_s, protocol_version=5)
                session = cluster.connect()
                session.default_timeout = 5
                session.execute(
                    f"CREATE KEYSPACE IF NOT EXISTS {self.keyspace} WITH replication = "
                    "{'class': 'SimpleStrategy', 'replication_factor': 1}", timeout=60)
                session.set_keyspace(self.keyspace)
                session.execute("CREATE TABLE IF NOT EXISTS wide_rows "
                                "(pk text, ck text, value text, PRIMARY KEY (pk, ck))", timeout=60)
                self._stmts = {
                    "insert": session.prepare("INSERT INTO wide_rows (pk, ck, value) VALUES (?, ?, ?)"),
                    "read_row": session.prepare("SELECT ck, value FROM wide_rows WHERE pk=? AND ck=?"),
                    "read_part": session.prepare("SELECT ck, value FROM wide_rows WHERE pk=? LIMIT ?"),
                    "del_row": session.prepare("DELETE FROM wide_rows WHERE pk=? AND ck=?"),
                    "del_part": session.prepare("DELETE FROM wide_rows WHERE pk=?"),
                }
                self._version = session.execute("SELECT release_version FROM system.local").one()[0]
                session.execute(self._stmts["insert"], ("__hanf_hc__", "0", "{}"))
                ok = session.execute(self._stmts["read_row"], ("__hanf_hc__", "0")).one() is not None
                session.execute(self._stmts["del_part"], ("__hanf_hc__",))
                self._cluster, self._session, self._verified = cluster, session, ok
                self._status = "connected"
                self._detail = f"{','.join(self.hosts)}:{self.port} keyspace={self.keyspace} (RF=1)"
            except Exception as exc:  # noqa: BLE001
                self._status, self._detail = "unavailable", f"{type(exc).__name__}: {str(exc)[:160]}"

    def health(self, deep: bool = False) -> AdapterHealth:
        lat = None
        # A deep check also re-tests an engine that failed earlier, so a transient timeout
        # (e.g. while the JVM warms up) does not leave the adapter unavailable forever.
        if self._session is not None and deep:
            t0 = time.perf_counter()
            try:
                self._session.execute("SELECT now() FROM system.local", timeout=10)
                lat = round((time.perf_counter() - t0) * 1000, 3)
                self._status = "connected"
                self._detail = f"{','.join(self.hosts)}:{self.port} keyspace={self.keyspace} (RF=1)"
            except Exception as exc:  # noqa: BLE001
                self._status, self._detail = "unavailable", f"query failed: {str(exc)[:160]}"
        return AdapterHealth(self.name, self.engine, self.data_model, self._status, self._detail,
                             self._version, lat, capabilities=list(self.capabilities),
                             verified_round_trip=self._verified and self._status == "connected")

    def _bind(self, name: str, params: tuple, level: str | None):
        from cassandra import ConsistencyLevel

        stmt = self._stmts[name].bind(params)
        stmt.consistency_level = getattr(ConsistencyLevel, (level or "ONE").upper(),
                                         ConsistencyLevel.ONE)
        return stmt

    def execute(self, op: Operation) -> ExecResult:
        if not self.available:
            raise AdapterUnavailable(f"Cassandra is {self._status}: {self._detail}")
        if not op.key:
            raise OperationRejected("a partition key is required")
        s = self._session
        level = (op.consistency or "ONE").upper()
        meta = {"table": f"{self.keyspace}.wide_rows", "consistency_level_applied": level}
        ck = None
        if isinstance(op.value, dict):
            ck = op.value.get("ts") or op.value.get("ck")
        if op.filter and isinstance(op.filter, dict):
            ck = op.filter.get("ts") or op.filter.get("ck") or ck
        if op.operation == "read":
            if ck is not None:
                row = s.execute(self._bind("read_row", (op.key, str(ck)), level)).one()
                if row is None:
                    return ExecResult("not_found", None, meta, "row not found")
                return ExecResult("ok", {"ck": row.ck, **json.loads(row.value)}, meta)
            rows = list(s.execute(self._bind("read_part", (op.key, max(1, min(op.limit, 500))), level)))
            if not rows:
                return ExecResult("not_found", None, meta, "partition not found")
            return ExecResult("ok", {"rows": [{"ck": r.ck, **json.loads(r.value)} for r in rows],
                                     "count": len(rows)}, meta)
        if op.operation in ("write", "update"):
            if not isinstance(op.value, dict):
                raise OperationRejected("wide-column engine expects a JSON object as value")
            cols = {k: v for k, v in op.value.items() if k not in ("ts", "ck")}
            ck = str(ck if ck is not None else "default")
            if op.operation == "update":
                cur = s.execute(self._bind("read_row", (op.key, ck), level)).one()
                if cur is None:
                    return ExecResult("not_found", None, meta, "row not found")
                cols = {**json.loads(cur.value), **cols}
            s.execute(self._bind("insert", (op.key, ck, json.dumps(cols)), level))
            return ExecResult("ok", {"partition": op.key, "ck": ck, "columns": list(cols)}, meta)
        if op.operation == "delete":
            if ck is not None:
                s.execute(self._bind("del_row", (op.key, str(ck)), level))
            else:
                s.execute(self._bind("del_part", (op.key,), level))
            return ExecResult("ok", {"deleted": True}, meta)
        raise OperationRejected(f"operation {op.operation!r} is not supported by the wide-column engine")

    def close(self) -> None:
        if self._cluster is not None:
            self._cluster.shutdown()
