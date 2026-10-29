"""End-to-end API tests on the full FastAPI app (emulated Redis, optional engines disabled)."""
import time


def wait_job(client, job_id, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.3)
    raise AssertionError("job did not finish")


def test_health_and_status(api_client):
    h = api_client.get("/health").json()
    assert h["status"] == "ok" and h["redis_mode"] == "emulated"
    assert h["adapters"] == {"redis": "emulated", "mongodb": "disabled", "cassandra": "disabled"}
    st = api_client.get("/api/status").json()
    assert st["topology"]["active"] == ["shard-1", "shard-2", "shard-3", "shard-4"]
    assert "telemetry" in st and "consistency" in st


def test_adapters_endpoint_reports_truthfully(api_client):
    d = api_client.get("/api/adapters?deep=true").json()
    by = {a["name"]: a for a in d["adapters"]}
    assert by["redis"]["verified_round_trip"] is True
    assert by["mongodb"]["verified_round_trip"] is False and by["mongodb"]["status"] == "disabled"


def test_seed_and_execute_flow(api_client):
    assert api_client.get("/health").json()["seeded"] is False
    r = api_client.post("/api/seed", json={"reset": True}).json()
    assert r["redis"]["records"] == 6000
    assert api_client.get("/health").json()["seeded"] is True

    out = api_client.post("/api/requests/execute", json={"operation": "read", "key": "product:42"}).json()
    assert out["status"] == "ok" and out["engine"] == "redis"
    assert out["data"].startswith("product:42|")
    assert out["routing"]["classification"]["workload"] == "key-value"
    assert out["shard"]["id"].startswith("shard-") and out["latency_ms"] > 0
    assert out["consistency"]["level"] in ("ONE", "QUORUM", "ALL")

    w = api_client.post("/api/requests/execute", json={"operation": "write", "key": "kv:1", "value": "hello"}).json()
    assert w["status"] == "ok"
    assert api_client.post("/api/requests/execute", json={"operation": "read", "key": "kv:1"}).json()["data"] == "hello"
    d = api_client.post("/api/requests/execute", json={"operation": "delete", "key": "kv:1"}).json()
    assert d["status"] == "ok"
    assert api_client.post("/api/requests/execute", json={"operation": "read", "key": "kv:1"}).json()["status"] == "not_found"

    ok = api_client.post("/api/requests/execute", json={"operation": "login", "key": "user3", "value": "pass-3"}).json()
    bad = api_client.post("/api/requests/execute", json={"operation": "login", "key": "user3", "value": "nope"}).json()
    assert ok["data"]["authenticated"] is True and bad["status"] == "denied"
    sc = api_client.post("/api/requests/execute", json={"operation": "scan", "key": "product:1*", "limit": 5}).json()
    assert sc["data"]["count"] == 5 and all(k.startswith("product:1") for k in sc["data"]["keys"])

    tel = api_client.get("/api/telemetry?limit=5").json()
    assert tel["snapshot"]["total_requests"] >= 8 and tel["events"]


def test_document_request_falls_back_when_mongo_disabled(api_client):
    r = api_client.post("/api/requests/execute",
                        json={"operation": "write", "key": "order:1", "value": {"items": [1, 2]}}).json()
    assert r["status"] == "ok" and r["engine"] == "redis"
    assert r["routing"]["fallback_used"] is True and r["routing"]["preferred_engine"] == "mongodb"
    q = api_client.post("/api/requests/execute", json={"operation": "query", "filter": {"a": 1}})
    assert q.status_code == 503


def test_validation_errors(api_client):
    assert api_client.post("/api/requests/execute", json={"operation": "write", "key": "x"}).status_code == 422
    assert api_client.post("/api/requests/execute", json={"operation": "drop", "key": "x"}).status_code == 422
    assert api_client.post("/api/requests/execute", json={"operation": "read"}).status_code == 422
    assert api_client.post("/api/requests/execute",
                           json={"operation": "read", "key": "x", "client_id": "bad id!"}).status_code == 422
    assert api_client.post("/api/consistency/simulate", json={"phases": []}).status_code == 422


