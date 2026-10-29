import threading

from app.telemetry.bus import TelemetryBus


def test_publish_subscribe_and_wildcard():
    bus = TelemetryBus()
    seen, everything = [], []
    bus.subscribe("request", seen.append)
    bus.subscribe("*", everything.append)
    bus.publish("request", "t", status="ok", latency_ms=1.0)
    bus.publish("other", "t")
    assert len(seen) == 1 and len(everything) == 2
    assert seen[0].data["status"] == "ok" and seen[0].id == 1


def test_snapshot_metrics_derived_from_events():
    bus = TelemetryBus()
    for i in range(10):
        bus.publish("request", "t", status="error" if i < 2 else "ok", latency_ms=float(i),
                    engine="redis" if i % 2 else "mongodb", shard="shard-1" if i % 2 else None)
    s = bus.snapshot()
    assert s["total_requests"] == 10 and s["errors"] == 2 and s["error_rate"] == 0.2
    assert s["by_engine"] == {"mongodb": 5, "redis": 5}
    assert s["shard_load"] == {"shard-1": 5}
    assert s["latency_ms"]["p50"] == 4.5
    assert s["throughput_10s"] == 1.0
    assert "total_requests" in s["metric_sources"]


def test_retention_limit_and_filters():
    bus = TelemetryBus(max_events=50)
    for i in range(120):
        bus.publish("a" if i % 2 else "b", "t", i=i)
    assert len(bus.events(limit=1000)) == 50
    assert all(e["type"] == "a" for e in bus.events(limit=10, event_type="a"))
    newest = bus.events(limit=1)[0]["id"]
    assert bus.events(since_id=newest) == []


def test_subscriber_errors_do_not_propagate():
    bus = TelemetryBus()
    bus.subscribe("x", lambda e: 1 / 0)
    bus.publish("x", "t")
    assert bus.snapshot()["subscriber_errors"] == 1


def test_thread_safety_counts():
    bus = TelemetryBus(max_events=100)

    def work():
        for _ in range(500):
            bus.publish("request", "t", status="ok", latency_ms=0.1)
    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert bus.snapshot()["total_requests"] == 4000


def test_reset():
    bus = TelemetryBus()
    bus.publish("request", "t", status="ok")
    bus.reset()
    assert bus.snapshot()["total_requests"] == 0 and bus.events() == []
