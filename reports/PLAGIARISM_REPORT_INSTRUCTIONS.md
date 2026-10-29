# Obtaining the genuine plagiarism (similarity) report

The final-submission announcement requires a similarity score **below 7 %** (stricter than the
template's 12 %; the 7 % target applies). No similarity score has been computed for this paper and
none is claimed anywhere in the project. The report must come from a checker approved by VIT.

## Steps
1. **Finish the paper first.** Fill the placeholders in `reports/IEEE_FINAL_REPORT.docx`
   (demo-video link, Google Drive link) and resolve the reference marked *[venue to be confirmed]*
   (see `docs/REFERENCES_VERIFICATION.md`). Re-export the PDF (Word: *File → Save As → PDF*, or
   `python reports/build_report.py`, which regenerates both files).
2. **Use the approved checker.** VIT normally provides Turnitin through the course page / VTOP or the
   central library (iThenticate/Turnitin access). Ask the course faculty (Manjula V) which tool and
   submission point to use if it is not listed on the course page.
3. **Submit the final PDF** exactly as it will be submitted (same text, same references).
   Typical settings: exclude the bibliography and quoted material only if the faculty permits it —
   do not change settings to lower the score without permission.
4. **Download the similarity report** (the full report PDF, not only the percentage) and save it as
   `reports/PLAGIARISM_REPORT.pdf`.
5. **Check the score.**
   * < 7 %: done — include the report in the submission folder.
   * ≥ 7 %: open the report, look at the highlighted passages and their sources. Rewrite those
     passages genuinely in your own words *with correct citations*; add quotation marks + citation for
     any wording that must stay (e.g. a standard definition). Do **not** use paraphrasing tools,
     character substitution, images of text, or other tricks to hide matching text — that is academic
     misconduct and most checkers detect it.
6. Re-check after revisions and keep the final report.

## Where matches are most likely
* Reference list entries (normally excluded or ignored by faculty).
* Standard phrases about CAP, YCSB, consistent hashing and Isolation Forest — keep these short and cite.
* Text reused from your own Review I/II reports may be flagged as self-similarity if those were
  submitted to the same checker; ask the faculty whether self-plagiarism from earlier reviews of the
  same project is excluded.

Add the final report to the submission folder listed in `FINAL_SUBMISSION_CHECKLIST.md`.
