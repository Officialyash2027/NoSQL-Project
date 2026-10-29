# HANF REST API reference

Base URL: `http://127.0.0.1:8000`. Interactive documentation generated from the code:
**`/docs`** (Swagger UI) and `/redoc`. All bodies are JSON.

**Status codes.** 200 success (including operation outcomes such as `not_found`, `denied`,
`rejected`, which are returned in the body); 400 invalid action (e.g. no spare shard);
404 unknown run/job/window; 409 dataset not seeded; 422 validation error (FastAPI format:
`{"detail": [{"loc": [...], "msg": "..."}]}`); 503 engine unavailable and no fallback;
500 unexpected error (`{"detail": "<type>: <message>"}`).

---

## System

### `GET /health`
```json
{"status": "ok", "redis_mode": "live",
 "adapters": {"redis": "connected", "mongodb": "connected", "cassandra": "connected"},
 "seeded": true, "time": 1792335845.56}
```
`status` is `degraded` when the Redis path is not connected.

### `GET /api/status`
Everything the dashboard shows: `adapters`, `shards`, `topology`, `security`, `consistency`,
`telemetry` (snapshot), `seeded`, `uptime_s`, `redis_mode`.

### `POST /api/seed`
Body: `{"reset": false, "include_optional": true}`. `reset=true` runs FLUSHDB on the HANF Redis DB
of every shard first. Loads 1,000 `user:*` + 5,000 `product:*` values (100–400 bytes, deterministic),
50 synthetic login users (`user0..user49`, password `pass-<i>`), 50 MongoDB `order:*` documents and
100 Cassandra rows (`sensor:0..4`) when those engines are connected.
```json
{"reset": true, "redis": {"records": 6000, "login_users": 50, "per_shard_written": {"shard-1": 1455, "...": 0}},
 "mongodb": {"documents": 50}, "cassandra": {"rows": 100, "partitions": 5}, "elapsed_s": 1.8}
```

## Router and requests

### `GET /api/adapters?deep=false`
`adapters[]`: `name, engine, data_model, status (connected|emulated|unavailable|disabled|connecting|degraded),
detail, version, latency_ms, capabilities, verified_round_trip`; plus `routing_rules` and
`fallback_to_kv`. `deep=true` performs a ping (and for Redis a write/read/delete round trip).

### `POST /api/adapters/{name}/reconnect`
`name` ∈ `redis, mongodb, cassandra`. Retries the connection and returns the new health record.

### `POST /api/requests/execute`
| Field | Type | Notes |
|---|---|---|
| `operation` | `read · write · update · delete · query · scan · login` | required |
| `key` | string ≤ 512 | required except for `scan` (pattern, default `*`) and `query` |
| `value` | any JSON | required for write/update/login; ≤ 64 KiB serialized |
| `filter` | object | required for `query`; equality on scalars only |
| `limit` | 1–200 | scan/query result cap (default 50) |
| `hint` | `key-value · document · wide-column` | optional router hint |
| `engine` | `redis · mongodb · cassandra` | optional override |
| `consistency` | `ONE · QUORUM · ALL` | optional override of the controller |
| `client_id` | `[A-Za-z0-9_.:-]{1,64}` | enables security windows for this client |
| `client_ts`, `synthetic_label` | number, string | used by the synthetic traffic generator |

Example:
```bash
curl -X POST localhost:8000/api/requests/execute -H "content-type: application/json" \
     -d '{"operation":"read","key":"product:42"}'
```
Response (abridged):
```json
{"request_id": 1, "status": "ok", "data": "product:42|1cc40f...", "error": null,
 "engine": "redis", "latency_ms": 1.41,
 "routing": {"classification": {"workload": "key-value", "scores": {"key-value": 3.5, "document": 0.0, "wide-column": 0.0},
             "confidence": 1.0, "reasons": ["+3 key-value: key prefix 'product' is mapped to key-value", "+0.5 key-value: default tie-breaker"]},
             "preferred_engine": "redis", "engine": "redis", "fallback_used": false, "reason": "..."},
 "consistency": {"level": "ONE", "profile": "QUORUM/ONE", "applied": "advisory - Redis shards here are independent primaries without replicas"},
 "meta": {"shard": "shard-1", "port": 7001, "response_bytes": 292},
 "shard": {"id": "shard-1", "ring_position": 0.982696, "token_position": 0.982966, "port": 7001, "migration_in_progress": false},
 "security": {"client_id": null, "injection_signal": false, "monitor_mode": "async", "note": "requests without client_id are not windowed"}}
```
`status`: `ok · not_found · denied · rejected · error`.

### `POST /api/classify`
Same body as `execute`; returns only the routing decision (nothing is executed).

## Telemetry

### `GET /api/telemetry?limit=100&type=request,security_alert&since_id=`
`{"snapshot": {...}, "events": [{"id", "ts", "type", "source", "data"}]}` (newest first). Snapshot
fields: `total_requests, errors, error_rate, throughput_10s, throughput_60s, latency_ms{p50,p95,p99,mean,samples},
by_engine, by_operation, shard_load, security, consistency, events_by_type, events_retained,
retention_max_events, metric_sources`.

