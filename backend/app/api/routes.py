"""REST API. Interactive documentation: /docs (Swagger UI) and /redoc."""
from __future__ import annotations

import csv
import io
import json
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from ..adapters.base import AdapterUnavailable
from ..consistency.simulator import NetworkModel, run_timeline
from ..experiments.common import ExperimentContext, load_profile
from ..experiments.store import EXPERIMENTS
from ..models.schemas import (BenchmarkRequest, CongestionRequest, ConsistencySimRequest,
                              ExecuteRequest, SecuritySettingsRequest, SeedRequest,
                              ShardActionRequest, TrafficRequest, TrainRequest, TriageRequest)
from ..security.features import FEATURE_HELP

router = APIRouter()


def svc(request: Request):
    return request.app.state.service


def _bad(exc: Exception, code: int = 400):
    raise HTTPException(status_code=code, detail=str(exc)) from exc


# ------------------------------------------------------------------------------ system
@router.get("/health", tags=["system"])
def health(request: Request):
    s = svc(request)
    adapters = {a["name"]: a["status"] for a in s.adapters_health()}
    redis_ok = adapters.get("redis") in ("connected", "emulated")
    return {"status": "ok" if redis_ok else "degraded", "redis_mode": s.settings.redis_mode,
            "adapters": adapters, "seeded": s.is_seeded(), "time": time.time()}


@router.get("/api/status", tags=["system"])
def status(request: Request):
    return svc(request).status()


@router.get("/api/adapters", tags=["router"])
def adapters(request: Request, deep: bool = False):
    s = svc(request)
    return {"adapters": s.adapters_health(deep=deep),
            "routing_rules": s.router.classifier.rules,
            "fallback_to_kv": s.router.fallback_to_kv}


@router.post("/api/adapters/{name}/reconnect", tags=["router"])
def reconnect(name: str, request: Request):
    s = svc(request)
    if name not in s.adapters:
        raise HTTPException(404, f"unknown adapter {name}")
    return s.reconnect(name)


@router.post("/api/seed", tags=["system"])
def seed(body: SeedRequest, request: Request):
    try:
        return svc(request).seed(body.reset, body.include_optional)
    except Exception as exc:  # noqa: BLE001
        _bad(exc, 503)


@router.post("/api/classify", tags=["router"])
def classify(body: ExecuteRequest, request: Request):
    """Classification and routing decision only (nothing is executed)."""
    try:
        return svc(request).router.route(body.operation, body.key, body.value, body.filter,
                                         body.hint, body.engine).to_dict()
    except AdapterUnavailable as exc:
        _bad(exc, 503)
    except ValueError as exc:
        _bad(exc)


# ------------------------------------------------------------------------------ requests
@router.post("/api/requests/execute", tags=["router"])
def execute(body: ExecuteRequest, request: Request):
    s = svc(request)
    try:
        return s.execute(body.operation, body.key, body.value, body.filter, body.limit, body.hint,
                         body.engine, body.client_id, body.client_ts, body.synthetic_label,
                         explain=True, consistency=body.consistency)
    except AdapterUnavailable as exc:
        _bad(exc, 503)
    except ValueError as exc:
        _bad(exc)


@router.get("/api/telemetry", tags=["telemetry"])
def telemetry(request: Request, limit: int = Query(100, ge=1, le=2000),
              type: str | None = None, since_id: int | None = None):
    bus = svc(request).telemetry
    return {"snapshot": bus.snapshot(), "events": bus.events(limit, type, since_id)}


@router.post("/api/telemetry/reset", tags=["telemetry"])
def telemetry_reset(request: Request):
    svc(request).telemetry.reset()
    return {"reset": True}


# ------------------------------------------------------------------------------ shards
@router.get("/api/shards", tags=["sharding"])
def shards(request: Request):
    s = svc(request)
    return {"health": s.shards.health(), "topology": s.shards.topology()}


@router.get("/api/shards/locate", tags=["sharding"])
def locate(request: Request, key: str = Query(..., max_length=512)):
    s = svc(request)
    loc = s.shards.ring.locate(key)
    loc["port"] = s.shards.shards[loc["node"]].port
    return loc


