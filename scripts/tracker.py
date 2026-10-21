#!/usr/bin/env python3
"""The only way application status changes. Enforces resume routing, caps, and proof.

  start <job_id>                              begin an attempt; prints resume + paths to use
  submitted <job_id> --proof PNG              confirmation page captured (dry_run must be off)
  dry-run-ok <job_id> --proof PNG             form fully filled, Submit not clicked
  needs-input <job_id> --question TEXT        a required question has no answer in rules.toml
  blocked <job_id> --reason TEXT              CAPTCHA, login wall, account creation, assessment
  failed <job_id> --reason TEXT               tried and it didn't go through
  reopen <job_id> --reason TEXT               a skipped job goes back to the scorer (rule change)
  shortlist <job_id> --reason TEXT            manual override: a skipped/reopened job goes on the apply list
  next                                        print the next shortlisted job to apply to (or NONE / CAP / STOP)
  record-scores data/shortlist.json           store the scorer's decisions (shortlist, skips, company verdicts)
  email-confirmed <job_id>                    a confirmation email arrived
  seed FILE.csv                               import past applications so they're never repeated
  summary                                     today's results -> data/runs/<date>.md
"""
import argparse
import csv
import fcntl
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10 or older (e.g. the Mac's built-in 3.9): use bundled copy
    sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))
    import tomli as tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ats  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TRACKER, QUEUE, CURRENT = DATA / "tracker.csv", DATA / "queue.json", DATA / "current_job.json"
PENDING = DATA / "questions_pending.md"
PROFILES = DATA / "company_profiles.json"
COLUMNS = ["job_id", "company", "title", "region", "url", "status", "score", "attempts", "resume",
           "proof", "email_confirmed", "note", "started_at", "updated_at"]
MIN_PROOF_BYTES = 5_000
STALE_AFTER = timedelta(hours=2)


def now():
    return datetime.now(timezone.utc)


def die(msg):
    print(f"REFUSED: {msg}", file=sys.stderr)
    sys.exit(1)


def rules():
    path = ROOT / "config" / "rules.toml"
    if not path.exists():
        die("config/rules.toml is missing. Copy config/rules.example.toml to config/rules.toml and fill it in.")
    with open(path, "rb") as f:
        return tomllib.load(f)


def safe_id(job_id):
    return "".join(c if c.isalnum() else "-" for c in job_id)


def verify_resume(rel_path):
    """Refuse unless the PDF is byte-identical to what lock_resumes.py fingerprinted."""
    lock_file = ROOT / "config" / "resume.lock"
    if not lock_file.exists():
        die("resumes aren't locked. The user must run `python3 scripts/lock_resumes.py` themselves.")
    expected = json.loads(lock_file.read_text()).get(rel_path)
    actual = hashlib.sha256((ROOT / rel_path).read_bytes()).hexdigest()
    if expected != actual:
        die(f"{rel_path} doesn't match its locked fingerprint. It has been changed; stop the run "
            f"and tell the user. Don't try to fix or regenerate it.")


def check_enabled(cfg):
    if (ROOT / "STOP").exists():
        return "STOP file present"
    if not cfg["run"].get("enabled", True):
        return "enabled = false in rules.toml"
    return None


def board_of(job_id, url=""):
    """'ats:slug' from the job_id, or from the URL for imported rows (seed:...) that don't carry it."""
    parts = job_id.split(":")
    if len(parts) >= 3:
        return f"{parts[0]}:{parts[1].lower()}"
    hit = ats.parse_board_url(url or "")
    return f"{hit[0]}:{hit[1].lower()}" if hit else None


def company_cap_hit(rows, cfg, job_id, company):
    """True when this company already has max_per_company applications in the cap window."""
    cap = cfg["run"].get("max_per_company", 2)
    cutoff = now() - timedelta(days=cfg["run"].get("company_cap_days", 60))
    counted = {"submitted"} | ({"dry_run_ok"} if cfg["run"].get("dry_run", True) else set())
    board = board_of(job_id, rows.get(job_id, {}).get("url", ""))
    key, n = ats.company_key(company), 0
    for r in rows.values():
        if r["status"] not in counted or r["job_id"] == job_id:
            continue
        try:
            if datetime.fromisoformat(r["updated_at"]) < cutoff:
                continue
        except ValueError:
            pass
        same_board = board and board_of(r["job_id"], r.get("url", "")) == board
        if same_board or (key and ats.company_key(r["company"]) == key):
            n += 1
    return n >= cap


