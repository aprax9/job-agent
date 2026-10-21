# Job agent: operating contract

This folder applies to jobs on behalf of one person, the user, with nobody watching. The daily entry point is `/apply-run`. Because runs are unattended, the rules below are hard limits.

## Hard rules

1. **Never invent anything.** Facts about the user come only from the resume being uploaded (`profile/`) and `config/rules.toml`. No new numbers, titles, employers, or answers.
2. **Unknown fact = stop, don't guess.** If a required field asks for a fact that isn't in `rules.toml`, or its answer there is blank, don't fill it with a best guess and don't submit. Run `python3 scripts/tracker.py needs-input <job_id> --question "<exact question and options>"` and move to the next job. The user answers these in `rules.toml` and the job is retried on the next run.
3. **Free text: `free_text_max_sentences` max** (2 by default), drawn from the resume and the job description. A hook rejects longer answers.
4. **Respect language limits.** Never apply to a role that requires a language in `[targeting].blocked_languages` above beginner level, and never state a higher level than the `*_level` answers in `rules.toml`.
5. **No cover letters.** A job that requires a cover-letter file is recorded as blocked.
6. **Resumes are read-only originals.** Never edit, regenerate, convert or reconstruct a resume PDF. Upload the file exactly as it is; tracker.py and the upload hook refuse any file that doesn't match its fingerprint in `config/resume.lock`.
7. **Resume routing is not your decision.** `tracker.py start <job_id>` prints the only resume you may upload for that job and the answers for its region. A hook blocks every other file.
8. **Submitted means proven.** A job counts as submitted only when `tracker.py submitted <job_id> --proof <screenshot>` succeeds. Never edit `data/tracker.csv` directly.
9. **Don't fight blockers.** CAPTCHA, login wall, forced account creation, video interview, or assessment: `tracker.py blocked <job_id> --reason "..."`. No workarounds.
10. **Respect the switches** in `rules.toml`: `enabled`, `dry_run`, `max_submissions_per_run`, `accept_standard_attestations`.
11. **One browser, one job at a time.** Never run two applicant subagents in parallel.

## Layout

- `config/rules.toml`: targeting, salary floors, standard and per-region answers (the user edits this; you can't). Created from `config/rules.example.toml`.
- `config/boards.toml`: seed companies that are always polled
- `config/resume.lock`: resume fingerprints, written by `scripts/lock_resumes.py`
- `profile/`: the user's resume PDFs, at the paths each `[regions.*].resume` names
- `scripts/universe.py`: builds `data/companies.json` (every company on 9 no-login job sites) from Common Crawl
- `scripts/ats.py`: the job-site adapters (listings, details, pay parsing)
- `scripts/discover.py`: polls companies, filters postings in code, writes `data/queue.json`, `data/to_score.json` and a census
- `scripts/tracker.py`: the only way to change application status
- `scripts/lock_resumes.py`: the user runs this themselves; you're not allowed to
- `scripts/report.py`: writes `data/report.html`, a filterable ledger of every job
- `scripts/doctor.py`: setup check
- `data/`: company list, company verdicts, queue, shortlist, tracker, proof screenshots, run summaries
- A file named `STOP` in this folder halts all applying.
