# HANF — Pre-Implementation Audit

*Written before any code was created (2026-10-18). It records what was found in the
project folder, what the reports require, and the plan the implementation follows.*

## 1. Files found

| File | Type | Notes |
|---|---|---|
| `Documents/NoSQL_DA1_Report.pdf` | Review I report (8 pages) | Abstract, problem statement, objectives, 10-paper literature table, gaps, proposed HANF design, 12 references |
| `Documents/NoSQL_DA2_Review2_Report.pdf` | Review II report (12 pages) | Architecture (Fig. 1), module status (Table 1), setup (Table 2), experiments E1–E4 with protocols, results and caveats, progress table, limitations, 11 references |
| `Documents/Template report 3.pdf` | DA-3 implementation-report template (1 page) | Cover table (course, slot D1, reg. no., name, title, demo link) and 9 required sections; 6–8 pages, ≥1,500 words, "use the guideline of IEEE template" |

No source code, notebooks, data, or earlier implementation exist in the folder. The project is
therefore built from scratch. The `Documents/` folder is treated as read-only.

The official IEEE conference template (`.docx`/`.dotx`) is **not** present. The template PDF only
says to follow IEEE guidelines.

## 2. Environment found

| Item | Found |
|---|---|
| OS | Windows 11 Home, 8 logical CPUs, ~8 GB RAM |
| Python | 3.11.1 (`py -3.11`) |
| Node.js | 22.20.0 / npm 10.9.3 |
| Docker | 28.5.1 (Docker Desktop, was stopped; started during the audit) |
| Native Redis | Not installed |
| Microsoft Word | Installed (usable for DOCX → PDF export through COM) |
| LibreOffice | Not installed |
| Internet | Available (PyPI and Docker Hub reachable) |

## 3. Requirements extracted

### Review I (DA-1)
- Problem: selection/configuration decisions use benchmarks that look at performance, consistency
  or security in isolation, although the three are coupled.
- Objectives O1–O5: literature review (2024+); gap identification; trade-off analysis across
  NoSQL engines; propose a hybrid design (polyglot persistence + adaptive consistency + ML anomaly
  detection + adaptive sharding); outline future work incl. prototype and validation.
- Gaps: single-dimension evaluation; limited environments; static configuration; review-heavy
  contributions; few security studies.
- HANF design: four layers sharing a telemetry bus; staged evaluation plan (reproduce YCSB baselines,
  implement HANF over MongoDB/Cassandra/Redis, compare against static baselines).

### Review II (DA-2)
- Architecture: client → Polyglot Router → {Consistency Controller, Security Monitor, Sharding
  Manager} ↔ shared telemetry bus → engines (Redis implemented; MongoDB and Cassandra planned).
- Request flow (5 steps): classify/select engine → hash ring → pick consistency profile from
  telemetry estimate → execute while the monitor accumulates per-client windows → publish latency,
  key-load and anomaly signals.
- Historical self-assessed status: Sharding 90 %, Security 70 %, Consistency 60 %,
  Telemetry 50 %, Router 35 %, overall 61 %.
- Setup: 1 vCPU VM, Redis 7.0.15 ×5 on ports 7001–7005 (no persistence), Python 3.12,
  scikit-learn, Matplotlib; Python re-implementation of YCSB A/B/C with Zipfian 0.99.
- **E1**: 50,000 records × 100 B, 20,000 ops × 3 runs; direct Redis vs. HANF routing (4 shards)
  vs. + synchronous IF scoring per 100-request window vs. + batched scoring (20 windows/call);
  throughput mean ± SD, p50, p99.
- **E2**: modulo vs. consistent hashing (16/64/128/256 vnodes), 100,000 keys, 4→5 and 10→11 nodes,
  remap % and imbalance; adaptive rebalancing (20,000 keys, 8 nodes × 128 vnodes, 200,000 Zipf-0.7
  requests, 6 rounds, rule `v_i ← v_i·(1 − 0.5·(load_i/mean − 1))`, floor 16); live migration of
  50,000 keys 4→5 Redis shards with sample verification.
- **E3**: Isolation Forest (100 trees) on 6 window features (rate, key diversity, mean payload,
  failure ratio, injection ratio, scan ratio), z-score scaling; 2,000 training windows; test set
  3,000 normal (5 % benign look-alikes) + 200 each of injection/exfiltration/brute force, 30 % subtle;
  static rules (inj > 0.2, fail > 0.5, scan > 0.5, rate > 150/s); contamination 0.01/0.02/0.05;
  precision, recall, F1, FPR, per-attack recall, scoring latency.
