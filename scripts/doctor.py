#!/usr/bin/env python3
"""Setup check. Run it after each setup step in the README, and whenever a run misbehaves.

  python3 scripts/doctor.py

Read-only. Prints one line per check (ok / FIX / note) and exits 1 if anything needs fixing.
"""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10 or older (e.g. the Mac's built-in 3.9): use bundled copy
    sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
fixes = 0


def say(kind, msg):
    global fixes
    fixes += kind == "FIX"
    print(f"{kind:>4}  {msg}")


def tools():
    v = sys.version_info
    say("ok" if v >= (3, 9) else "FIX", f"Python {v.major}.{v.minor} (needs 3.9+)")
    if shutil.which("npx"):
        say("ok", "npx found (runs the Playwright browser)")
    else:
        say("FIX", "npx not found: install Node 18+ from https://nodejs.org")
    chrome = (Path("/Applications/Google Chrome.app").exists()
              or any(shutil.which(c) for c in ("google-chrome", "google-chrome-stable")))
    say("ok" if chrome else "FIX", "Google Chrome found" if chrome else "Google Chrome not found: install it")


def mcp_paths():
    """.mcp.json points the browser profile and screenshots at a folder; it must be this one."""
    try:
        args = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["playwright"]["args"]
    except (OSError, KeyError, ValueError):
        say("FIX", ".mcp.json is missing or has no playwright server")
        return
    out = args[args.index("--output-dir") + 1] if "--output-dir" in args else ""
    out = Path(out.replace("${HOME}", str(Path.home()))).expanduser()
    if out.parent.resolve() == (ROOT / "data").resolve():
        say("ok", ".mcp.json paths point at this folder")
    else:
        say("FIX", f".mcp.json saves screenshots to {out}, outside this folder. Clone the repo to "
                   f"~/job-agent, or change both paths in .mcp.json to start with {ROOT}")


def rules():
    path = ROOT / "config" / "rules.toml"
    if not path.exists():
        say("FIX", "config/rules.toml missing: cp config/rules.example.toml config/rules.toml")
        return
    try:
        cfg = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        say("FIX", f"config/rules.toml doesn't parse: {e}")
        return
    say("ok", "config/rules.toml parses")
    regions, order = cfg.get("regions", {}), cfg.get("run", {}).get("region_order", [])
    for name in order:
        if name not in regions:
            say("FIX", f"region_order lists {name}, but there's no [regions.{name}]")

    lock_file = ROOT / "config" / "resume.lock"
    lock = json.loads(lock_file.read_text()) if lock_file.exists() else {}
    for rel in sorted({r.get("resume", "") for r in regions.values()}):
        users = ", ".join(n for n, r in regions.items() if r.get("resume", "") == rel)
        f = ROOT / rel if rel else None
        if not rel:
            say("note", f"no resume set for {users}: those jobs park as needs_input")
        elif not f.is_file():
            say("FIX", f"{rel} not found ({users}). Put your resume PDF there; see profile/README.md")
        elif lock.get(rel) != hashlib.sha256(f.read_bytes()).hexdigest():
            say("FIX", f"{rel} isn't locked, or changed since it was: python3 scripts/lock_resumes.py")
        else:
            say("ok", f"{rel} present and locked ({users})")

    ans = cfg.get("answers", {})
    blank = [k for k, v in ans.items() if v == ""]
    blank += [f"yes_no.{k}" for k, v in ans.get("yes_no", {}).items() if v == ""]
    for name in order:
        if name not in ans:
            say("note", f"no [answers.{name}] table: phone, location and work-authorization questions there park as needs_input")
        blank += [f"{name}.{k}" for k, v in ans.get(name, {}).items() if v == ""]
    if blank:
        say("note", f"{len(blank)} blank answers in rules.toml; forms that ask for them park as needs_input:")
        for i in range(0, len(blank), 6):
            print("        " + ", ".join(blank[i:i + 6]))
    else:
        say("ok", "every answer in rules.toml is filled in")

    run = cfg.get("run", {})
    if run.get("dry_run", True):
        say("note", "dry_run = true: forms are filled and screenshotted, never submitted")
    else:
        say("note", f"dry_run = false: the agent submits up to {run.get('max_submissions_per_run')} applications a day")
    if (ROOT / "STOP").exists() or not run.get("enabled", True):
        say("note", "applying is switched off (STOP file or enabled = false)")


def data():
    companies = ROOT / "data" / "companies.json"
    if companies.exists():
        say("ok", f"company list: {len(json.loads(companies.read_text()))} companies")
    else:
        say("note", "no company list yet: python3 scripts/universe.py --ats ashby,greenhouse,lever --crawls 1")


if __name__ == "__main__":
    os.chdir(ROOT)
    tools()
    mcp_paths()
    rules()
    data()
    print("\nAll set." if not fixes else f"\n{fixes} thing{'s' if fixes > 1 else ''} to fix.")
    sys.exit(1 if fixes else 0)
