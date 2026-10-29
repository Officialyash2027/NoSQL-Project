"""Document adapter: MongoDB via pymongo.

Documents are stored in ``<db>.documents`` with ``_id`` = request key. Field names starting with
``$`` or containing ``.`` are rejected, and query filters accept equality on scalar values only, so
an operator such as ``{"$ne": null}`` can never reach the server as a query operator.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from .base import (AdapterHealth, AdapterUnavailable, ExecResult, Operation, OperationRejected,
                   StorageAdapter)

SCALARS = (str, int, float, bool, type(None))


def check_field_names(doc: Any, path: str = "") -> None:
    if isinstance(doc, dict):
        for k, v in doc.items():
            if not isinstance(k, str) or k.startswith("$") or "." in k:
                raise OperationRejected(f"field name {path + str(k)!r} is not allowed "
                                        "(operator-like or dotted field names are rejected)")
            check_field_names(v, f"{path}{k}.")
    elif isinstance(doc, list):
        for item in doc:
            check_field_names(item, path)


class MongoAdapter(StorageAdapter):
    name = "mongodb"
    engine = "MongoDB"
    data_model = "document"
    capabilities = ("read", "write", "update", "delete", "query")

    def __init__(self, uri: str, db: str, enabled: bool = True, timeout_s: float = 2.0):
        self.uri, self.db_name, self.enabled, self.timeout_s = uri, db, enabled, timeout_s
        self._client = None
        self._coll = None
        self._status = "disabled" if not enabled else "connecting"
        self._detail = "disabled by configuration (HANF_MONGO_ENABLED=false)" if not enabled else ""
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
            try:
                from pymongo import MongoClient

                ms = int(self.timeout_s * 1000)
                client = MongoClient(self.uri, serverSelectionTimeoutMS=ms, connectTimeoutMS=ms,
                                     socketTimeoutMS=5000)
                info = client.server_info()
                coll = client[self.db_name]["documents"]
                # Real round trip before the engine is reported as connected.
                coll.replace_one({"_id": "__hanf_healthcheck__"}, {"ok": 1}, upsert=True)
                ok = coll.find_one({"_id": "__hanf_healthcheck__"}) is not None
                coll.delete_one({"_id": "__hanf_healthcheck__"})
                self._client, self._coll = client, coll
                self._version, self._verified = info.get("version"), ok
                self._status, self._detail = "connected", f"{self.uri} db={self.db_name}"
            except Exception as exc:  # noqa: BLE001
                self._client = self._coll = None
                self._status, self._detail = "unavailable", f"{type(exc).__name__}: {str(exc)[:160]}"

    def health(self, deep: bool = False) -> AdapterHealth:
        lat = None
        if self._client is not None and deep:  # also re-tests an engine that failed earlier
            t0 = time.perf_counter()
            try:
                self._client.admin.command("ping")
                lat = round((time.perf_counter() - t0) * 1000, 3)
                self._status, self._detail = "connected", f"{self.uri} db={self.db_name}"
            except Exception as exc:  # noqa: BLE001
                self._status, self._detail = "unavailable", f"ping failed: {str(exc)[:160]}"
        return AdapterHealth(self.name, self.engine, self.data_model, self._status, self._detail,
                             self._version, lat, capabilities=list(self.capabilities),
                             verified_round_trip=self._verified and self._status == "connected")

    def execute(self, op: Operation) -> ExecResult:
        if not self.available:
            raise AdapterUnavailable(f"MongoDB is {self._status}: {self._detail}")
        c = self._coll
        meta: dict[str, Any] = {"collection": f"{self.db_name}.documents"}
        if op.operation == "query":
            flt = op.filter or {}
            check_field_names(flt)
            for k, v in flt.items():
                if not isinstance(v, SCALARS):
                    raise OperationRejected(f"filter value for {k!r} must be a scalar (equality only)")
            docs = list(c.find(flt).limit(max(1, min(op.limit, 200))))
            for d in docs:
                d["key"] = d.pop("_id")
            return ExecResult("ok", {"documents": docs, "count": len(docs)}, meta | {"filter": flt})
        if not op.key:
            raise OperationRejected("a key is required for this operation")
        if op.operation == "read":
            doc = c.find_one({"_id": op.key})
            if doc is None:
                return ExecResult("not_found", None, meta, "document not found")
            doc.pop("_id", None)
            return ExecResult("ok", doc, meta)
        if op.operation in ("write", "update"):
            if not isinstance(op.value, dict):
                raise OperationRejected("document engine expects a JSON object as value")
            check_field_names(op.value)
            if op.operation == "write":
                r = c.replace_one({"_id": op.key}, op.value, upsert=True)
                return ExecResult("ok", {"upserted": r.upserted_id is not None,
                                         "modified": r.modified_count}, meta)
            r = c.update_one({"_id": op.key}, {"$set": op.value})
            if r.matched_count == 0:
                return ExecResult("not_found", None, meta, "document not found")
            return ExecResult("ok", {"modified": r.modified_count}, meta)
        if op.operation == "delete":
            r = c.delete_one({"_id": op.key})
            if r.deleted_count == 0:
                return ExecResult("not_found", None, meta, "document not found")
            return ExecResult("ok", {"deleted": r.deleted_count}, meta)
        raise OperationRejected(f"operation {op.operation!r} is not supported by the document engine")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
