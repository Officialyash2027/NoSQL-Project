# How to run HANF — step by step

Every command below is run from the **project root** (the folder that contains `docker-compose.yml`,
`backend/`, `frontend/`) unless stated otherwise. Windows PowerShell commands are given first;
Linux/macOS equivalents follow. Output shown under *Example output* was captured on the development
machine (Windows 11, Docker Desktop); your timings and key counts per shard will differ.

## 0. Prerequisites

| Tool | Version used | Needed for |
|---|---|---|
| Python | 3.11 (3.11.1 tested) | backend, experiments, tests |
| Docker Desktop (or Docker Engine + Compose v2) | 28.5 | Redis ×5, optional MongoDB/Cassandra |
| Free ports | 7001–7005, 8000 (+27017, 9042 for the full profile) | |
| RAM | ≥ 4 GB free for the full profile (Cassandra uses ~1 GB) | |
| Microsoft Word (optional) | — | regenerating the IEEE PDF only |

Check: `py -3.11 --version` and `docker info` must both succeed.

## 1. One-time setup

```powershell
cd "D:\sem 7\NoSQL\Project NoSQL\NoSQL Project"          # your project folder
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Dev
```
Linux/macOS: `bash scripts/setup.sh --dev`

This creates `.venv`, installs `requirements-dev.txt` (runtime + pytest + python-docx) and copies
`.env.example` to `.env`. Expected last line: `Setup complete. Next: scripts\start.ps1`.

## 2. Start the services

### 2a. Primary demo (Redis shards only) — recommended
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```
### 2b. All three engines (adds MongoDB and Cassandra)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Full
```
Cassandra takes 1–2 minutes to boot and ~25 s to create its table the first time. If the dashboard
shows Cassandra as `unavailable`, wait and press **Engines & routing → Reconnect / re-check**.

### 2c. No Docker available
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Emulated
```
Redis is replaced by in-process fakeredis servers. The UI, `/health` and experiment metadata all say
`emulated`; **do not report emulated numbers as Redis measurements.**

Linux/macOS: `bash scripts/start.sh`, `bash scripts/start.sh --full`, `bash scripts/start.sh --emulated`.

*Example output (2b):*
```
Redis shards healthy: 5/5
Dashboard : http://127.0.0.1:8000/
API docs  : http://127.0.0.1:8000/docs
Health    : http://127.0.0.1:8000/health
INFO:     Application startup complete.
```
The server runs in the foreground of this terminal. Open a **second terminal** for the next steps.

Manual alternative (same thing, without the script):
```powershell
docker compose up -d                      # or: docker compose --profile full up -d
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

## 3. Service URLs

| URL | What |
|---|---|
| http://127.0.0.1:8000/ | Dashboard |
| http://127.0.0.1:8000/docs | Swagger API documentation |
| http://127.0.0.1:8000/health | Health JSON |

## 4. Check health
```powershell
.\.venv\Scripts\python scripts\healthcheck.py
```
*Example output (full profile):*
```
overall: ok   redis mode: live   dataset seeded: False
  Redis      connected    7.0.15     verified round trip  4/4 active shards reachable
  MongoDB    connected    7.0.45     verified round trip  mongodb://127.0.0.1:27017 db=hanf
  Cassandra  connected    4.1.12     verified round trip  127.0.0.1:9042 keyspace=hanf (RF=1)
  shard-1 :7001  up  active  keys=0
  ...
  shard-5 :7005  up  spare   keys=0
```
Exit code 0 = healthy. With the Redis-only profile MongoDB and Cassandra show `unavailable` — that is
expected and the router falls back to Redis (flagged in each response).

## 5. Seed the dataset
```powershell
.\.venv\Scripts\python scripts\seed.py --reset
```
or the **Seed dataset** button on the dashboard. Loads 6,000 Redis records, 50 login users, and
(when connected) 50 MongoDB documents and 100 Cassandra rows.

