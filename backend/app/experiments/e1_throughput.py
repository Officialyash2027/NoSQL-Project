"""E1 - throughput and latency under YCSB-style workloads (live Redis).

Workloads (YCSB core): A = 50 % read / 50 % update, B = 95 / 5, C = 100 % read; scrambled Zipfian
key popularity (theta 0.99). Configurations:
* ``direct``          - redis-py GET/SET on one Redis instance (shard 5, experiment DB);
* ``hanf``            - the full HANF pipeline (router + hash ring over 4 shards + telemetry),
                        security monitor off;
* ``hanf_sync_ml``    - + Isolation Forest scoring inline for every completed 100-request window;
* ``hanf_batched_ml`` - + windows queued and scored inline 20 at a time (one model call);
* ``hanf_async_ml``   - + windows scored by a background worker thread (off the request path).
A single client thread issues requests sequentially; latency is measured per operation around the
client call. Runs are interleaved (rep -> workload -> config) to spread machine drift evenly.
"""
from __future__ import annotations

import time

import numpy as np

from ..core.service import HANFService
from . import plotstyle
from .common import ExperimentContext, flush_experiment_db, summarize_latencies, zipf_sampler

WORKLOADS = {"A": 0.50, "B": 0.95, "C": 1.00}   # read proportion
CONFIG_LABELS = {
    "direct": "Direct Redis (baseline)",
    "hanf": "HANF routing, 4 shards",
    "hanf_sync_ml": "HANF + synchronous ML scoring",
    "hanf_batched_ml": "HANF + batched ML scoring",
    "hanf_async_ml": "HANF + async ML scoring",
}
SECURITY_MODE = {"hanf": "off", "hanf_sync_ml": "sync", "hanf_batched_ml": "batched",
                 "hanf_async_ml": "async"}


