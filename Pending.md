# Pending work register

Only genuinely unfinished items are listed. "Blocks" says whether the item blocks the demo video or the
final submission.

## A. Human actions required for submission

| # | Pending task | Reason | Priority | Dependencies | Recommended next action | Blocks demo | Blocks submission |
|---|---|---|---|---|---|---|---|
| A1 | Record the demonstration video | Must be recorded by the students | High | Services running; `docs/DEMO_CHECKLIST.md` | Follow `docs/DEMO_SCRIPT.md` (12–15 min) | — | **Yes** |
| A2 | Insert the demo-video link and Google Drive link into the paper | Links do not exist yet | High | A1, A4 | Replace the two `[to be added: …]` placeholders in `reports/IEEE_FINAL_REPORT.docx` (cover table + Appendix), or edit `PLACEHOLDER_*` in `reports/build_report.py` and rebuild; re-export PDF | No | **Yes** |
| A3 | Obtain the genuine plagiarism report (< 7 %) | Must come from the institution's checker; no score was computed here | High | Final PDF (A2, A5) | `reports/PLAGIARISM_REPORT_INSTRUCTIONS.md` | No | **Yes** |
| A4 | Create the public Google Drive folder (code, video, paper, manual, plagiarism report) | Needs the students' account | High | A1–A3 | `FINAL_SUBMISSION_CHECKLIST.md` | No | **Yes** |
| A5 | Confirm reference [3] (Maamari & Nasar, 2025) | Could not be found by web search; venue/DOI unknown | Medium | Access to the source the students used | Add full venue/DOI, or replace with a verified ML-security reference; see `docs/REFERENCES_VERIFICATION.md` | No | Recommended |
| A6 | Check the paper against the official IEEE conference template if the faculty requires the exact .docx template | The official template file was not in the project folder | Medium | Template from the faculty/IEEE site | Paste the sections into the official template or confirm the current IEEE-style layout is acceptable | No | Possibly |
| A7 | Proof-read the paper; confirm names/register numbers (taken from the Review II report, e.g. "Yash Krishnadixit") | Authors must approve | Medium | — | Edit `AUTHORS` in `reports/build_report.py` if needed and rebuild | No | Recommended |

## B. Technical work deliberately left as future work (documented as limitations)

| # | Pending task | Reason | Priority | Reproduction / starting point | Blocks |
|---|---|---|---|---|---|
| B1 | Validate the consistency controller on a real multi-node, multi-region Cassandra cluster | Needs several hosts/regions; not possible on one laptop | Medium | `backend/app/consistency/simulator.py`; Cassandra adapter already applies the chosen level | No |
| B2 | Evaluate the security monitor on a real labelled NoSQL query log and against adaptive attackers | No such dataset available | Medium | `backend/app/experiments/e3_security.py` (swap the generator for a log reader) | No |
| B3 | Detect slow brute force (evasion sweep shows ≤ 23 % recall at 30 % failed logins) | Window-level features dilute per-account failures | Medium | Add a per-account failed-login counter in `security/monitor.py` | No |
| B4 | Hot-key mitigation (Zipf 0.99: one key ≈ 9 % of traffic cannot be rebalanced by vnodes) | Needs key replication or a cache tier | Low | `sharding/manager.py` | No |
| B5 | Repeat E1 with the official YCSB client on dedicated Linux hardware | Laptop + Docker Desktop network dominates absolute numbers | Low | `configs/experiments.yaml` `large` profile | No |
| B6 | Authentication/TLS for the HANF API | Out of scope for a local demo | Low | FastAPI dependencies | No |
| B7 | Put the project under git and re-run experiments so metadata records a commit hash | The folder is not a git repository (`git_commit` is null in metadata) | Low | `git init && git add . && git commit` | No |
