# Faculty demonstration script (12–15 minutes)

Roles are suggested for three presenters (Rohit, Yash, Dev); adjust as you like. Times are targets.
*Expected* results below were observed on the development machine; exact counts and timings vary.

## Before the session (10 minutes earlier)
```powershell
cd "D:\sem 7\NoSQL\Project NoSQL\NoSQL Project"
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Full        # terminal 1, leave running
# wait ~2 min for Cassandra, then in terminal 2:
.\.venv\Scripts\python scripts\healthcheck.py                         # all three engines "connected"
.\.venv\Scripts\python scripts\reset_demo.py                          # clean state, 4 shards, seeded
```
Open http://127.0.0.1:8000/ in a browser (zoom 90 %), and a second tab with
http://127.0.0.1:8000/docs. Run the full test suite once beforehand so you can show its output
(`.\.venv\Scripts\python -m pytest -q`, ≈ 2–3 min).

---

## 1. Opening (1 min) — *speaker 1*
"Our project compares NoSQL engines on performance, consistency and security, and found in Review I
that existing studies look at these one at a time and use static configurations. HANF is a middleware
layer that makes four decisions per request — engine, shard, consistency level and anomaly check —
and shares its signals on one telemetry bus. Review II had a 61 % prototype on Redis only; today we
show the completed system with Redis, MongoDB and Cassandra, a dashboard, a REST API, 92 automated
tests and re-run experiments."

## 2. System health (1 min) — *speaker 1*
* **Dashboard**: point to the health strip — Redis 7.0.15, MongoDB 7.0, Cassandra 4.1 all
  *connected*; explain that "connected" is shown only after a real write/read/delete round trip.
* **Engines & routing** page: version, verified round trip, capabilities; routing rules come from
  `configs/routing.yaml`.

## 3. Request routing (2.5 min) — *speaker 2*
**Request playground**:
1. Preset **KV read** → Execute. *Expect*: status ok, engine redis, classification key-value (rule:
   key prefix `product`), shard e.g. shard-1 with its ring position, latency ~1–3 ms, consistency level
   marked *advisory* for Redis.
2. Preset **Document write** → Execute. *Expect*: document class (prefix `order` + nested object),
   engine mongodb.
3. Preset **Wide-column read** → Execute. *Expect*: wide-column, engine cassandra, rows returned,
   consistency level *applied to the CQL statement*.
4. Preset **Injection attempt** → Execute. *Expect*: status **rejected** — the `$ne` operator never
   reaches MongoDB.
"The classifier is a transparent weighted-rule scorer, not an AI model — each fired rule is listed."

## 4. Telemetry (0.5 min) — *speaker 2*
Back to **Dashboard**: requests counted per engine, latency percentiles, the newest events in the
table. Hover an ⓘ to show where the metric comes from.

## 5. Security monitor (2.5 min) — *speaker 3*
**Security monitor** page — read the banner (label vs verdict vs triage).
1. Keep defaults (8 normal windows, 3 per attack type, 30 % subtle) → **Generate mixed traffic**.
   *Expect*: 1,700 requests executed in ~1–2 s; brute-force logins mostly `denied`, injection reads
   `not_found`.
2. **Evaluate now**. *Expect* (example run): Isolation Forest recall 100 %, FPR 0 %; static rules
   recall ~60 % — they miss the subtle variants.
3. In **Scored windows** point at an anomaly: the most-deviating features (e.g. failure_ratio z ≈ +50
   for brute force). Press **confirm** on one to show triage.
"Scoring runs asynchronously in batches. In E1 we measured why: inline per-window scoring costs a lot
of throughput."

## 6. Adaptive sharding (2 min) — *speaker 1*
**Shards** page: four active shards, ring, keys per shard.
1. **Add shard & rebalance**. *Expect*: status completed; ~21 % of keys moved (ideal 20 %), all
   verified with byte-identical DUMP, 0 failed, *count reconciled* 6,0xx → 6,0xx; ~0.2 s.
2. **Locate a key** `product:42` → shows the owner and the ring position.
3. Go to the **Request playground**, read `product:42` again → still ok (now possibly on another shard).
"Modulo hashing would move ~80 % of keys; consistent hashing moves only the keys that must move."

