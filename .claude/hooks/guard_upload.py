#!/usr/bin/env python3
"""PreToolUse hook for browser file uploads.

Allows only the resume that tracker.py routed to the job in progress, and only if its bytes
still match the fingerprint in config/resume.lock. Anything else is blocked (exit 2), so neither
the wrong resume nor an altered one can go out, even if the model gets confused.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])


def block(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)


event = json.load(sys.stdin)
paths = (event.get("tool_input") or {}).get("paths") or []
if isinstance(paths, str):
    paths = [paths]
if not paths:
    sys.exit(0)  # closing a file chooser without uploading

cur_file = ROOT / "data" / "current_job.json"
if not cur_file.exists():
    block("Upload blocked: no job in progress. Run `python3 scripts/tracker.py start <job_id>` first.")
cur = json.loads(cur_file.read_text())
resume = Path(cur["resume"]).resolve()

lock_file = ROOT / "config" / "resume.lock"
locked = json.loads(lock_file.read_text()) if lock_file.exists() else {}

for p in paths:
    rp = Path(p)
    rp = (rp if rp.is_absolute() else ROOT / rp).resolve()
    if rp == resume:
        rel = str(rp.relative_to(ROOT.resolve())) if rp.is_relative_to(ROOT.resolve()) else ""
        if locked.get(rel) != hashlib.sha256(rp.read_bytes()).hexdigest():
            block(f"Upload blocked: {rel or rp} doesn't match its locked fingerprint, so it has "
                  f"been altered. Stop and record this job as failed; don't modify the file.")
        continue
    block(f"Upload blocked: {p}. For {cur['job_id']} ({cur['region']}) the only file you may "
          f"upload is {resume}. No cover letters.")
sys.exit(0)