## 6. Run the end-to-end smoke test
```powershell
.\.venv\Scripts\python scripts\smoke_test.py
```
15 steps covering health, seeding, routing to each engine, telemetry, synthetic traffic, security
evaluation, shard expansion with value verification, consistency simulation, a background benchmark
job and export. It restores the demo state at the end. Expected final line: `15/15 steps passed`.
Results: `results/smoke/<run_id>/results.json`.

## 7. Run the experiments (CLI — recommended for report numbers)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_experiments.ps1 -Experiment e4 -Profile quick
powershell -ExecutionPolicy Bypass -File scripts\run_experiments.ps1 -Experiment all -Profile default
```
Linux/macOS: `bash scripts/run_experiments.sh all default`

| Profile | Purpose | Approx. time on the dev laptop |
|---|---|---|
| `quick` | check that everything runs | E1 ≈ 35 s, E2 ≈ 5 s, E3 ≈ 10 s, E4 ≈ 3 s |
| `default` | Review II protocol sizes (used in the report) | E1 ≈ 8–10 min, others ≤ 2 min each |
| `large` | longer runs for stronger hardware | tens of minutes |

Override any value: `-Set "repetitions=5"` (PowerShell) or `--set repetitions=5`.
For cleaner E1 timings, stop the API server and the optional containers first
(`docker stop hanf-mongo hanf-cassandra`). Output: `results/<exp>/<run_id>/` and `graphs/`.

The same experiments can be started from the **Benchmarks** page (background job).

## 8. Run the automated tests
```powershell
.\.venv\Scripts\python -m pytest -v
```
Unit/API tests need nothing running. Tests marked `live` use the Redis containers (logical DBs 14/15,
never the demo data) and are skipped automatically if Redis is down; MongoDB/Cassandra tests skip if
those engines are not running. See [TESTING.md](TESTING.md).

Optional UI check (headless browser; `pip install playwright` + `python -m playwright install chromium`):
`.\.venv\Scripts\python scripts\ui_check.py`.

## 9. Export results
* Dashboard → **Experiment history** → select a run → **Download ZIP**
* `GET /api/experiments/<run_id>/download`, `GET /api/export/telemetry`, `GET /api/export/security-events`
* Files on disk: `results/<exp>/<run_id>/*.csv|json|png`, latest graphs in `graphs/`.

## 10. Reset the demo state (between rehearsals)
```powershell
.\.venv\Scripts\python scripts\reset_demo.py
```
Shards back to four, dataset re-seeded, security history and telemetry cleared, controller at 1×.

## 11. Shut down
1. Press **Ctrl+C** in the server terminal.
2. `powershell -ExecutionPolicy Bypass -File scripts\stop.ps1` (stops containers; add `-Remove` to delete them).
   Linux/macOS: `bash scripts/stop.sh [--remove]`.

## 12. Recovery

| Symptom | Fix |
|---|---|
| `Docker is not running` | Start Docker Desktop, wait until it says *Running*, retry. Or use `-Emulated`. |
| `port is already allocated` (7001–7005/8000) | Another Redis/HANF instance is running: `docker ps`, stop it, or change `HANF_REDIS_PORTS` / `-Port`. |
| `/health` = degraded | `docker compose ps`; restart with `docker compose up -d`. |
| Cassandra `OperationTimedOut` | It is still booting. Wait 1–2 min, then Reconnect on the Engines page. |
| Topology shows 5 shards after restart | Intentional (persisted in `data/runtime/topology_live.json`). Use **Reset to initial shards** or delete that file while the server is stopped. |
| "dataset not seeded" (409) when generating traffic | Seed first (step 5). |
| Security model "loading" for long | First start trains the model (~5–20 s on a cold disk). |
| Very slow first start | SciPy/scikit-learn import from a cold disk can take 10–20 s; it happens in the background. |
| Corrupted model file | Delete `data/models/security_iforest.*`; it is retrained on the next start. |
