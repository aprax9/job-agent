#!/usr/bin/env python3
"""Stop hook: don't let a run end with a job still marked in progress."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
event = json.load(sys.stdin)
cur_file = ROOT / "data" / "current_job.json"

if event.get("stop_hook_active") or not cur_file.exists():
    sys.exit(0)  # second pass or nothing open: let it stop; tracker marks leftovers stale next run

cur = json.loads(cur_file.read_text())
print(f"{cur['job_id']} is still in progress. Record an outcome with scripts/tracker.py "
      f"(submitted / dry-run-ok / needs-input / blocked / failed), then run "
      f"`python3 scripts/tracker.py summary` before finishing.", file=sys.stderr)
sys.exit(2)