@router.post("/api/shards/rebalance", tags=["sharding"])
def rebalance(body: ShardActionRequest, request: Request):
    m = svc(request).shards
    try:
        if body.action == "add_shard":
            return m.add_shard(body.shard_id).to_dict()
        if body.action == "remove_shard":
            if not body.shard_id:
                raise ValueError("shard_id is required to remove a shard")
            return m.remove_shard(body.shard_id).to_dict()
        if body.action == "rebalance_by_load":
            return m.rebalance_by_load(alpha=body.alpha, min_requests=body.min_requests)
        if body.action == "retry":
            return m.retry_migration().to_dict()
        r = m.reset_to_initial()
        return r.to_dict() if r else {"status": "already at initial topology"}
    except (ValueError, KeyError) as exc:
        _bad(exc)
    except Exception as exc:  # noqa: BLE001 - e.g. the spare shard is down
        _bad(exc, 503)


# ------------------------------------------------------------------------------ security
@router.get("/api/security/metrics", tags=["security"])
def security_metrics(request: Request):
    s = svc(request)
    return s.security.live_metrics() | {"feature_help": FEATURE_HELP,
                                        "static_rules": s.security.rules.thresholds}


@router.get("/api/security/events", tags=["security"])
def security_events(request: Request, limit: int = Query(100, ge=1, le=2000),
                    only: str | None = Query(None, pattern="^anomaly$")):
    return {"events": svc(request).security.history(limit, only)}


@router.post("/api/security/events/{window_id}/triage", tags=["security"])
def triage(window_id: int, body: TriageRequest, request: Request):
    try:
        return svc(request).security.triage(window_id, body.decision)
    except KeyError:
        raise HTTPException(404, f"window {window_id} not in history") from None


@router.post("/api/security/generate-traffic", tags=["security"])
def generate_traffic(body: TrafficRequest, request: Request):
    s = svc(request)
    if not s.is_seeded():
        raise HTTPException(409, "dataset not seeded - call POST /api/seed first so that synthetic "
                                 "normal traffic hits existing keys")
    return s.generate_traffic(body.normal_windows, body.attack_windows, tuple(body.attack_kinds),
                              body.subtle_fraction, body.seed)


