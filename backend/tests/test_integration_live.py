"""Integration tests against the real Redis containers (and MongoDB/Cassandra when running).

Redis tests use logical DBs 14/15 so they never touch the demo data in DB 0; everything they
write is flushed afterwards. Each test skips automatically when its service is unreachable.
"""
import uuid

import pytest

from app.adapters.base import Operation
from app.adapters.cassandra_adapter import CassandraAdapter
from app.adapters.mongo_adapter import MongoAdapter
from app.core.service import HANFService
from app.experiments import e1_throughput
from app.experiments.common import ExperimentContext, load_profile

from .conftest import port_open

pytestmark = pytest.mark.live


@pytest.fixture
def live_service(live_settings):
    svc = HANFService(live_settings, enable_optional=False, security_mode="sync", topology_file=False)
    for s in svc.shards.shards.values():
        s.client.flushdb()
    yield svc
    for s in svc.shards.shards.values():
        s.client.flushdb()


def test_live_pipeline_seed_read_write(live_service):
    assert live_service.redis.health(deep=True).verified_round_trip
    rep = live_service.seed(reset=True, include_optional=False)
    assert rep["redis"]["records"] == 6000
    r = live_service.execute("read", "product:5")
    assert r["status"] == "ok" and r["shard"]["port"] in (7001, 7002, 7003, 7004)
    assert live_service.execute("write", "kv:live", "v")["status"] == "ok"
    assert live_service.execute("read", "kv:live")["data"] == "v"


def test_live_migration_four_to_five(live_service):
    live_service.seed(reset=True, include_optional=False)
    before = sum(live_service.shards.distribution().values())
    rep = live_service.shards.add_shard("shard-5")
    assert rep.status == "completed" and rep.failed == 0
    assert rep.total_before == rep.total_after == before
    for i in range(0, 5000, 50):
        k = f"product:{i}"
        assert live_service.redis.raw_get(k) == live_service.dataset.value_for(k).encode()


def test_live_e1_tiny(live_settings):
    cfg = load_profile(live_settings, "quick", "e1", {
        "records": 500, "operations": 300, "warmup": 20, "repetitions": 1, "workloads": ["B"],
        "configs": ["direct", "hanf", "hanf_batched_ml"]})
    doc = e1_throughput.run(ExperimentContext(live_settings), cfg)
    assert doc["status"] == "completed", doc["failures"]
    assert {r["config"] for r in doc["results"]["summary"]} == {"direct", "hanf", "hanf_batched_ml"}
    assert all(r["errors"] == 0 for r in doc["results"]["summary"])


@pytest.mark.optional_engine
def test_mongodb_adapter_round_trip():
    if not port_open(27017):
        pytest.skip("MongoDB not running (docker compose --profile full up -d)")
    a = MongoAdapter("mongodb://127.0.0.1:27017", "hanf_test")
    a.connect()
    assert a.health().status == "connected" and a.health().verified_round_trip
    key = f"test:{uuid.uuid4().hex}"
    assert a.execute(Operation("write", key, {"status": "x", "n": 1})).status == "ok"
    assert a.execute(Operation("read", key)).data == {"status": "x", "n": 1}
    assert a.execute(Operation("update", key, {"n": 2})).status == "ok"
    assert a.execute(Operation("query", filter={"status": "x"})).data["count"] >= 1
    with pytest.raises(Exception):
        a.execute(Operation("query", filter={"status": {"$ne": None}}))
    assert a.execute(Operation("delete", key)).status == "ok"
    a._client.drop_database("hanf_test")
    a.close()


@pytest.mark.optional_engine
def test_cassandra_adapter_round_trip_with_consistency_levels():
    if not port_open(9042):
        pytest.skip("Cassandra not running (docker compose --profile full up -d)")
    a = CassandraAdapter(["127.0.0.1"], 9042, "hanf")
    a.connect()
    if a.health().status != "connected":
        pytest.skip(f"Cassandra not ready: {a.health().detail}")
    key = f"test:{uuid.uuid4().hex[:12]}"
    for level in ("ONE", "QUORUM", "ALL"):
        r = a.execute(Operation("write", key, {"ts": level, "v": 1}, consistency=level))
        assert r.status == "ok" and r.meta["consistency_level_applied"] == level
    rows = a.execute(Operation("read", key, consistency="QUORUM")).data
    assert rows["count"] == 3
    assert a.execute(Operation("delete", key)).status == "ok"
    assert a.execute(Operation("read", key)).status == "not_found"
    a.close()
