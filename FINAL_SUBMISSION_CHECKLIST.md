# Final submission checklist

Items marked [x] were completed and verified in this repository. Unchecked items need **human action**.

## Code and documentation
- [x] Final source code: `backend/`, `frontend/`, `scripts/`, `configs/`, `docker-compose.yml`, requirements files
- [x] README.md (setup, run, test, troubleshooting)
- [x] User manual — `docs/USER_MANUAL.md` (with screenshots in `docs/screenshots/`)
- [x] Run guide, architecture, API reference, testing, experiments, limitations, demo script/checklist in `docs/`
- [x] Experiment outputs used by the paper — `reports/data/<run_id>/`
- [ ] Optional: export `docs/USER_MANUAL.md` to PDF for the Drive folder (e.g. open in VS Code → Markdown PDF, or print from a Markdown viewer)
- [ ] Remove `.venv/` and other generated folders before zipping/uploading the code (`.gitignore` lists them)

## Paper
- [x] IEEE-style report generated from the actual results — `reports/IEEE_FINAL_REPORT.docx` (editable) and `.pdf`
- [ ] Fill in the **demo video link** (cover table and Appendix) — replace `[to be added: demo video link]`
- [ ] Fill in the **Google Drive link** (Appendix) — replace `[to be added: public Google Drive folder link]`
- [ ] Confirm or replace reference [3] (Maamari & Nasar) — marked *[venue to be confirmed]*; see `docs/REFERENCES_VERIFICATION.md`
- [ ] Check the paper against the **official IEEE conference template** if the faculty requires the exact template (the official .docx was not in the project folder; the report follows IEEE conference style: two columns, Times New Roman 10 pt, roman-numbered sections, IEEE references)
- [ ] Re-export the PDF after edits (Word → Save As → PDF, or `python reports\build_report.py`)
- [ ] Each author proof-reads the paper; verify names and register numbers on the cover table

## Plagiarism report
- [ ] Submit the final PDF to the institution's approved checker (see `reports/PLAGIARISM_REPORT_INSTRUCTIONS.md`)
- [ ] Confirm similarity **< 7 %**; save the genuine report as `reports/PLAGIARISM_REPORT.pdf`

## Demo video
- [ ] Rehearse with `docs/DEMO_SCRIPT.md`; go through `docs/DEMO_CHECKLIST.md` right before recording
- [ ] Record the 12–15 minute demonstration and upload it
- [ ] Make the video link viewable by the faculty

## Google Drive folder (publicly accessible to the evaluator)
- [ ] Source code (zip or folder) with access
- [ ] `IEEE_FINAL_REPORT.pdf` and `.docx`
- [ ] Plagiarism report
- [ ] Demo video
- [ ] User manual
- [ ] Sharing set to "Anyone with the link can view" (or as instructed by the faculty); link tested in a private browser window

## Consistency check before submitting
- [ ] Video, paper and code describe the same version (paper numbers = `reports/data/runs_used.json` runs; UI in video = current `frontend/`)
- [ ] If any experiment is re-run, rebuild the paper (`python reports\build_report.py`) so tables and figures match
- [ ] Final review completed by all three authors