def daily_done(rows, cfg):
    done_status = "dry_run_ok" if cfg["run"].get("dry_run", True) else "submitted"
    return sum(1 for r in rows.values() if r["status"] == done_status and local_today(r["updated_at"]))


def region_answers(cfg, region):
    """Shared answers with this region's answers merged on top, plus a computed start date."""
    ans = cfg.get("answers", {})
    merged = {k: v for k, v in ans.items() if not isinstance(v, dict) or k == "yes_no"}
    merged.update(ans.get(region, {}))
    days = int(merged.get("notice_days") or 30)
    merged["earliest_start_date"] = (date.today() + timedelta(days=days)).isoformat()
    unanswered = [k for k, v in merged.items() if v == ""]
    if unanswered:
        merged["_unanswered"] = unanswered  # questions needing these -> needs-input
    return merged


def load_rows():
    if not TRACKER.exists():
        return {}
    with open(TRACKER, newline="") as f:
        return {r["job_id"]: r for r in csv.DictReader(f)}


def save_rows(rows):
    DATA.mkdir(exist_ok=True)
    tmp = TRACKER.with_suffix(".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows.values())
    tmp.replace(TRACKER)


def queue():
    return {j["id"]: j for j in json.loads(QUEUE.read_text())} if QUEUE.exists() else {}


def upsert(rows, job_id, **fields):
    row = rows.get(job_id) or {c: "" for c in COLUMNS} | {"job_id": job_id, "attempts": "0"}
    row.update({k: str(v) for k, v in fields.items()})
    row["updated_at"] = now().isoformat(timespec="seconds")
    rows[job_id] = row
    return row


def local_today(ts):
    try:
        return datetime.fromisoformat(ts).astimezone().date() == date.today()
    except ValueError:
        return False


def current():
    return json.loads(CURRENT.read_text()) if CURRENT.exists() else None


def clear_stale(rows):
    cur = current()
    if cur and now() - datetime.fromisoformat(cur["started_at"]) > STALE_AFTER:
        upsert(rows, cur["job_id"], status="failed", note="stale: previous run ended without an outcome")
        CURRENT.unlink()
        save_rows(rows)

# ---------- commands ----------

def cmd_start(a):
    rows, cfg = load_rows(), rules()
    clear_stale(rows)
    cur = current()
    if cur and cur["job_id"] != a.job_id:
        die(f"{cur['job_id']} is still in progress. Record its outcome first.")
    off = check_enabled(cfg)
    if off:
        die(f"applying is switched off ({off}).")
    job = queue().get(a.job_id)
    if not job:
        if a.job_id in rows:
            upsert(rows, a.job_id, status="failed", note="posting no longer listed")
            save_rows(rows)
        die(f"{a.job_id} is no longer listed (not in data/queue.json); recorded as failed.")
    dry = cfg["run"].get("dry_run", True)
    done_status = "dry_run_ok" if dry else "submitted"
    done_today = daily_done(rows, cfg)
    if done_today >= cfg["run"]["max_submissions_per_run"]:
        die(f"daily cap reached ({done_today} {done_status} today). Stop applying.")
    if company_cap_hit(rows, cfg, a.job_id, job["company"]):
        upsert(rows, a.job_id, status="skipped", note="company cap reached")
        save_rows(rows)
        die(f"{job['company']} already has {cfg['run'].get('max_per_company', 2)} applications in the cap window; skipped.")
    region = cfg["regions"].get(job["region"]) or die(f"region {job['region']} not configured")
    if not region.get("resume"):  # a region with no resume picked yet: ask, don't guess
        a.question = (f"Which resume should {job['region']} jobs use? Set resume in "
                      f"[regions.{job['region']}] of config/rules.toml.")
        cmd_needs_input(a)
        die(f"no resume chosen for {job['region']}; recorded as needs_input.")
    resume = (ROOT / region["resume"]).resolve()
    if not resume.is_file():
        die(f"resume missing: {resume}")
    verify_resume(region["resume"])
    sid = safe_id(a.job_id)
    CURRENT.write_text(json.dumps({
        "job_id": a.job_id, "safe_id": sid, "region": job["region"], "resume": str(resume),
        "started_at": now().isoformat(timespec="seconds"), "dry_run": dry,
        "max_sentences": cfg["run"].get("free_text_max_sentences", 2),
    }, indent=2))
    prev = rows.get(a.job_id, {})
    upsert(rows, a.job_id, company=job["company"], title=job["title"], region=job["region"],
           url=job["url"], status="in_progress", resume=region["resume"],
           attempts=int(prev.get("attempts") or 0) + 1, started_at=now().isoformat(timespec="seconds"))
    save_rows(rows)
    print(json.dumps({
        "job_id": a.job_id, "company": job["company"], "title": job["title"], "region": job["region"],
        "apply_url": job.get("apply_url") or job["url"], "resume_to_upload": str(resume),
        "proof_screenshot_name": f"{sid}-{'filled' if dry else 'confirmation'}.png",
        "dry_run": dry, "accept_standard_attestations": cfg["run"].get("accept_standard_attestations", False),
        "answers": region_answers(cfg, job["region"]),
    }, indent=2, ensure_ascii=False))


def finish(a, status, note="", proof=""):
    rows, cur = load_rows(), current()
    if cur and cur["job_id"] == a.job_id:
        CURRENT.unlink()
    elif a.job_id not in rows:
        job = queue().get(a.job_id, {})
        upsert(rows, a.job_id, company=job.get("company", ""), title=job.get("title", ""),
               region=job.get("region", ""), url=job.get("url", ""))
    upsert(rows, a.job_id, status=status, note=note, proof=proof)
    save_rows(rows)
    print(f"{a.job_id}: {status}" + (f" ({note})" if note else ""))


def check_proof(a, require_live):
    cur = current()
    if not cur or cur["job_id"] != a.job_id:
        die(f"{a.job_id} isn't the job in progress. Run start first.")
    if require_live and cur["dry_run"]:
        die("dry_run is on; this attempt can only be recorded as dry-run-ok.")
    p = Path(a.proof)
    p = p if p.is_absolute() else ROOT / p
    if not p.exists():
        die(f"proof file not found: {p}")
    if p.stat().st_size < MIN_PROOF_BYTES:
        die("proof file is suspiciously small; take a real screenshot of the page.")
    if datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) < datetime.fromisoformat(cur["started_at"]):
        die("proof file predates this attempt.")
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def cmd_submitted(a):
    finish(a, "submitted", proof=check_proof(a, require_live=True))


