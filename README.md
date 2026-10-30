# HANF — Hybrid Adaptive NoSQL Framework

**Project:** *A Comparative Study of Performance, Consistency, and Security in NoSQL Databases for Big
Data Applications, with a Proposed Hybrid Adaptive Framework* — BCSE406L NoSQL Databases, VIT Chennai.

HANF is a middleware decision layer between client applications and NoSQL engines. For every request
it decides **which engine** (Redis, MongoDB or Cassandra), **which shard** (consistent hashing over five
Redis instances), **which consistency level** (ONE / QUORUM / ALL chosen under a staleness objective)
and **whether the client's behaviour is anomalous** (Isolation Forest over request windows). All four
modules share one telemetry bus. A dashboard, a REST API, four reproducible experiments (E1–E4), an
automated test suite and an IEEE-style report are included.

| Module | What it does | Evidence |
|---|---|---|
| Workload-aware Polyglot Router | Explainable weighted-rule classifier; routes to Redis / MongoDB / Cassandra adapters; flagged fallback | live engines |
| Adaptive Sharding Manager | Consistent hashing with virtual nodes; verified, idempotent live key migration; load-based re-weighting | live Redis + algorithmic |
| ML Security Monitor | Six window features, Isolation Forest vs static rules, async/batched scoring, explanations, triage | synthetic traffic |
| Adaptive Consistency Controller | Monte-Carlo latency/staleness prediction for 7 (W,R) profiles; SLO-constrained selection | **simulation** |
| Shared Telemetry Bus | Timestamped events, subscribers, derived dashboard metrics | live |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for diagrams and algorithms and
[docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) for what is simulated or synthetic.

---

## Quick start (Windows PowerShell)

Prerequisites: **Python 3.11**, **Docker Desktop** (running), ports 7001–7005 and 8000 free.

```powershell
git clone https://github.com/Officialyash2027/NoSQL-Project.git
cd NoSQL-Project
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Dev      # once: .venv + dependencies + .env
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Full     # Redis x5 + MongoDB + Cassandra + API/UI
```
Open **http://127.0.0.1:8000/** (dashboard) — API docs at **http://127.0.0.1:8000/docs**.
In a second terminal:
```powershell
.\.venv\Scripts\python scripts\healthcheck.py      # engine status
.\.venv\Scripts\python scripts\seed.py --reset     # deterministic demo dataset
.\.venv\Scripts\python scripts\smoke_test.py       # 15-step end-to-end check
```
Without `-Full` only the five Redis shards start (the primary demo; MongoDB/Cassandra requests fall back
to Redis and say so). Without Docker: `scripts\start.ps1 -Emulated` (in-process emulated Redis,
labelled as such everywhere).

### Linux / macOS
```bash
git clone https://github.com/Officialyash2027/NoSQL-Project.git && cd NoSQL-Project
bash scripts/setup.sh --dev
bash scripts/start.sh --full          # or: bash scripts/start.sh  |  bash scripts/start.sh --emulated
.venv/bin/python scripts/healthcheck.py
```

### Manual start (any OS)
```bash
docker compose up -d                                   # add --profile full for MongoDB + Cassandra
.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --port 8000   # .venv/bin/python on Linux/macOS
```

## Configuration
Copy `.env.example` to `.env` (setup does this). Important variables: `HANF_REDIS_PORTS`,
`HANF_INITIAL_SHARDS`, `HANF_VNODES`, `HANF_REDIS_MODE` (`live`/`emulated`), `HANF_MONGO_ENABLED`,
`HANF_CASSANDRA_ENABLED`, `HANF_ROUTER_FALLBACK_TO_KV`, `HANF_SECURITY_MODE` (`async`/`batched`/`sync`/`off`),
`HANF_SECURITY_CONTAMINATION`, `HANF_STALENESS_SLO`. Routing rules: `configs/routing.yaml`.
Experiment sizes: `configs/experiments.yaml` (`quick`, `default`, `large`).

## Experiments
```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_experiments.ps1 -Experiment all -Profile quick
powershell -ExecutionPolicy Bypass -File scripts\run_experiments.ps1 -Experiment e1 -Profile default
```
| ID | Experiment | Evidence type |
|---|---|---|
| E1 | Throughput/latency, YCSB-style workloads A/B/C, direct Redis vs HANF (+ sync / batched / async ML scoring) | live Redis |
| E2 | Remapping (modulo vs consistent hashing), adaptive rebalancing, live 4→5 shard migration | algorithmic + live Redis |
| E3 | Isolation Forest vs static rules with train/validation/test splits, evasion sweep, scoring latency | synthetic data |
| E4 | Static vs adaptive consistency over a 16-phase congestion timeline + sensitivity sweeps | simulation |

