#!/usr/bin/env python3
"""Fingerprint the resume PDFs and make them read-only. Run this yourself, not the agent.

  python3 scripts/lock_resumes.py            lock the current files
  python3 scripts/lock_resumes.py --unlock   make them writable so you can replace them,
                                             then run without --unlock to lock the new ones

After locking, tracker.py and the upload hook refuse any resume whose bytes differ from the
fingerprint, so a file that was edited, regenerated or swapped can never be uploaded.
The agent is denied permission to run this script (see .claude/settings.json).
"""
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10 or older (e.g. the Mac's built-in 3.9): use bundled copy
    sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "config" / "resume.lock"

if not (ROOT / "config" / "rules.toml").exists():
    sys.exit("config/rules.toml is missing. Copy config/rules.example.toml to config/rules.toml and fill it in.")
with open(ROOT / "config" / "rules.toml", "rb") as f:
    paths = sorted({r["resume"] for r in tomllib.load(f)["regions"].values() if r.get("resume")})
if not paths:
    sys.exit("No resumes set. Give each [regions.*] in config/rules.toml a resume path, e.g. profile/resume.pdf.")

if "--unlock" in sys.argv:
    for p in paths:
        os.chmod(ROOT / p, 0o644)
    print("Unlocked. Replace the PDFs, then run this script again without --unlock.")
    sys.exit(0)

lock = {}
for p in paths:
    f = ROOT / p
    if not f.is_file():
        sys.exit(f"Missing {p}. Put your resume PDF there (see profile/README.md).")
    lock[p] = hashlib.sha256(f.read_bytes()).hexdigest()
    os.chmod(f, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)  # 0444, read-only
LOCK.write_text(json.dumps(lock, indent=2) + "\n")
for p, h in lock.items():
    print(f"locked {p}  sha256 {h[:16]}…")