- **E4**: 3 regions, N = 3, writer at A (RTT 2/30/110 ms), reader at B (30/2/80 ms), log-normal
  jitter σ = 0.25, read issued Exp(15 ms) after write ack, congestion multiplier on remote links,
  16 phases (1,1,1,3,3,6,6,3,1,1,1,6,1,1,3,1) × 4,000 ops, 7 (W,R) profiles, 4,000 Monte-Carlo
  samples, SLO stale ≤ 1 %, fallback ALL/ALL, one-phase lag.
- Caveats the report itself states (must be preserved): single-core VM; Redis only; synthetic
  security data; contamination chosen on the test set; simulated consistency; Python YCSB clone.
- Remaining work listed for Review III: MongoDB/Cassandra adapters and query classifier; live
  consistency validation; async ML scoring, held-out split, real dataset, evasive attacks; official
  YCSB on multi-core hardware.

### DA-3 template
- Sections: Abstract; Literature review and problem statement; Proposed system architecture;
  Proposed system and algorithm implementation; Results and discussion; Graphs/plots/tables;
  Conclusion; References; Appendix (drive link with code, demo video, user manual).
- 6–8 pages, ≥1,500 words. Template says < 12 % similarity; the final-submission announcement
  (per the user) requires < 7 %, which is the target used.

## 4. Reference issues spotted in the earlier reports
- Review I lists the Redis/Aerospike/Dragonfly paper author as "D. **Bodra**"; Review II lists
  "D. **Borda**". Must be checked against arXiv:2510.08863.
- Maamari & Nasar (2025) has no venue, volume, or DOI in either report.
- Several Review I entries have no authors (IEEE retail paper, IAEME IJCC, IJFMR, WJAETS, arXiv
  sharding review).
- These will be verified where possible; unverifiable details will be flagged, not invented.

## 5. Current implementation status
There is no code. The percentages in Review II are historical self-assessments of a prototype
that is not in this folder. Everything below is built new.

## 6. Major gaps to close
1. Polyglot Router: no workload classifier, no MongoDB/Cassandra adapters, no health/capability API.
2. Telemetry bus: only "in-process collection" was described; no event model, retention, or
   subscribers.
3. Security monitor: shadow-mode only; contamination was tuned on the test set; no async path;
   no event history or explanations.
4. Consistency controller: simulation only and not connected to the request path.
5. No API, no UI, no tests, no reproducible experiment runner, no export, no docs.

## 7. Implementation plan
- **Stack**: Python 3.11, FastAPI + Uvicorn, redis-py, pymongo, cassandra-driver, NumPy, pandas,
  scikit-learn, Matplotlib, pytest. Frontend: a dependency-free HTML/CSS/JS single-page app served by
  the backend (Chart.js vendored locally) — chosen over React/Vite so the demo is one process with no
  build step and works offline.
- **Infrastructure**: `docker-compose.yml` with five `redis:7.0.15-alpine` instances on
  7001–7005 (matching Review II); MongoDB 7 and Cassandra 4.1 under an optional `full` profile.
  Fallback without Docker: an explicitly labelled in-process emulation mode (fakeredis) so the UI and
  simulations still run, never reported as live Redis.
- **Request pipeline**: validate → classify (transparent weighted rules) → choose adapter → hash
  ring → consistency profile (applied for Cassandra, advisory for Redis) → execute → feed security
  window → publish telemetry.
- **Experiments**: executable E1–E4 scripts writing JSON/CSV + PNG graphs under
  `results/<experiment>/<run_id>/`, with environment metadata; quick/default/large configs.
  Improvements over Review II: held-out validation split for contamination selection (E3),
  sensitivity sweep on the read delay (E4), keys-moved cost per rebalancing round (E2).
- **Docs and report**: README, HOW_TO_RUN, USER_MANUAL, ARCHITECTURE, API_REFERENCE, TESTING,
  EXPERIMENTS_AND_RESULTS, DEMO_SCRIPT, DEMO_CHECKLIST, KNOWN_LIMITATIONS, PROJECT_COMPLETION,
  Pending, FINAL_SUBMISSION_CHECKLIST, IEEE-style DOCX + PDF generated from real results.

## 8. Acceptance criteria
- `docker compose up -d` + one start command brings up the API and UI; `/health` reports each
  engine truthfully.
- A request submitted in the UI shows classification, engine, shard, consistency profile, real
  result and measured latency.
- 4→5 shard migration on live Redis reports moved/failed keys, verified values and a reconciled
  total.
- Security monitor trains, scores live synthetic traffic in batches, and reports precision/recall/
  F1/FPR on a held-out test set.
- Consistency simulation runs from UI and CLI and produces graphs.
- E1–E4 produce machine-readable results and graphs from actual runs.
- pytest suite runs and passes (integration tests skip only when a service is genuinely absent).
- Documentation and the paper quote only numbers present in the generated result files.
