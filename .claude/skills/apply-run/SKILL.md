---
name: apply-run
description: Daily unattended job-application run. Refreshes the company list weekly, discovers matching roles across 9 job sites, scores them, applies to the shortlist one at a time with proof, and writes a run summary. Invoke as /apply-run.
---

# Daily application run

Follow CLAUDE.md's hard rules throughout. Don't ask questions: nobody is watching. If something
isn't covered, take the conservative option (skip, block, or needs-input) and keep going.

## 0. Pre-flight
- If a file named `STOP` exists in this folder, or `enabled = false` in `config/rules.toml`, print
  "Switched off" and end the run.
- Run `date`. If local time is after 20:00 (a late catch-up run), do steps 1-3 and 6 only.

## 1. Company list (weekly)
If `data/companies.json` is missing, or `data/state.json` has no `universe_refreshed` within the last
7 days, run `python3 scripts/universe.py` (it can take 10-40 minutes). Then run each web search
listed in `[discovery].web_searches` in `config/rules.toml` and pass every job-site URL in the
results to `python3 scripts/discover.py add-company <url> ...`. If the list is empty, skip this.

## 2. Discover
Run `python3 scripts/discover.py run`. It prints a census table; keep it for the summary.

## 3. Score
If `data/to_score.json` is not empty, delegate to the **job-scorer** subagent, then run
`python3 scripts/tracker.py record-scores data/shortlist.json`.

## 4. Apply
Loop:
1. Run `python3 scripts/tracker.py next`.
2. If it prints a job_id, delegate to the **applicant** subagent with that job_id and wait for it
   to finish. Never run two applicants at once.
3. If it prints NONE, CAP or STOP, stop looping.
If an applicant reports `failed` for a reason that looks transient (timeout, page didn't load),
run the loop once more at the end; otherwise move on.

## 5. Confirmations (optional)
If Gmail tools are available, search the last 3 days for application confirmation emails. For each
that clearly matches a `submitted` row in `data/tracker.csv`, run
`python3 scripts/tracker.py email-confirmed <job_id>`.

## 6. Summary
Run `python3 scripts/tracker.py summary` and end by printing it plus the census table from step 2.
One line on anything that needs the user's attention (new questions, a site that blocked repeatedly).
