"""End-to-end smoke test of a RUNNING HANF server (the primary demonstration flow).

Usage:  .venv\\Scripts\\python scripts\\smoke_test.py
Each step prints PASS/FAIL/SKIP; results are written to results/smoke/<run_id>/results.json.
Exit code 0 only if no step failed. The demo state is restored at the end (shards reset,
dataset re-seeded).
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

from hanf_client import ApiError, call

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

steps: list[dict] = []


def step(name):
    def deco(fn):
        t0 = time.perf_counter()
        try:
            detail = fn()
            status = "SKIP" if isinstance(detail, str) and detail.startswith("SKIP") else "PASS"
        except AssertionError as e:
            status, detail = "FAIL", f"assertion: {e}"
        except Exception as e:  # noqa: BLE001
            status, detail = "FAIL", f"{type(e).__name__}: {e}"
            traceback.print_exc()
        ms = round((time.perf_counter() - t0) * 1000)
        steps.append({"step": name, "status": status, "detail": detail, "ms": ms})
        print(f"[{status}] {name} ({ms} ms) - {detail}")
        return fn
    return deco


@step("1. health")
def _():
    h = call("/health")
    assert h["status"] == "ok", h
    return f"redis={h['adapters']['redis']} mongodb={h['adapters']['mongodb']} cassandra={h['adapters']['cassandra']} mode={h['redis_mode']}"


@step("2. reset topology to initial shards")
def _():
    call("/api/shards/rebalance", {"action": "reset"})
    t = call("/api/shards")["topology"]
    assert len(t["active"]) == 4, t["active"]
    return f"active={t['active']}"


@step("3. seed deterministic dataset")
def _():
    r = call("/api/seed", {"reset": True})
    assert r["redis"]["records"] == 6000
    return f"redis={r['redis']['records']} mongodb={r['mongodb']} cassandra={r['cassandra']}"


@step("4. key-value read routed to a Redis shard")
def _():
    r = call("/api/requests/execute", {"operation": "read", "key": "product:42"})
    assert r["status"] == "ok" and r["engine"] == "redis" and r["shard"]["id"].startswith("shard-")
    assert r["data"].startswith("product:42|")
    return f"shard={r['shard']['id']} latency={r['latency_ms']}ms"


@step("5. key-value write then read back")
def _():
    call("/api/requests/execute", {"operation": "write", "key": "product:smoke", "value": "smoke-value"})
    r = call("/api/requests/execute", {"operation": "read", "key": "product:smoke"})
    assert r["data"] == "smoke-value"
    return "value round-tripped"


@step("6. document request (MongoDB, or flagged fallback)")
def _():
    r = call("/api/requests/execute", {"operation": "write", "key": "order:smoke",
                                       "value": {"status": "new", "items": [{"sku": "product:1", "qty": 1}]}})
    assert r["status"] == "ok"
    assert r["engine"] == "mongodb" or r["routing"]["fallback_used"]
    return f"engine={r['engine']} fallback={r['routing']['fallback_used']}"


@step("7. wide-column request (Cassandra, or flagged fallback)")
def _():
    r = call("/api/requests/execute", {"operation": "write", "key": "sensor:smoke",
                                       "value": {"ts": "2026-10-18T09:00:00", "temp_c": 20.5}})
    assert r["status"] == "ok"
    assert r["engine"] == "cassandra" or r["routing"]["fallback_used"]
    return f"engine={r['engine']} level={r['consistency']['level']} ({r['consistency']['applied']})"


@step("8. telemetry received request events")
def _():
    t = call("/api/telemetry?limit=5")
    assert t["snapshot"]["total_requests"] >= 4
    return f"requests={t['snapshot']['total_requests']} p50={t['snapshot']['latency_ms']['p50']}ms"


@step("9. synthetic normal + attack traffic through the pipeline")
def _():
    call("/api/security/clear", method="POST")
    r = call("/api/security/generate-traffic", {"normal_windows": 6, "attack_windows": 2, "seed": 4242}, timeout=300)
    assert r["requests"] == 1200
    return f"{r['requests']} requests in {r['elapsed_s']}s; statuses={r['statuses_by_kind']}"


@step("10. security evaluation")
def _():
    for _ in range(20):
        if call("/api/security/metrics")["model"]:
            break
        time.sleep(1)
    e = call("/api/security/evaluate", method="POST")
    ev = e["synthetic_evaluation"]
    m, rl = ev["isolation_forest"], ev["static_rules"]
    assert ev["windows"] == 12
    return (f"IF P={m['precision']} R={m['recall']} F1={m['f1']} FPR={m['fpr']} | "
            f"rules P={rl['precision']} R={rl['recall']} F1={rl['f1']}")


SAMPLE: dict[str, object] = {}


@step("11. add Redis shard and migrate keys")
def _():
    # snapshot values right before the migration (step 9 traffic legitimately rewrote some keys)
    for i in range(0, 5000, 100):
        SAMPLE[f"product:{i}"] = call("/api/requests/execute", {"operation": "read", "key": f"product:{i}"})["data"]
    before = call("/api/shards")
    if not before["topology"]["spare"]:
        return "SKIP no spare shard configured"
    r = call("/api/shards/rebalance", {"action": "add_shard"})
    assert r["status"] == "completed" and r["failed"] == 0 and r["count_reconciled"], r
    return f"moved={r['moved']} ({r['remapped_pct']}%) verified={r['verified']} total={r['total_before']}->{r['total_after']} in {r['elapsed_s']}s"


@step("12. values readable after migration")
def _():
    assert SAMPLE, "no pre-migration sample"
    bad = 0
    for k, v in SAMPLE.items():
        r = call("/api/requests/execute", {"operation": "read", "key": k})
        bad += r["data"] != v
    assert bad == 0, f"{bad} mismatches"
    return f"{len(SAMPLE)} sampled keys identical before and after migration"


@step("13. consistency: live controller + simulation")
def _():
    c = call("/api/consistency/network", {"congestion": 6})
    s = call("/api/consistency/simulate", {"ops_per_phase": 1000, "mc_samples": 1000})
    a, q = s["strategies"]["HANF adaptive"], s["strategies"]["static QUORUM/QUORUM"]
    assert a["stale_pct"] <= 1.0
    call("/api/consistency/network", {"congestion": 1})
    return f"profile@6x={c['profile']}; adaptive {a['mean_ms']}ms/{a['stale_pct']}% stale vs QUORUM {q['mean_ms']}ms (simulation)"


@step("14. benchmark job (E4 quick) and experiment export")
def _():
    j = call("/api/benchmarks/run", {"experiment": "e4", "profile": "quick"})
    for _ in range(300):
        j = call(f"/api/jobs/{j['id']}")
        if j["status"] in ("done", "failed"):
            break
        time.sleep(1)
    assert j["status"] == "done", j.get("error")
    run_id = j["result"][0]["run_id"]
    z = call(f"/api/experiments/{run_id}/download")
    assert z[:2] == b"PK"
    return f"run {run_id}, zip {len(z)} bytes"


@step("15. restore demo state")
def _():
    call("/api/shards/rebalance", {"action": "reset"})
    call("/api/seed", {"reset": True})
    return "shards reset to initial, dataset re-seeded"


def main() -> int:
    failed = [s for s in steps if s["status"] == "FAIL"]
    try:
        from app.config import get_settings
        from app.experiments.store import ExperimentStore, environment
        st = get_settings()
        w = ExperimentStore(st.results_dir, st.graphs_dir).new_run("smoke", {"base_url": __import__("hanf_client").BASE},
                                                                   environment(st.root))
        doc = w.finish({"steps": steps}, status="completed" if not failed else "failed")
        print(f"\nresults: results/smoke/{doc['run_id']}/results.json")
    except Exception as e:  # noqa: BLE001
        print(f"(could not write results file: {e})")
    print(f"\n{len(steps) - len(failed)}/{len(steps)} steps passed" + (f"; FAILED: {[s['step'] for s in failed]}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