def _load(ctx, shards, direct, keys, value_bytes, rng):
    vals = [rng.bytes(value_bytes // 2).hex().encode()[:value_bytes] for _ in range(1000)]
    pd = direct.client.pipeline(transaction=False)
    ring_pipes = {}
    from ..sharding.hashring import ConsistentHashRing

    ring = ConsistentHashRing([s.id for s in shards[:4]], ctx.settings.vnodes)
    by_id = {s.id: s for s in shards}
    for i, k in enumerate(keys):
        v = vals[i % len(vals)]
        pd.set(k, v)
        sid = ring.get_node(k.decode())
        p = ring_pipes.setdefault(sid, by_id[sid].client.pipeline(transaction=False))
        p.set(k, v)
        if len(pd) >= 2000:
            pd.execute()
            for p in ring_pipes.values():
                p.execute()
    pd.execute()
    for p in ring_pipes.values():
        p.execute()
    return vals


def _run_direct(client, is_read, key_idx, keys, vals):
    n = len(key_idx)
    lat = np.empty(n)
    errors = 0
    pc = time.perf_counter_ns
    t0 = time.perf_counter()
    for i in range(n):
        k = keys[key_idx[i]]
        s = pc()
        try:
            if is_read[i]:
                client.get(k)
            else:
                client.set(k, vals[i % len(vals)], xx=True)
        except Exception:  # noqa: BLE001
            errors += 1
        lat[i] = pc() - s
    return lat / 1000.0, time.perf_counter() - t0, errors


def _run_hanf(svc: HANFService, is_read, key_idx, keys, vals, client_id):
    n = len(key_idx)
    lat = np.empty(n)
    errors = 0
    pc = time.perf_counter_ns
    ex = svc.execute
    t0 = time.perf_counter()
    for i in range(n):
        k = keys[key_idx[i]]
        s = pc()
        if is_read[i]:
            r = ex("read", k, explain=False, client_id=client_id)
        else:
            r = ex("update", k, vals[i % len(vals)], explain=False, client_id=client_id)
        lat[i] = pc() - s
        if r["status"] != "ok":
            errors += 1
    return lat / 1000.0, time.perf_counter() - t0, errors


def pipeline_overhead(svc: HANFService, keys: list[str], n: int = 20000) -> dict:
    """CPU cost of HANF's own decision steps per request, without any database I/O."""
    ks = [keys[i % len(keys)] for i in range(n)]
    out = {}
    pc = time.perf_counter
    t = pc()
    for k in ks:
        svc.router.route("read", k)
    out["router_classify_route_us"] = (pc() - t) / n * 1e6
    t = pc()
    for k in ks:
        svc.shards.shard_for(k)
    out["hash_ring_lookup_us"] = (pc() - t) / n * 1e6
    t = pc()
    for k in ks:
        svc.consistency.level_for("read")
    out["consistency_level_us"] = (pc() - t) / n * 1e6
    t = pc()
    for i, k in enumerate(ks):
        svc.telemetry.publish("request", "bench", operation="read", key=k, engine="redis",
                              shard="shard-1", status="ok", latency_ms=0.1, payload_bytes=100)
    out["telemetry_publish_us"] = (pc() - t) / n * 1e6
    out |= _stubbed_pipeline(svc.settings, ks)
    return {k: round(v, 2) for k, v in out.items()} | {"samples": n}


class _StubClient:
    """In-memory stand-in for a Redis connection: isolates HANF's own CPU cost from network I/O."""

    def get(self, k):
        return b"x" * 100

    def set(self, k, v, xx=False):
        return True

    def ping(self):
        return True


def _stubbed_pipeline(settings, keys: list[str]) -> dict:
    from ..sharding.manager import Shard

    stub = [Shard(f"shard-{i}", "stub", 0, _StubClient()) for i in range(1, 6)]
    svc = HANFService(settings, shards=stub, enable_optional=False, security_mode="off",
                      topology_file=False)
    out = {}
    pc = time.perf_counter
    for name, op, val in (("full_pipeline_read_no_io_us", "read", None),
                          ("full_pipeline_update_no_io_us", "update", "v" * 100)):
        for k in keys[:500]:
            svc.execute(op, k, val, explain=False)
        t = pc()
        for k in keys:
            svc.execute(op, k, val, explain=False)
        out[name] = (pc() - t) / len(keys) * 1e6
    return out


def run(ctx: ExperimentContext, cfg: dict) -> dict:
    shards = ctx.experiment_shards()
    writer = ctx.store.new_run("e1", cfg, ctx.env(shards))
    rows: list[dict] = []
    try:
        for s in shards:
            s.client.ping()
        flush_experiment_db(shards)
        direct = shards[4] if len(shards) >= 5 else shards[0]
        rng = np.random.default_rng(cfg["seed"])
        keys = [f"ycsb:user{i}".encode() for i in range(cfg["records"])]
        ctx.progress(f"E1: loading {cfg['records']} records into direct instance and 4 HANF shards")
        vals = [v.decode() for v in _load(ctx, shards, direct, keys, cfg["value_bytes"], rng)]
        str_keys = [k.decode() for k in keys]
        bvals = [v.encode() for v in vals]
        draw = zipf_sampler(cfg["records"], cfg["zipf_theta"], cfg["seed"])

        services: dict[str, HANFService] = {}
        for c in cfg["configs"]:
            if c == "direct":
                continue
            svc = HANFService(ctx.settings, shards=shards, enable_optional=False,
                              security_mode=SECURITY_MODE[c], topology_file=False)
            svc.security.ensure_model()
            svc.security.start()
            services[c] = svc

        total = cfg["repetitions"] * len(cfg["workloads"]) * len(cfg["configs"])
        done = 0
        for rep in range(cfg["repetitions"]):
            for wl in cfg["workloads"]:
                op_rng = np.random.default_rng(cfg["seed"] * 1000 + rep * 10 + ord(wl))
                n = cfg["operations"]
                is_read = op_rng.random(n) < WORKLOADS[wl]
                key_idx = draw(op_rng, n)
                warm_idx = draw(op_rng, cfg["warmup"])
                warm_read = np.ones(cfg["warmup"], dtype=bool)
                for c in cfg["configs"]:
                    done += 1
                    ctx.progress(f"E1: rep {rep + 1}/{cfg['repetitions']} workload {wl} {c} ({done}/{total})")
                    extra = {}
                    if c == "direct":
                        _run_direct(direct.client, warm_read, warm_idx, keys, bvals)
                        lat, elapsed, errors = _run_direct(direct.client, is_read, key_idx, keys, bvals)
                    else:
                        svc = services[c]
                        cid = f"ycsb-{wl}-{rep}" if SECURITY_MODE[c] != "off" else None
                        _run_hanf(svc, warm_read, warm_idx, str_keys, vals, None)
                        svc.security.clear()
                        lat, elapsed, errors = _run_hanf(svc, is_read, key_idx, str_keys, vals, cid)
                        st = svc.security.stats
                        extra = {"windows_queued": st.get("windows_queued", 0),
                                 "windows_scored_during_run": st.get("windows_scored", 0),
                                 "backlog_at_end": len(svc.security._queue)}
                        svc.security.flush()
                    row = {"rep": rep, "workload": wl, "config": c, "operations": n,
                           "throughput_ops_s": n / elapsed, "elapsed_s": elapsed, "errors": errors,
                           **summarize_latencies(lat), **extra}
                    rows.append(row)
        for svc in services.values():
            svc.security.stop()
        overhead = None
        if "hanf" in services:
            ctx.progress("E1: measuring pipeline CPU overhead without I/O")
            fresh = HANFService(ctx.settings, shards=shards, enable_optional=False,
                                security_mode="off", topology_file=False)
            overhead = pipeline_overhead(fresh, str_keys)
    except Exception as exc:  # noqa: BLE001
        writer.record_failure("run", f"{type(exc).__name__}: {exc}")
        writer.write_csv("e1_runs.csv", rows)
        flush_experiment_db(shards)
        return writer.finish({"runs": rows}, status="failed")
    flush_experiment_db(shards)

    writer.write_csv("e1_runs.csv", rows)
    summary = summarize(rows, cfg)
    writer.write_csv("e1_summary.csv", summary)
    if overhead:
        writer.write_csv("e1_pipeline_overhead.csv", [overhead])
    plot(writer, summary, cfg)
    return writer.finish({"summary": summary, "pipeline_overhead_us": overhead, "runs": rows})


def summarize(rows: list[dict], cfg: dict) -> list[dict]:
    out = []
    for wl in cfg["workloads"]:
        base = None
        for c in cfg["configs"]:
            rs = [r for r in rows if r["workload"] == wl and r["config"] == c]
            if not rs:
                continue
            tp = np.array([r["throughput_ops_s"] for r in rs])
            row = {"workload": wl, "config": c, "label": CONFIG_LABELS[c], "runs": len(rs),
                   "throughput_mean": round(float(tp.mean()), 1),
                   "throughput_sd": round(float(tp.std(ddof=1)) if len(tp) > 1 else 0.0, 1),
                   "p50_us": round(float(np.mean([r["p50_us"] for r in rs])), 1),
                   "p95_us": round(float(np.mean([r["p95_us"] for r in rs])), 1),
                   "p99_us": round(float(np.mean([r["p99_us"] for r in rs])), 1),
                   "errors": int(sum(r["errors"] for r in rs))}
            if c == "direct":
                base = row["throughput_mean"]
            row["delta_vs_direct_pct"] = (round(100 * (row["throughput_mean"] - base) / base, 1)
                                          if base else None)
            if "backlog_at_end" in rs[0]:
                row["mean_backlog_at_end"] = round(float(np.mean([r["backlog_at_end"] for r in rs])), 1)
            out.append(row)
    return out


def plot(writer, summary: list[dict], cfg: dict) -> None:
    wls = cfg["workloads"]
    configs = [c for c in cfg["configs"] if any(r["config"] == c for r in summary)]

    def series(field):
        return {CONFIG_LABELS[c]: [next((r[field] for r in summary if r["workload"] == w and r["config"] == c), 0)
                                   for w in wls] for c in configs}

    fig, ax = plotstyle.subplots(figsize=(7.2, 3.4))
    tp = {k: [v / 1000 for v in vals] for k, vals in series("throughput_mean").items()}
    sd = {k: [v / 1000 for v in vals] for k, vals in series("throughput_sd").items()}
    plotstyle.grouped_bars(ax, [f"Workload {w}" for w in wls], tp, sd)
    ax.set_ylabel("Throughput (thousand ops/s)")
    ax.set_title("E1 - Throughput by workload and configuration (mean ± SD)")
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    writer.add_graph(fig, "e1_throughput.png")

    fig, ax = plotstyle.subplots(figsize=(7.2, 3.4))
    plotstyle.grouped_bars(ax, [f"Workload {w}" for w in wls],
                           {k: [v / 1000 for v in vals] for k, vals in series("p99_us").items()})
    ax.set_ylabel("p99 latency (ms)")
    ax.set_title("E1 - Tail (p99) latency by workload and configuration")
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    writer.add_graph(fig, "e1_p99_latency.png")