## 7. Adaptive consistency (2 min) — *speaker 2*
**Consistency** page — point to the *Simulation* banner.
1. Slide congestion to **6**, **Apply estimate**: ONE/ONE fails the 1 % SLO (≈ 99 % stale), the
   controller chooses QUORUM/ONE or ONE/QUORUM (≈ 93 ms) instead of QUORUM/QUORUM (≈ 186 ms).
2. **Run static vs adaptive** (defaults). *Expect*: adaptive ≈ 39 ms mean with 0 % stale vs
   QUORUM ≈ 75 ms; ONE/ONE ≈ 2 ms but ≈ 73 % stale reads.
"These numbers are from a Monte-Carlo model with assumed latencies — we did not have a multi-region
cluster. The level is applied to Cassandra statements in the live path."

## 8. Benchmarks and results (2 min) — *speaker 3*
**Benchmarks** page: start **E4 quick** to show a background job with live progress, then show the
graphs of the latest default runs (E1–E4) and the clearly labelled *historical* Review II table.
Mention the key results from `docs/EXPERIMENTS_AND_RESULTS.md`: synchronous ML scoring always had
the worst p99 latency (batch it or run it off the request path); consistent hashing moved ~19 % vs ~80 %
for modulo; IF F1 ≈ 0.98 vs 0.83 for rules (synthetic data); adaptive consistency ≈ 49 % faster than
QUORUM with no stale reads (simulation). The cards are labelled *used in IEEE report*.
**Experiment history**: open a run → environment metadata, CSV files, **Download ZIP**.

## 9. Tests and documents (1 min) — *speaker 1*
Show the terminal with `pytest -q` output (92 passed) and `scripts\smoke_test.py` (15/15). Show
`docs/` (manual, architecture, API reference, limitations) and `reports/IEEE_FINAL_REPORT.pdf`.

## 10. Close (0.5 min)
"All four modules are integrated and demonstrated on live engines; consistency is simulated and the
security data is synthetic — both stated in the report. Next steps are a real multi-region Cassandra
deployment and a real labelled query log."

---

## Fallbacks during the demo
| Problem | What to do / say |
|---|---|
| Cassandra or MongoDB shows unavailable | Press Reconnect; if still down, run requests anyway — the response shows **fallback** to Redis, which is itself a feature to explain. |
| Docker problem | `scripts\start.ps1 -Emulated`: everything except live Redis numbers works; say that the run is emulated (the UI says so too). |
| "dataset not seeded" | Dashboard → Seed dataset. |
| Add shard says no spare shard | Shards → Reset to initial shards, then add again. |
| UI unresponsive | Use Swagger at `/docs` for the same calls. |

## Likely questions and accurate answers
* **Is the classifier machine learning?** No. It is a weighted rule scorer with configurable rules;
  every decision is explained. A learned classifier would need labelled query logs we don't have.
* **Are the consistency results real?** They come from a simulation calibrated with Review II's
  assumed latencies. The controller is connected to the live path and its level is applied to
  Cassandra statements, but our Cassandra is a single node.
* **Why did ONE/QUORUM give 0 % staleness when W + R = N?** Because the replica that acknowledges a
  ONE write (the writer's local replica) is always among the reader's two closest replicas in this
  layout. The controller finds that by simulation; a different placement would change the choice.
* **How do you know migration lost no data?** Each moved key is verified byte-for-byte (DUMP) at the
  destination before the source copy is deleted; the report reconciles total key counts; tests force
  a mid-migration failure and check that nothing is lost and retry completes.
* **Is the security model accurate on real attacks?** Unknown — it is evaluated on synthetic traffic.
  We report the evasion sweep, which shows weaknesses (slow brute force).
* **Why are your E1 throughputs lower than Review II?** Different machine and Docker Desktop on Windows
  adds ~0.3–0.7 ms per round trip; we compare configurations relative to direct Redis on the same
  machine.
* **Why does E1 list three runs?** Desktop background load made two runs very noisy. We fixed a rule in
  advance (use the newest run whose direct-Redis variation is below 20 %) and publish all three runs; we
  only claim findings that held in every run.
* **How is the contamination chosen?** On a separate validation split; the test split is used only for
  reporting (Review II had tuned it on the test set).
* **What happens if a shard dies during migration?** The batch is marked failed, keys stay on the source
  and remain readable via the previous ring; *Retry migration* completes it.