def test_shard_expansion_and_reset(api_client):
    api_client.post("/api/seed", json={"reset": True})
    r = api_client.post("/api/shards/rebalance", json={"action": "add_shard"}).json()
    assert r["status"] == "completed" and r["failed"] == 0 and r["count_reconciled"]
    assert r["keys_after"]["shard-5"] == r["moved"] > 0
    assert api_client.post("/api/requests/execute", json={"operation": "read", "key": "product:7"}).json()["status"] == "ok"
    again = api_client.post("/api/shards/rebalance", json={"action": "add_shard"})
    assert again.status_code == 400  # no spare shard left
    back = api_client.post("/api/shards/rebalance", json={"action": "reset"}).json()
    assert back["status"] == "completed"
    topo = api_client.get("/api/shards").json()["topology"]
    assert topo["active"] == ["shard-1", "shard-2", "shard-3", "shard-4"]
    loc = api_client.get("/api/shards/locate?key=product:7").json()
    assert loc["node"] in topo["active"]


def test_security_traffic_and_evaluation(api_client):
    assert api_client.post("/api/security/generate-traffic", json={}).status_code in (200, 409)
    api_client.post("/api/seed", json={"reset": True})
    api_client.post("/api/security/clear")
    g = api_client.post("/api/security/generate-traffic",
                        json={"normal_windows": 4, "attack_windows": 2, "seed": 77}).json()
    assert g["requests"] == (4 + 6) * 100
    assert g["statuses_by_kind"]["bruteforce"].get("denied", 0) > 50
    e = api_client.post("/api/security/evaluate").json()
    ev = e["synthetic_evaluation"]
    assert ev["windows"] == 10
    assert ev["isolation_forest"]["recall"] >= 0.8
    events = api_client.get("/api/security/events?only=anomaly").json()["events"]
    assert events and all(x["verdict"] == "anomaly" for x in events)
    t = api_client.post(f"/api/security/events/{events[0]['id']}/triage", json={"decision": "confirmed"})
    assert t.json()["triage"] == "confirmed"
    csv_text = api_client.get("/api/export/security-events").text
    assert csv_text.startswith("id,scored_at,client_id")
    s = api_client.post("/api/security/settings", json={"mode": "batched"}).json()
    assert s["mode"] == "batched"


def test_consistency_endpoints(api_client):
    st = api_client.post("/api/consistency/network", json={"congestion": 6}).json()
    assert st["profile"] != "ONE/ONE"
    r = api_client.post("/api/consistency/simulate",
                        json={"phases": [1, 6], "ops_per_phase": 400, "mc_samples": 300}).json()
    assert r["evidence"] == "simulation" and "HANF adaptive" in r["strategies"]


def test_benchmark_job_and_experiment_export(api_client):
    j = api_client.post("/api/benchmarks/run", json={
        "experiment": "e4", "profile": "quick",
        "overrides": {"phases": [1, 3], "ops_per_phase": 200, "mc_samples": 200, "seeds": [1],
                      "read_delays_ms": [15], "slos": [0.01]}}).json()
    job = wait_job(api_client, j["id"])
    assert job["status"] == "done", job.get("error")
    run_id = job["result"][0]["run_id"]
    runs = api_client.get("/api/experiments").json()["runs"]
    assert any(r["run_id"] == run_id for r in runs)
    detail = api_client.get(f"/api/experiments/{run_id}").json()
    assert detail["status"] == "completed" and "e4_summary.csv" in detail["files"]
    assert api_client.get(f"/api/experiments/{run_id}/files/e4_summary.csv").status_code == 200
    assert api_client.get(f"/api/experiments/{run_id}/files/..%2Fconfig.json").status_code == 404
    z = api_client.get(f"/api/experiments/{run_id}/download")
    assert z.status_code == 200 and z.content[:2] == b"PK"
    assert api_client.get("/api/experiments/nope-123").status_code == 404
    assert isinstance(api_client.get("/api/experiments/report-runs").json()["runs"], dict)
    assert api_client.get("/api/export/telemetry").json()["snapshot"]


def test_dashboard_page_served(api_client):
    r = api_client.get("/")
    assert r.status_code == 200 and "HANF" in r.text
    assert api_client.get("/static/app.js").status_code == 200
