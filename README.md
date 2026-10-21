# job-agent

An unattended job-application agent built on [Claude Code](https://claude.com/claude-code). Every
day it finds roles that match your titles and cities on nine public job sites, filters them in
Python, has Claude score what's left against your resume, and fills in the application forms in a
real Chrome window, one job at a time, saving a screenshot as proof of each submission.

It only uses sites that don't need an account: Ashby, Greenhouse, Lever, BambooHR, Breezy,
Workable, Recruitee, SmartRecruiters and Rippling. No LinkedIn and no job-board logins.

```
weekly   universe.py      Common Crawl + web search -> every company on the 9 sites (data/companies.json)
daily    discover.py      poll companies -> filter in code -> queue + census
         job-scorer       Claude rates fit and judges new companies (tech or not), once each
         tracker.py next  picks the next shortlisted job (region order, then score, caps enforced)
         applicant        fills the form in Chrome, uploads your locked resume, proves submission
         summary          data/runs/<date>.md
```

## How it stays honest

An agent that submits forms under your name while you sleep needs limits it can't talk its way
around. Most of this repo is those limits.

- **Code filters, Claude judges.** Titles, cities, posting age, talent-pool postings, language
  requirements, posted pay below your floor, excluded companies, duplicates and per-company caps
  are all decided in Python before Claude sees a posting. Claude only makes the calls a regex
  can't, such as whether a "product operations" role is really an engineering job.
- **It never guesses a fact about you.** Every answer comes from `config/rules.toml` or your
  resume. A blank answer means unknown: the job parks as `needs_input`, the exact question goes to
  `data/questions_pending.md`, and the job retries after you answer it.
- **Your resume can't be altered.** `lock_resumes.py` fingerprints each PDF (SHA-256) and makes it
  read-only. A `PreToolUse` hook blocks any browser upload that isn't the exact file routed to that
  job, byte for byte.
- **Free-text answers are capped.** A second hook rejects any typed answer longer than
  `free_text_max_sentences` (2 by default). No cover letters; a job that requires one is marked
  blocked for you to handle.
- **Submitted means proven.** Only `tracker.py` can change an application's status, and it won't
  record a submission without a screenshot that was taken after the attempt started.
- **Blockers are respected.** CAPTCHA, forced account creation, assessments and video interviews
  are recorded as blocked. The agent doesn't try to get around them.
- **Permissions are locked down** in `.claude/settings.json`: the agent can't edit your config,
  your resumes, the tracker or its own hooks, can't run the lock script, and can read email but
  never send it.
- **Kill switches.** A file named `STOP`, `enabled = false`, `dry_run = true`, a daily cap and a
  per-company cap (2 applications per 60 days by default).

## Requirements

- macOS or Linux (the tracker uses Unix file locks)
- Claude Code, either the desktop app's Code tab or the `claude` CLI, on a plan that includes it
- Python 3.9 or newer. No `pip install`; everything is standard library plus a vendored TOML parser
- Node.js 18 or newer, for the Playwright browser server (`npx @playwright/mcp`)
- Google Chrome

## Setup

### 1. Clone the repo into `~/job-agent`

```bash
git clone https://github.com/<your-username>/job-agent.git ~/job-agent
```

```bash
cd ~/job-agent
```

`.mcp.json` points the browser profile and screenshot folder at `~/job-agent`. If you clone
somewhere else, change both paths in that file.

### 2. Create your config

```bash
cp config/rules.example.toml config/rules.toml
```

`config/rules.toml` is gitignored, so nothing you put in it gets committed. Open it and work
down the file:

1. **`[regions.*]`**: one table per city you're targeting, with its keywords, currency, salary
   floor and which resume to send. The example has New York, London and Berlin. Rename, add or
   delete them, and keep `region_order` in `[run]` in sync.
2. **`[targeting]`**: the titles to accept and reject, plus plain-English descriptions of the
   seniority, functions and domains you want. The scorer reads these the way a recruiter would
   read a brief.
3. **`[answers]`**: your contact details and the facts application forms ask for. Shared answers
   go in `[answers]`; per-city ones (phone, location, work authorization, sponsorship, expected
   salary) go in `[answers.<region>]`.

Leave an answer blank (`""`) if you don't know it yet. The agent will ask instead of guessing.

### 3. Add your resume

Put your resume PDF at `profile/resume.pdf`. That's the path every region in the example config
uses. To send different versions to different cities, see [profile/README.md](profile/README.md).

### 4. Lock the resume

Run this yourself, in your own terminal:

```bash
python3 scripts/lock_resumes.py
```

It prints `locked profile/resume.pdf` and its fingerprint. The agent isn't allowed to run this
script, which is the point.

### 5. Check the setup

```bash
python3 scripts/doctor.py
```

It checks Python, Node and Chrome, the `.mcp.json` paths, your config, that each resume exists
and is locked, and lists every answer that's still blank. Fix anything marked `FIX` before going
on.

### 6. Build a first company list

```bash
python3 scripts/universe.py --ats ashby,greenhouse,lever --crawls 1
```

This reads the [Common Crawl](https://commoncrawl.org) index for every company board on those
three sites and takes 10 to 20 minutes. It prints progress as it goes. The Common Crawl index
server is sometimes slow; the script waits and retries, and if it's down, rerun it later. The
existing list is kept.

### 7. See what's out there

```bash
python3 scripts/discover.py run
```

It ends with a census table: how many postings survived each filter in each region, and how many
were posted in the last 7 days. That's the realistic number of applications a day.

### 8. Do a dry run

`dry_run = true` is the default, so this fills forms and screenshots them without clicking Submit.

- **Desktop app:** Code tab, open the `job-agent` folder, start a session, type `/apply-run`.
- **CLI:** run `claude` inside `~/job-agent`, then type `/apply-run`.

Approve the Playwright browser and the scripts when asked. A Chrome window opens and the agent
works through the shortlist, up to `max_submissions_per_run` forms.

### 9. Review it

- `data/proof/*-filled.png`: a screenshot of every filled form
- `data/runs/<date>.md`: what happened to each job
- `data/questions_pending.md`: questions the agent couldn't answer from your config

Answer the questions in `config/rules.toml` and those jobs retry on the next run. Repeat the dry
run until the screenshots look like forms you'd submit yourself.

### 10. Go live and schedule it

Set `dry_run = false` in `config/rules.toml`. Then schedule a daily run. In the desktop app: Code
tab, Routines, New routine, Local, folder `job-agent`, instructions `/apply-run`, daily at a time
you choose, permission mode Manual. Click **Run now** once to approve permissions. Turn on Keep
computer awake in the desktop app's settings so the run isn't skipped.

Later, build the full company list (all nine sites, two crawls, 20 to 40 minutes):

```bash
python3 scripts/universe.py
```

After that the daily run refreshes it once a week by itself.

## Day to day

- Read `data/runs/<date>.md`.
- Answer anything new in `data/questions_pending.md`, in `config/rules.toml`.
- Handle `blocked` jobs yourself (CAPTCHA, account creation, assessments, required cover-letter file).
- For a filterable view of every job the agent has touched, run `python3 scripts/report.py` and
  open `data/report.html`.
- If a Gmail connector is available to Claude, the run also matches confirmation emails to
  submitted applications. It has read-only access; sending is denied.

## Controls

| To | Do this |
|---|---|
| Stop everything now | `touch ~/job-agent/STOP`. Delete the file to resume. |
| Practice instead of submitting | `dry_run = true` in `[run]` |
| Change daily volume | `max_submissions_per_run`. Claude's usage limits on your plan set the real ceiling. |
| Limit applications per company | `max_per_company` and `company_cap_days` |
| Apply at any company, not only tech | `tech_only = false` in `[companies]` |
| Overrule a company verdict | edit `"tech"` for that company in `data/company_profiles.json` |
| Never apply somewhere | add it to `exclude_companies` in `[targeting]` |
| Apply to a job the scorer skipped | `python3 scripts/tracker.py shortlist <job_id> --reason "..."` |
| Re-score a skipped job after a rule change | `python3 scripts/tracker.py reopen <job_id> --reason "..."` |
| Never repeat past applications | `python3 scripts/tracker.py seed past.csv` (columns: `company`, `title`, `url`, `status`, `city`, `notes`) |
| Always poll a company | add its board token to `config/boards.toml` |

## Commands

| Command | What it does |
|---|---|
| `python3 scripts/doctor.py` | setup check |
| `python3 scripts/lock_resumes.py [--unlock]` | fingerprint and write-protect resumes (you run this, not the agent) |
| `python3 scripts/universe.py [--ats ...] [--crawls N]` | build or refresh the company list |
| `python3 scripts/discover.py run [--full] [--limit N]` | poll boards, filter, queue, census |
| `python3 scripts/discover.py add-company <url> ...` | add companies from careers URLs |
| `python3 scripts/tracker.py next` | the next job to apply to, or `NONE` / `CAP` / `STOP` |
| `python3 scripts/tracker.py summary` | today's results to `data/runs/<date>.md` |
| `python3 scripts/report.py` | write `data/report.html` |
| `python3 -m unittest discover tests` | run the filter tests |

`tracker.py --help` and `discover.py --help` list the rest.

## Layout

```
CLAUDE.md                     the agent's hard rules
.claude/skills/apply-run/     the daily run (/apply-run)
.claude/agents/               job-scorer and applicant subagents
.claude/hooks/                upload guard, free-text guard, unfinished-job check
.claude/settings.json         permissions and hook wiring
.mcp.json                     Playwright browser server
config/rules.example.toml     template for config/rules.toml (yours, gitignored)
config/boards.toml            seed companies polled every run
profile/                      your resume PDFs (gitignored)
scripts/ats.py                job-site adapters and pay parsing
scripts/universe.py           company list from Common Crawl
scripts/discover.py           polling, filters, scoring batches, census
scripts/tracker.py            application state machine
scripts/report.py             HTML ledger
scripts/doctor.py             setup check
data/                         queue, tracker, screenshots, run summaries (gitignored)
tests/                        unit tests for the filters
```

## Limitations

- Sites with a CAPTCHA, a login wall or an assessment are out of reach by design. Those jobs are
  listed for you to do by hand.
- The language filter is keyword- and stopword-based. It catches "fluent German required" and
  postings written mostly in another language; the scorer catches subtler cases, and it can
  still get one wrong.
- Pay parsing reads only amounts explicitly marked in the region's currency, and ignores hourly
  rates. A posting with no pay listed is never filtered on pay.
- Each form is filled by Claude in a live browser, so your Claude plan's usage limits cap how
  many applications a day are possible.

## Responsible use

Applications go out under your name. Run dry runs until you trust the output, keep the
per-company cap low so no employer gets a pile of applications from you, and follow each job
site's terms of use. The agent only states what's in your resume and your config, so keep both
accurate.

## License

MIT. See [LICENSE](LICENSE). The vendored TOML parser in `scripts/_vendor/tomli` is MIT-licensed
by its author; its license file is included.
