# Testing

## 1. Test plan

| Level | What | Where | Needs |
|---|---|---|---|
| Unit | Hash ring, modulo baseline, rebalancing rule, balance metrics | `test_hashring.py` (10) | nothing |
| Unit | Live migration logic: values preserved, idempotent re-run, remove/reset, forced destination failure + retry, newer destination write kept, load re-weighting, topology persistence | `test_sharding_manager.py` (8) | nothing (fakeredis) |
| Unit | Router classification (9 cases), determinism, hints, engine selection, fallback, no-fallback errors, unsupported operations | `test_router.py` (15) | nothing |
| Unit | Window features, injection signal (7 cases), generator reproducibility, dataset values, IF vs normal/attacks, static rules, metric formulas, explanations, model save/load + tamper detection, monitor in sync/batched/async modes, triage, unwindowed requests | `test_security.py` (19) | nothing |
| Unit | Quorum counts, zero staleness for overlapping quorums, ONE/ONE staleness, latency ordering, W-th-smallest latency without jitter, SLO-constrained selection, fallback, controller events, deterministic timeline, model validation | `test_consistency.py` (10) | nothing |
| Unit | Pub/sub, derived metrics, retention, filters, subscriber isolation, thread safety, reset | `test_telemetry.py` (6) | nothing |
| Unit | Adapter recovery after transient failures, disabled adapters, operator field-name rejection | `test_adapters.py` (4) | nothing |
| API / E2E | Health, status, adapters, seed + execute (read/write/delete/login/scan), fallback, validation errors (422), shard expansion + reset, synthetic traffic + evaluation + triage + CSV export, consistency endpoints, background benchmark job + run files + ZIP + path-traversal rejection, dashboard served | `test_api.py` (10) | nothing (full app on fakeredis) |
| Experiments | All profiles load, Zipf sampler, E2/E3/E4 end-to-end on small configs | `test_experiments.py` (5) | nothing |
| Integration (live) | Seed/read/write through the real pipeline on Redis, live 4→5 migration with value checks, tiny live E1, MongoDB adapter round trip incl. operator rejection, Cassandra adapter with ONE/QUORUM/ALL | `test_integration_live.py` (5) | Redis containers (DB 14/15); MongoDB/Cassandra for the last two |
| System | 15-step HTTP smoke test of the demonstration flow | `scripts/smoke_test.py` | running server |
| UI | Headless Chromium clicks the main action on every page, fails on JS errors/error toasts, checks phone-width overflow | `scripts/ui_check.py` | running server + Playwright |

Isolation rules: unit/API tests use in-process fakeredis shards and temporary directories
(`HANF_STATE_DIR`, `HANF_OUTPUT_DIR`), so they never touch the demo data, the saved model or the
results folder. Live tests use Redis logical DBs 14 and 15 and flush them afterwards. Tests that need an
unavailable service skip automatically with a reason.

## 2. Commands

```powershell
.\.venv\Scripts\python -m pytest -v                         # everything
.\.venv\Scripts\python -m pytest -v -m "not live"           # no external services needed
.\.venv\Scripts\python -m pytest -v backend\tests\test_integration_live.py
.\.venv\Scripts\python scripts\smoke_test.py                # server must be running
.\.venv\Scripts\python scripts\ui_check.py                  # server + Playwright
```

## 3. Results of the final run (2026-10-18)

Environment: Windows 11 (build 26300), Python 3.11.1, Redis 7.0.15 ×5, MongoDB 7.0.45 and Cassandra
4.1.12 in Docker Desktop, all running.

```
.\.venv\Scripts\python -m pytest -v -p no:cacheprovider --junitxml=data/runtime/pytest_report.xml
================== 92 passed, 1 warning in 73.37s (0:01:13) ===================
```

| File | Passed | Skipped | Failed |
|---|---|---|---|
| test_adapters.py | 4 | 0 | 0 |
| test_api.py | 10 | 0 | 0 |
| test_consistency.py | 10 | 0 | 0 |
| test_experiments.py | 5 | 0 | 0 |
| test_hashring.py | 10 | 0 | 0 |
| test_integration_live.py | 5 | 0 | 0 |
| test_router.py | 15 | 0 | 0 |
| test_security.py | 19 | 0 | 0 |
| test_sharding_manager.py | 8 | 0 | 0 |
| test_telemetry.py | 6 | 0 | 0 |
| **Total** | **92** | **0** | **0** |

The one warning is a third-party deprecation notice (Starlette's TestClient recommending `httpx2`); it
does not affect the tests. Full output: `data/runtime/pytest_output.txt`; JUnit XML:
`data/runtime/pytest_report.xml` (generated files, not committed).

**Smoke test** (`scripts/smoke_test.py`, server started with `scripts\start.ps1 -Full`): **15/15 steps
passed**, results in `results/smoke/<run_id>/results.json`. In one of the final runs Cassandra was still
warming up and timed out; step 7 then passed through the router's *flagged fallback to Redis*, which
exposed an adapter bug (see §5).

**UI check** (`scripts/ui_check.py`): passed — no JavaScript errors, no error toasts, all actions on the
eight pages completed, no horizontal overflow at 390 px width. Screenshots in `docs/screenshots/`.

**Clean start:** `scripts\start.ps1 -Full` → `scripts\healthcheck.py` reported all three engines
`connected` with verified round trips (exit code 0).

## 4. Reproducibility checks
* Fixed seeds everywhere (traffic generator, Isolation Forest, Zipf samplers, Monte-Carlo); the
  consistency timeline test asserts identical results for identical seeds.
* `test_generator_is_reproducible_and_labelled` asserts identical synthetic windows for the same seed.
* Every experiment run stores its configuration and environment (`config.json`, `metadata.json`).

## 5. Defects found by testing and fixed
| Found by | Defect | Fix |
|---|---|---|
| E1 quick run | Every routed request pinged all five shards and counted keys (router availability check) — ~10 network round trips per request | Availability cached for 2 s and reduced to one PING (`redis_adapter.py`) |
| Server start | Telemetry keyword `source` collided with the event's own `source` argument → startup crash | Renamed the field to `trigger` |
| Server start | Cassandra table creation took ~25 s on first start and hit the 5 s request timeout | 60 s timeout for DDL |
| E2 quick run | `remapped_pct` divided by keys *scanned* (moved keys were scanned twice) | Scan the new shard first; divide by keys before migration |
| Smoke test | Step 12 compared against seed values although step 9 legitimately rewrote some keys | Snapshot values immediately before migration |
| Smoke test | One transient Cassandra timeout left the adapter `unavailable` forever | Deep health checks re-test failed engines (`test_adapters.py` covers it) |
| Report review | `platform.release()` reports "10" on Windows 11 | Detect build ≥ 22000 as Windows 11 |
| Startup time | scikit-learn/SciPy import took 10–20 s on a cold disk, delaying the API | Lazy imports; security model loads in a background thread |

## 6. Not covered / known gaps
* No load or concurrency test with many simultaneous clients (E1 uses one client thread).
* No browser test of the dark theme beyond rendering (manual check only).
* Security detection is tested on synthetic traffic only.
* No test against a multi-node Cassandra or a MongoDB replica set.