def cmd_dry_run_ok(a):
    finish(a, "dry_run_ok", proof=check_proof(a, require_live=False))


def cmd_needs_input(a):
    job = load_rows().get(a.job_id) or queue().get(a.job_id, {})
    with open(PENDING, "a") as f:
        f.write(f"- [{date.today()}] {job.get('company', '?')} / {job.get('title', '?')}: {a.question}\n")
    finish(a, "needs_input", note=a.question)


def cmd_reopen(a):
    """Send a skipped job back to the scorer, e.g. after a rule change makes it eligible. Only skips reopen."""
    rows = load_rows()
    r = rows.get(a.job_id) or die(f"{a.job_id} not in tracker")
    if r["status"] != "skipped":
        die(f"{a.job_id} is {r['status']}; only skipped jobs can be reopened.")
    upsert(rows, a.job_id, status="reopened", note=f"reopened: {a.reason} (was: {r['note']})")
    save_rows(rows)
    print(f"{a.job_id}: reopened")


def cmd_shortlist(a):
    """Manual override: put a skipped (or reopened) job straight on the apply list, without re-scoring.
    The job must still be in data/queue.json, or `next` would drop it as no longer listed."""
    rows = load_rows()
    r = rows.get(a.job_id) or die(f"{a.job_id} not in tracker")
    if r["status"] not in ("skipped", "reopened", "needs_input"):
        die(f"{a.job_id} is {r['status']}; only skipped, reopened or needs-input jobs can be shortlisted by override.")
    if a.job_id not in queue():
        die(f"{a.job_id} isn't in data/queue.json (re-poll its board first); not shortlisted.")
    upsert(rows, a.job_id, status="shortlisted", note=f"Manual override: {a.reason} (was: {r['note']})")
    save_rows(rows)
    print(f"{a.job_id}: shortlisted (override)")


def cmd_simple(status):
    return lambda a: finish(a, status, note=a.reason)