Outputs: `results/<exp>/<run_id>/` (config, environment metadata, JSON, CSV, PNG) and `graphs/`.
Results and their interpretation: [docs/EXPERIMENTS_AND_RESULTS.md](docs/EXPERIMENTS_AND_RESULTS.md).

## Tests
```powershell
.\.venv\Scripts\python -m pytest -v
```
Unit + API tests run anywhere (fakeredis); `live` tests use the Redis containers on separate logical
DBs and skip automatically when unavailable; MongoDB/Cassandra tests skip when those engines are down.
Details and the latest results: [docs/TESTING.md](docs/TESTING.md).

## Exporting results
Dashboard → *Experiment history* → *Download ZIP*; or `GET /api/experiments/{run_id}/download`,
`GET /api/export/telemetry`, `GET /api/export/security-events`.

## Stop / reset
* Stop the server: **Ctrl+C**. Stop containers: `scripts\stop.ps1` (`-Remove` to delete them).
* Reset the demo (4 shards, re-seed, clear security/telemetry): `python scripts\reset_demo.py`.

## Troubleshooting
| Problem | Fix |
|---|---|
| `Docker is not running` | Start Docker Desktop; or `scripts\start.ps1 -Emulated` |
| Port already allocated | `docker ps` → stop the other container, or change `HANF_REDIS_PORTS` / `-Port` |
| Cassandra `unavailable` / `OperationTimedOut` | It boots in 1–2 min; Engines page → *Reconnect* |
| 409 "dataset not seeded" | `python scripts\seed.py --reset` or *Seed dataset* on the dashboard |
| Five shards after restart | Topology is persisted; *Shards → Reset to initial shards* |
| Slow first start | SciPy/scikit-learn import from a cold disk; model loads in the background |
| `ExecutionPolicy` error | Use `powershell -ExecutionPolicy Bypass -File ...` as shown |
More in [docs/HOW_TO_RUN.md](docs/HOW_TO_RUN.md#12-recovery).

## Repository layout
```
backend/app/        FastAPI app: api/, core/ (pipeline, jobs), router/, sharding/, security/,
                    consistency/, telemetry/, adapters/, experiments/, models/ (schemas)
backend/tests/      pytest suite (unit, API, experiments, live integration)
frontend/           dashboard (index.html, app.js, styles.css, vendor/chart.umd.min.js)
scripts/            setup/start/stop, healthcheck, seed, smoke test, reset, experiments, UI check
configs/            routing.yaml, experiments.yaml
data/               models/ (security model + checksum), runtime/ (topology, logs) — generated
results/, graphs/   experiment outputs — generated
docs/               all documentation (see below)
reports/            report builder (build_report.py), figures, plagiarism-report instructions
                    (the generated IEEE_FINAL_REPORT.docx/.pdf and the Documents/ review reports are
                    kept out of the public repository because they contain personal data)
```

## Documentation
[HOW_TO_RUN](docs/HOW_TO_RUN.md) · [USER_MANUAL](docs/USER_MANUAL.md) · [ARCHITECTURE](docs/ARCHITECTURE.md) ·
[API_REFERENCE](docs/API_REFERENCE.md) · [TESTING](docs/TESTING.md) ·
[EXPERIMENTS_AND_RESULTS](docs/EXPERIMENTS_AND_RESULTS.md) · [DEMO_SCRIPT](docs/DEMO_SCRIPT.md) ·
[DEMO_CHECKLIST](docs/DEMO_CHECKLIST.md) · [KNOWN_LIMITATIONS](docs/KNOWN_LIMITATIONS.md) ·
[AUDIT](docs/AUDIT.md) · [REFERENCES_VERIFICATION](docs/REFERENCES_VERIFICATION.md) ·
[PROJECT_COMPLETION](PROJECT_COMPLETION.md) · [Pending](Pending.md) ·
[FINAL_SUBMISSION_CHECKLIST](FINAL_SUBMISSION_CHECKLIST.md)

## Authors
Rohit Kumar, Yash Krishna Dixit, Dev Pradhan — Slot D1,
faculty: Manjula V. (Names and register numbers as given in the Review II report.)