@router.post("/api/security/evaluate", tags=["security"])
def security_evaluate(request: Request):
    """Score every queued window now, then return detection metrics on synthetic windows."""
    s = svc(request)
    t0 = time.perf_counter()
    n = s.security.flush()
    out = s.security.live_metrics()
    out["flushed_windows"] = n
    out["flush_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return out


@router.post("/api/security/train", tags=["security"])
def security_train(body: TrainRequest, request: Request):
    return svc(request).security.train(body.n_train, body.contamination)


@router.post("/api/security/settings", tags=["security"])
def security_settings(body: SecuritySettingsRequest, request: Request):
    mon = svc(request).security
    try:
        if body.mode:
            mon.set_mode(body.mode)
        if body.clear_threshold:
            mon.threshold_override = None
        elif body.threshold_override is not None:
            mon.threshold_override = body.threshold_override
    except ValueError as exc:
        _bad(exc)
    return mon.live_metrics()


@router.post("/api/security/clear", tags=["security"])
def security_clear(request: Request):
    svc(request).security.clear()
    return {"cleared": True}


# ------------------------------------------------------------------------------ consistency
@router.get("/api/consistency", tags=["consistency"])
def consistency_state(request: Request):
    return svc(request).consistency.state()


@router.post("/api/consistency/network", tags=["consistency"])
def consistency_network(body: CongestionRequest, request: Request):
    """Feed a new congestion estimate to the live controller; it re-selects the profile."""
    return svc(request).consistency.update(body.congestion, source="api")


@router.post("/api/consistency/simulate", tags=["consistency"])
def consistency_simulate(body: ConsistencySimRequest):
    try:
        model = NetworkModel(**body.network.model_dump())
    except ValueError as exc:
        _bad(exc)
    t0 = time.perf_counter()
    res = run_timeline(model, body.phases, body.ops_per_phase, body.slo, body.mc_samples, body.seed,
                       body.include_oracle)
    res["elapsed_s"] = round(time.perf_counter() - t0, 3)
    res["evidence"] = "simulation"
    return res


# ------------------------------------------------------------------------------ experiments
@router.post("/api/benchmarks/run", tags=["experiments"])
def benchmark_run(body: BenchmarkRequest, request: Request):
    from ..experiments.runner import MODULES  # lazy: pulls in Matplotlib/SciPy

    app = request.app
    settings = svc(request).settings
    names = list(MODULES) if body.experiment == "all" else [body.experiment]
    try:
        for n in names:
            load_profile(settings, body.profile, n, body.overrides)
    except ValueError as exc:
        _bad(exc)

    def job(progress):
        docs = []
        for n in names:
            cfg = load_profile(settings, body.profile, n, body.overrides)
            doc = MODULES[n].run(ExperimentContext(settings, progress), cfg)
            docs.append({"run_id": doc["run_id"], "status": doc["status"], "graphs": doc["graphs"],
                         "failures": doc["failures"], "duration_s": doc["duration_s"]})
        return docs

    return app.state.jobs.submit(f"benchmark:{body.experiment}", body.model_dump(), job)


@router.get("/api/jobs", tags=["experiments"])
def jobs(request: Request):
    return {"jobs": request.app.state.jobs.list()}


@router.get("/api/jobs/{job_id}", tags=["experiments"])
def job(job_id: str, request: Request):
    try:
        return request.app.state.jobs.get(job_id)
    except KeyError:
        raise HTTPException(404, "unknown job") from None


@router.get("/api/experiments", tags=["experiments"])
def experiments(request: Request, experiment: str | None = None):
    if experiment and experiment not in EXPERIMENTS:
        raise HTTPException(400, f"experiment must be one of {list(EXPERIMENTS)}")
    return {"experiments": EXPERIMENTS, "runs": request.app.state.store.list_runs(experiment)}


@router.get("/api/experiments/report-runs", tags=["experiments"])
def report_runs(request: Request):
    """Runs used by the generated IEEE report (reports/data/runs_used.json), if it has been built."""
    p = svc(request).settings.root / "reports" / "data" / "runs_used.json"
    if not p.exists():
        return {"runs": {}}
    return {"runs": json.loads(p.read_text())}


@router.get("/api/experiments/{run_id}", tags=["experiments"])
def experiment(run_id: str, request: Request):
    try:
        return request.app.state.store.get_run(run_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "unknown run") from None


@router.get("/api/experiments/{run_id}/files/{name}", tags=["export"])
def experiment_file(run_id: str, name: str, request: Request):
    try:
        d = request.app.state.store.run_dir(run_id)
    except FileNotFoundError:
        raise HTTPException(404, "unknown run") from None
    p = (d / name).resolve()
    if p.parent != d or not p.is_file():
        raise HTTPException(404, "unknown file")
    return FileResponse(p, filename=name if not name.endswith(".png") else None)


@router.get("/api/experiments/{run_id}/download", tags=["export"])
def experiment_zip(run_id: str, request: Request):
    try:
        data = request.app.state.store.zip_run(run_id)
    except FileNotFoundError:
        raise HTTPException(404, "unknown run") from None
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{run_id}.zip"'})


@router.get("/api/export/telemetry", tags=["export"])
def export_telemetry(request: Request, limit: int = Query(5000, ge=1, le=50000)):
    bus = svc(request).telemetry
    body = json.dumps({"exported_at": time.time(), "snapshot": bus.snapshot(),
                       "events": bus.events(limit)}, indent=2, default=str)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="hanf_telemetry.json"'})


@router.get("/api/export/security-events", tags=["export"])
def export_security(request: Request):
    rows = svc(request).security.history(2000)
    buf = io.StringIO()
    fields = ["id", "scored_at", "client_id", "synthetic_label", "verdict", "anomaly_score",
              "threshold", "rule_verdict", "triage", "batch_size", "queue_delay_ms", "features", "explanation"]
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: (json.dumps(r[k]) if isinstance(r.get(k), (dict, list)) else r.get(k)) for k in fields})
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="hanf_security_events.csv"'})