def cmd_record_scores(a):
    """Store the scorer's output: shortlist (with score), skips, and company verdicts."""
    data = json.loads(Path(a.file).read_text())
    rows, q, cfg = load_rows(), queue(), rules()

    def base(job_id):
        job = q.get(job_id, {})
        return dict(company=job.get("company", ""), title=job.get("title", ""),
                    region=job.get("region", ""), url=job.get("url", ""))

    for s in data.get("skip", []):
        cur = rows.get(s["job_id"], {})
        if cur.get("status") in ("submitted", "in_progress"):
            continue  # a re-score never overrides an application that's already out
        if (cur.get("status") == "shortlisted" and cur.get("note", "").startswith("Manual override")
                and not s.get("reason", "").lower().startswith("manual")):
            continue  # ...or a job you put on the apply list yourself, unless you're the one skipping it
        upsert(rows, s["job_id"], **base(s["job_id"]), status="skipped", note=s.get("reason", ""))
    held = 0
    for s in data.get("apply", []):
        if rows.get(s["job_id"], {}).get("status") in ("submitted", "in_progress"):
            continue
        b = base(s["job_id"])
        # a region can set a higher bar than [run].min_score (e.g. to deprioritise it)
        floor = cfg["regions"].get(b["region"], {}).get("min_score")
        if floor is not None and float(s.get("score") or 0) < floor:
            upsert(rows, s["job_id"], **b, status="skipped", score=s.get("score", ""),
                   note=f"score {s.get('score')} below {b['region']} min_score {floor}: {s.get('why', '')}")
            held += 1
            continue
        upsert(rows, s["job_id"], **b, status="shortlisted",
               score=s.get("score", ""), note=s.get("why", ""))
    save_rows(rows)
    profiles = json.loads(PROFILES.read_text()) if PROFILES.exists() else {}
    for c in data.get("companies", []):
        if c.get("key") and c.get("tech") in ("yes", "no"):
            profiles[c["key"]] = {**profiles.get(c["key"], {}), "name": c.get("name", ""), "tech": c["tech"],
                                  "sector": c.get("sector", ""), "reason": c.get("reason", ""), "by": "scorer",
                                  "decided": date.today().isoformat()}
    PROFILES.write_text(json.dumps(profiles, indent=1, ensure_ascii=False, sort_keys=True))
    print(f"Recorded {len(data.get('apply', [])) - held} shortlisted, {len(data.get('skip', []))} skipped, "
          f"{held} below their region's min_score, {len(data.get('companies', []))} company verdicts.")


def region_rank(cfg, override=""):
    """Region -> rank. override: comma list of regions, where '*' stands for every region not named."""
    base = cfg["run"]["region_order"]
    if not override:
        return {n: i for i, n in enumerate(base)}
    names = [x.strip() for x in override.split(",") if x.strip()]
    unknown = [n for n in names if n != "*" and n not in base]
    if unknown:
        die(f"--order: unknown region(s) {', '.join(unknown)}. Known: {', '.join(base)}")
    rest = [n for n in base if n not in names]
    seq = [m for n in names for m in (rest if n == "*" else [n])]
    seq += [n for n in rest if n not in seq]
    return {n: i for i, n in enumerate(seq)}


def cmd_next(a):
    """Print the next job to apply to, or NONE / CAP / STOP. Highest region priority, then score."""
    rows, cfg = load_rows(), rules()
    clear_stale(rows)
    off = check_enabled(cfg)
    if off:
        print(f"STOP ({off})")
        return
    if daily_done(rows, cfg) >= cfg["run"]["max_submissions_per_run"]:
        print("CAP (daily limit reached)")
        return
    order = region_rank(cfg, a.order)
    q = queue()
    waiting = [r for r in rows.values() if r["status"] == "shortlisted"]
    waiting.sort(key=lambda r: (order.get(r["region"], 99), -float(r.get("score") or 0)))
    changed = False
    for r in waiting:
        if r["job_id"] not in q:
            upsert(rows, r["job_id"], status="failed", note="posting no longer listed")
            changed = True
            continue
        if company_cap_hit(rows, cfg, r["job_id"], r["company"]):
            upsert(rows, r["job_id"], status="skipped", note="company cap reached")
            changed = True
            continue
        if changed:
            save_rows(rows)
        print(r["job_id"])
        return
    if changed:
        save_rows(rows)
    print("NONE")


def cmd_mark_universe(_a):
    state_file = DATA / "state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    state["universe_refreshed"] = date.today().isoformat()
    state_file.write_text(json.dumps(state, indent=1, sort_keys=True))
    print("Company list refresh recorded.")


def cmd_email_confirmed(a):
    rows = load_rows()
    if a.job_id not in rows:
        die(f"{a.job_id} not in tracker")
    upsert(rows, a.job_id, email_confirmed="yes")
    save_rows(rows)
    print(f"{a.job_id}: email confirmation recorded")


SEED_STATUS = {"submitted": "submitted", "skipped": "skipped"}  # blocked/failed are left to retry


