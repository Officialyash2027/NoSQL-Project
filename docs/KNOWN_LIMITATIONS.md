# Known limitations

This page states what the implementation does **not** show, so that no result is read as more than
it is. It applies to the code, the dashboard, the experiment outputs and the IEEE paper.

## 1. What is simulated
* **Adaptive consistency (E4, Consistency page)** is a Monte-Carlo model of a three-region,
  three-replica quorum store. RTTs (2/30/110 and 30/2/80 ms), log-normal jitter (σ = 0.25), the
  Exp(15 ms) read-after-write delay and the congestion phases are *assumptions* taken from Review II.
  No multi-region database was deployed. The ONE/ONE staleness figure depends strongly on the read
  delay (see the sensitivity sweep).
* The adaptive controller's two winning profiles (ONE/QUORUM, QUORUM/ONE) have W + R = N, so their
  freshness is *not* guaranteed by quorum intersection; it holds in this topology only because the
  replica nearest the writer lies in the reader's read set. With other replica placements the
  controller would predict (and choose) differently.
* The controller reacts with a one-phase lag; the *oracle* variant is an upper bound only.
* In the live request path the chosen level is **applied** only by the Cassandra adapter, and the demo
  Cassandra is a single node (RF = 1), so every level succeeds trivially. For Redis and MongoDB the
  level is recorded as **advisory**: the five Redis instances are independent primaries without
  replicas, and MongoDB runs without a replica set.

## 2. Synthetic data
* **Security evaluation (E3) and the live security demo use synthetic traffic** from a seeded
  generator. Normal behaviour, the three attack families and their "subtle" variants are designed by
  us; detection quality on real NoSQL logs is unknown. No real labelled NoSQL attack dataset was used.
* Request timestamps of synthetic traffic are *simulated client timestamps* (so request-rate features
  are realistic without waiting); real interactive requests use server time.
* The evasion sweep shows a real weakness: slow brute force (few failed logins at a normal request
  rate) and very low-intensity exfiltration are largely missed.
* The Isolation Forest explanation (features with the largest |z|) is a deviation summary, not a
  causal attribution of the model's decision.
* Anomaly verdicts are not confirmed attacks; only analyst triage can mark an event as confirmed.

## 3. Optional engines
* MongoDB 7 and Cassandra 4.1 adapters are implemented and were tested live on the development machine
  (round-trip and integration tests passed). They run as **single nodes**; no sharded MongoDB cluster
  or multi-node Cassandra ring was deployed.
* The router's fallback stores documents/wide rows in Redis when their engine is down; those
  fallback writes are not copied back when the engine returns.
* The adaptive sharding manager manages **Redis** key placement only; MongoDB/Cassandra keep their
  own placement.

## 4. Hardware and measurement
* All live measurements come from one Windows 11 laptop (8 logical CPUs, ~8 GB RAM) with Redis in
  Docker Desktop (WSL 2 VM). Docker Desktop's virtualised network adds roughly 0.3–0.7 ms per round
  trip and multi-millisecond tail spikes, so **absolute** E1 throughput is far below what Redis
  achieves natively; compare configurations **relative** to each other.
* E1 uses one client thread and a Python re-implementation of the YCSB core workloads, not the
  official Java YCSB client. Results include Python interpreter overhead in both the direct and the
  HANF paths.
* Run-to-run variability on a shared laptop is substantial. E1 (default profile) was executed three
  times; two runs were disturbed by background desktop load (worst direct-Redis coefficient of variation
  0.50 and 0.38, versus 0.12 for the first). The report uses the most recent run with CV < 0.20 — a rule
  fixed before the third run was inspected — and `docs/EXPERIMENTS_AND_RESULTS.md` lists all three.
* Because of that variability, **the throughput ranking of the synchronous, batched and asynchronous
  scoring modes is not claimed**: it changed between runs. What held in every run: synchronous scoring
  has the highest p99 latency in every workload, and the HANF median latency exceeds direct access.
* The report's E1 numbers were produced from the CLI with the API server and the optional containers
  stopped; runs started from the dashboard are noisier.
* Single-window Isolation Forest scoring latency depends on the scikit-learn version and CPU.

## 5. Scope not covered
* No authentication/authorisation or TLS on the HANF API (local demo only).
* The telemetry bus is in-process; multiple HANF instances would need an external bus.
* Rebalancing by vnode weights cannot split a single extremely hot key (E2 with Zipf 0.99 shows the
  floor); that needs replication or caching of hot keys.
* No graph-database adapter (mentioned in Review I as a possible engine type).

## 6. Things that must not be claimed
* "Production-ready", "secure against real attacks", or "validated on a geo-distributed cluster".
* Historical Review II numbers as outputs of this implementation (they are shown separately and
  labelled as historical).
* Any plagiarism/similarity score — it must come from the institution's checker.