### `POST /api/telemetry/reset`
Clears counters and retained events.

## Sharding

### `GET /api/shards`
`health[]` (`id, host, port, active, vnodes, up, ping_ms, keys`) and `topology` (`epoch, active,
spare, vnodes, ownership, migrating, request_load_since_rebalance, ring_tokens[], last_migration, history`).

### `GET /api/shards/locate?key=product:42`
`{"key", "key_hash", "ring_position", "token_hash", "token_position", "node", "port"}`.

### `POST /api/shards/rebalance`
Body: `{"action": "add_shard" | "remove_shard" | "rebalance_by_load" | "reset" | "retry", "shard_id": "shard-5", "alpha": 0.5, "min_requests": 200}`.
Returns the migration report:
```json
{"action": "add shard-5", "status": "completed", "to_move": 1295, "moved": 1295, "verified": 1295,
 "failed": 0, "failed_keys": [], "kept_newer_destination": 0, "already_present": 0,
 "keys_before": {"shard-1": 1470, "...": 0}, "keys_after": {"...": 0}, "total_before": 6051, "total_after": 6051,
 "count_reconciled": true, "remapped_pct": 21.4, "moves_by_route": {"shard-1->shard-5": 285},
 "sample_checks": [{"key": "user:755", "from": "shard-1", "to": "shard-5", "bytes": 312, "match": true}],
 "elapsed_s": 0.18, "log": ["scanned shard-5", "..."]}
```
`rebalance_by_load` returns `{observed_load, imbalance_before, cv_before, vnodes_before, vnodes_after, migration}`.
Errors: 400 (`no spare shard`, `not active`, too few observed requests), 503 (target shard unreachable).

## Security

| Method & path | Body / query | Result |
|---|---|---|
| `GET /api/security/metrics` | — | mode, window, batch, queue_length, partial_windows, stats, model, batch_scoring_ms, `synthetic_evaluation` (IF vs rules metrics), feature_help, static_rules |
| `GET /api/security/events?limit=100&only=anomaly` | — | `events[]`: id, client_id, features, anomaly_score, threshold, verdict, rule_verdict, rule_reasons, explanation, synthetic_label, triage, batch_size, queue_delay_ms |
| `POST /api/security/generate-traffic` | `{"normal_windows": 6, "attack_windows": 2, "attack_kinds": ["injection","exfiltration","bruteforce"], "subtle_fraction": 0.3, "seed": null}` | executes the windows through the pipeline; returns requests, statuses_by_kind, windows, elapsed_s, queue_length. 409 if not seeded |
| `POST /api/security/evaluate` | — | scores everything queued now; returns metrics + `flushed_windows`, `flush_ms` |
| `POST /api/security/train` | `{"contamination": 0.02, "n_train": 2000}` | retrains and saves the model |
| `POST /api/security/settings` | `{"mode": "async|batched|sync|off", "threshold_override": 0.62, "clear_threshold": false}` | new monitor state |
| `POST /api/security/events/{id}/triage` | `{"decision": "confirmed|dismissed|unreviewed"}` | updated event |
| `POST /api/security/clear` | — | clears history, queue and partial windows |

## Consistency

| Method & path | Body | Result |
|---|---|---|
| `GET /api/consistency` | — | live controller state incl. per-profile predictions |
| `POST /api/consistency/network` | `{"congestion": 6}` | re-selects the profile for the new estimate; publishes `consistency_profile` |
| `POST /api/consistency/simulate` | `{"phases": [1,1,3,6], "ops_per_phase": 4000, "mc_samples": 2000, "slo": 0.01, "seed": 2024, "include_oracle": true, "network": {"regions": ["A","B","C"], "writer_rtt_ms": [2,30,110], "reader_rtt_ms": [30,2,80], "jitter_sigma": 0.25, "read_delay_mean_ms": 15}}` | `strategies{name: write_ms, read_ms, mean_ms, p95_ms, stale_pct, phases_over_slo, profile_counts}`, `phases[]`, `config`, `elapsed_s`, `evidence: "simulation"` |

Validation: 1–64 phases with multipliers 0.1–20; 1–9 regions; positive RTTs.

## Experiments and export

| Method & path | Notes |
|---|---|
| `POST /api/benchmarks/run` | `{"experiment": "e1|e2|e3|e4|all", "profile": "quick|default|large", "overrides": {}}` → job record (`id`, `status`) |
| `GET /api/jobs`, `GET /api/jobs/{id}` | job status, progress log, result (`run_id`, `status`, `graphs`, `failures`) |
| `GET /api/experiments?experiment=e1` | registry of runs on disk |
| `GET /api/experiments/report-runs` | run ids used by the generated IEEE report (`reports/data/runs_used.json`); `{"runs": {}}` if not built |
| `GET /api/experiments/{run_id}` | full `results.json` + file list |
| `GET /api/experiments/{run_id}/files/{name}` | one CSV/JSON/PNG of the run (path traversal rejected) |
| `GET /api/experiments/{run_id}/download` | ZIP of the run directory |
| `GET /api/export/telemetry?limit=5000` | JSON download of snapshot + events |
| `GET /api/export/security-events` | CSV download of scored windows |