def region_for_city(cfg, city):
    """The configured region whose location_keywords include this city, or ''."""
    city = city.strip().lower()
    for name, r in cfg.get("regions", {}).items():
        if city and (city == name.replace("_", " ") or city in r.get("location_keywords", [])):
            return name
    return ""


def cmd_seed(a):
    rows, kept, left, cfg = load_rows(), 0, [], rules()
    text = Path(a.file).read_text(encoding="utf-8-sig")
    delim = "\t" if text.splitlines()[0].count("\t") > text.splitlines()[0].count(",") else ","
    for r in csv.DictReader(text.splitlines(), delimiter=delim):
        r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        company = r.get("company", "")
        title = r.get("title") or r.get("role") or r.get("position", "")
        url = r.get("job_url") or r.get("link") or r.get("url", "")
        status = SEED_STATUS.get(r.get("status", "submitted").lower() or "submitted")
        if not (company or url):
            continue
        if not status:
            left.append(f"{company} / {title} ({r.get('status')}: {r.get('notes', '')})")
            continue
        jid = "seed:" + hashlib.sha1((url or company + title).lower().encode()).hexdigest()[:12]
        when = r.get("submitted_at") or r.get("date_found") or ""
        upsert(rows, jid, company=company, title=title, url=url, status=status,
               region=region_for_city(cfg, r.get("city", "")), proof=r.get("proof", ""),
               note=("imported: " + r.get("notes", "")).strip())
        if when:  # keep the original date so today's summary isn't polluted
            rows[jid]["updated_at"] = when.replace("Z", "+00:00") if "T" in when else when + "T00:00:00+00:00"
        kept += 1
    save_rows(rows)
    print(f"Imported {kept} past applications.")
    for x in left:
        print(f"  not imported (will be retried if found again): {x}")


def cmd_summary(_a):
    rows = load_rows()
    today = [r for r in rows.values() if local_today(r["updated_at"]) and not r["job_id"].startswith("seed:")]
    by = {}
    for r in today:
        by.setdefault(r["status"], []).append(r)
    total = sum(1 for r in rows.values() if r["status"] == "submitted")
    today_str = ", ".join(f"{len(v)} {k}" for k, v in sorted(by.items())) or "nothing"
    lines = [f"# Run summary {date.today()}", "", f"Submitted all-time: {total}. Today: {today_str}."]
    backlog = sum(1 for r in rows.values() if r["status"] == "shortlisted")
    lines.append(f"Shortlisted and waiting: {backlog}.")
    for status in ("submitted", "dry_run_ok", "needs_input", "blocked", "failed", "in_progress"):
        if by.get(status):
            lines += ["", f"## {status} ({len(by[status])})"]
            lines += [f"- {r['company']} / {r['title']} [{r['region']}] {r['proof'] or r['note']}"
                      for r in by[status]]
    if by.get("needs_input"):
        lines += ["", "Answer these in config/rules.toml; the jobs retry next run."]
    out = DATA / "runs" / f"{date.today()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    def job(name, fn, extra=None):
        s = sub.add_parser(name)
        s.add_argument("job_id")
        if extra:
            s.add_argument(f"--{extra}", required=True)
        s.set_defaults(fn=fn)

    job("start", cmd_start)
    job("submitted", cmd_submitted, "proof")
    job("dry-run-ok", cmd_dry_run_ok, "proof")
    job("needs-input", cmd_needs_input, "question")
    job("blocked", cmd_simple("blocked"), "reason")
    job("failed", cmd_simple("failed"), "reason")
    job("reopen", cmd_reopen, "reason")
    job("shortlist", cmd_shortlist, "reason")
    job("email-confirmed", cmd_email_confirmed)
    for name, fn in (("record-scores", cmd_record_scores), ("record-skips", cmd_record_scores), ("seed", cmd_seed)):
        s = sub.add_parser(name)
        s.add_argument("file")
        s.set_defaults(fn=fn)
    sub.add_parser("summary").set_defaults(fn=cmd_summary)
    n = sub.add_parser("next")
    n.add_argument("--order", default="", help="region priority for this run, e.g. london,*,new_york")
    n.set_defaults(fn=cmd_next)
    sub.add_parser("mark-universe-refreshed").set_defaults(fn=cmd_mark_universe)
    a = p.parse_args()
    DATA.mkdir(exist_ok=True)
    # one tracker command at a time, so recording scores while an application is in flight can't
    # lose either side's update (each command reads tracker.csv, changes it, and writes it back)
    with open(DATA / ".tracker.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        a.fn(a)


if __name__ == "__main__":
    main()
