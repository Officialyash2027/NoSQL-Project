import pytest

from app.adapters.base import AdapterHealth, AdapterUnavailable, ExecResult, StorageAdapter
from app.router.classifier import WorkloadClassifier, load_rules
from app.router.router import PolyglotRouter
from app.config import PROJECT_ROOT


class FakeAdapter(StorageAdapter):
    def __init__(self, name, model, caps, up=True):
        self.name, self.engine, self.data_model, self.capabilities, self.up = name, name, model, caps, up

    @property
    def available(self):
        return self.up

    def health(self, deep=False):
        return AdapterHealth(self.name, self.engine, self.data_model, "connected" if self.up else "unavailable")

    def execute(self, op):
        return ExecResult("ok")


KV = ("read", "write", "update", "delete", "scan", "login")
DOC = ("read", "write", "update", "delete", "query")
WIDE = ("read", "write", "update", "delete")


def make_router(mongo_up=True, cass_up=True, fallback=True):
    rules = load_rules(PROJECT_ROOT / "configs" / "routing.yaml")
    adapters = {"redis": FakeAdapter("redis", "key-value", KV),
                "mongodb": FakeAdapter("mongodb", "document", DOC, mongo_up),
                "cassandra": FakeAdapter("cassandra", "wide-column", WIDE, cass_up)}
    return PolyglotRouter(WorkloadClassifier(rules), adapters, rules["classes"], fallback)


@pytest.mark.parametrize("op,key,value,flt,expected", [
    ("read", "product:42", None, None, "key-value"),
    ("write", "session:9", "token", None, "key-value"),
    ("login", "user7", "pw", None, "key-value"),
    ("scan", "product:*", None, None, "key-value"),
    ("write", "order:1", {"items": [{"sku": "a"}], "total": 3}, None, "document"),
    ("query", None, None, {"status": "paid"}, "document"),
    ("write", "sensor:3", {"ts": "2026-01-01T00:00", "t": 21.0}, None, "wide-column"),
    ("read", "sensor:3", None, None, "wide-column"),
    ("read", "unknownkey", None, None, "key-value"),
])
def test_classification(op, key, value, flt, expected):
    c = make_router().classifier.classify(op, key, value, flt)
    assert c.workload == expected
    assert c.reasons and 0 < c.confidence <= 1


def test_classification_is_deterministic():
    r = make_router()
    a = r.classifier.classify("write", "order:5", {"a": {"b": 1}})
    b = r.classifier.classify("write", "order:5", {"a": {"b": 1}})
    assert a.to_dict() == b.to_dict()


def test_hint_dominates():
    c = make_router().classifier.classify("read", "product:1", hint="wide-column")
    assert c.workload == "wide-column"
    with pytest.raises(ValueError):
        make_router().classifier.classify("read", "x", hint="graph")


def test_adapter_selection_and_reason():
    d = make_router().route("write", "order:1", {"a": [1, 2]})
    assert d.engine == "mongodb" and not d.fallback_used and "document" in d.reason


def test_fallback_to_kv_when_document_engine_down():
    d = make_router(mongo_up=False).route("write", "order:1", {"a": [1]})
    assert d.engine == "redis" and d.preferred_engine == "mongodb" and d.fallback_used
    assert "fell back" in d.reason


def test_no_fallback_raises():
    with pytest.raises(AdapterUnavailable):
        make_router(mongo_up=False, fallback=False).route("write", "order:1", {"a": [1]})
    with pytest.raises(AdapterUnavailable):  # 'query' cannot fall back to the KV engine
        make_router(mongo_up=False).route("query", None, None, {"status": "x"})


def test_unsupported_operation_on_forced_engine():
    with pytest.raises(ValueError):
        make_router().route("scan", "x*", engine_override="cassandra")
    with pytest.raises(ValueError):
        make_router().route("read", "x", engine_override="neo4j")
