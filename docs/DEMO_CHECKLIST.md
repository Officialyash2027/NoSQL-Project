# Demo checklist (verify before recording)

## Environment
- [ ] Docker Desktop running; `docker compose --profile full up -d` done; `docker ps` shows 7 hanf containers
- [ ] `scripts\start.ps1 -Full` running in its own terminal, no errors
- [ ] `python scripts\healthcheck.py` → overall ok; Redis, MongoDB, Cassandra *connected*, verified round trip
- [ ] `python scripts\reset_demo.py` → 4 active shards, 6,000 records seeded

## Functional checks (≈ 3 minutes)
- [ ] Dashboard health strip all green; *dataset seeded*; *security model: ready*
- [ ] Playground: KV read → redis + shard; Document write → mongodb; Wide-column read → cassandra
- [ ] Playground: Injection attempt → `rejected`
- [ ] Security: Generate mixed traffic → Evaluate now → IF and rule metrics shown
- [ ] Shards: Add shard & rebalance → completed, 0 failed, count reconciled
- [ ] Shards: **Reset to initial shards** again afterwards (so the recording starts clean)
- [ ] Consistency: Apply estimate 6 → profile table; Run static vs adaptive → charts
- [ ] Benchmarks: latest E1–E4 default graphs visible
- [ ] Experiment history: run details open; ZIP downloads
- [ ] `python scripts\reset_demo.py` once more

## Evidence to show
- [ ] `python -m pytest -q` output ready in a terminal (92 passed)
- [ ] `python scripts\smoke_test.py` output ready (15/15)
- [ ] `graphs/` folder and `docs/EXPERIMENTS_AND_RESULTS.md`
- [ ] `reports/IEEE_FINAL_REPORT.pdf` opens

## Documentation
- [ ] README, HOW_TO_RUN, USER_MANUAL, DEMO_SCRIPT match the UI you are about to show
- [ ] KNOWN_LIMITATIONS read by every presenter (simulation / synthetic caveats)

## Screen recording
- [ ] Browser zoom 90 %, window ≥ 1366 px wide, notifications off
- [ ] Microphone checked; terminal font size large enough
- [ ] Recording length target 12–15 min; video uploaded; link copied into the paper's cover table

## Final submission folder
- [ ] Source code (without `.venv`, `results/*` optional) zipped or on Drive with access for faculty
- [ ] `reports/IEEE_FINAL_REPORT.docx` and `.pdf` with demo link and Drive link filled in
- [ ] Genuine plagiarism report (< 7 %) from the institution's checker
- [ ] Demo video
- [ ] User manual (`docs/USER_MANUAL.md`, or export to PDF)
