"""Adapter health behaviour that does not need the real engines."""
from app.adapters.cassandra_adapter import CassandraAdapter
from app.adapters.mongo_adapter import MongoAdapter, check_field_names
from app.adapters.base import OperationRejected

import pytest


class FlakySession:
    def __init__(self, failures):
        self.failures = failures

    def execute(self, *a, **k):
        if self.failures:
            self.failures -= 1
            raise TimeoutError("Client request timeout")
        return None


def test_cassandra_recovers_after_transient_timeout():
    a = CassandraAdapter(["127.0.0.1"], 9042, "hanf")
    a._session, a._status, a._verified = FlakySession(failures=1), "connected", True
    assert a.health(deep=True).status == "unavailable"
    assert not a.available
    h = a.health(deep=True)
    assert h.status == "connected" and a.available and h.verified_round_trip


class FlakyAdmin:
    def __init__(self, failures):
        self.failures = failures

    def command(self, name):
        if self.failures:
            self.failures -= 1
            raise ConnectionError("ping failed")
        return {"ok": 1}


class FakeClient:
    def __init__(self, failures):
        self.admin = FlakyAdmin(failures)


def test_mongo_recovers_after_transient_failure():
    a = MongoAdapter("mongodb://x", "db")
    a._client, a._status, a._verified = FakeClient(1), "connected", True
    assert a.health(deep=True).status == "unavailable"
    assert a.health(deep=True).status == "connected"


def test_disabled_adapters_report_disabled_and_never_available():
    for a in (MongoAdapter("mongodb://x", "db", enabled=False), CassandraAdapter(["h"], 1, "k", enabled=False)):
        a.connect()
        assert a.health(deep=True).status == "disabled" and not a.available


def test_operator_field_names_rejected():
    with pytest.raises(OperationRejected):
        check_field_names({"a": {"$where": "1"}})
    with pytest.raises(OperationRejected):
        check_field_names({"a.b": 1})
    check_field_names({"a": [{"b": 1}], "c": "text with $ inside"})
