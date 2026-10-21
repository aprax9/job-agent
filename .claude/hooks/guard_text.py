#!/usr/bin/env python3
"""PreToolUse hook for typing into forms: caps free-text answers at N sentences.

N comes from config/rules.toml [run].free_text_max_sentences (default 2). Only active while a
job is in progress, so logging in or searching isn't affected.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
ABBREV = re.compile(r"\b(e\.g|i\.e|etc|vs|approx|incl|St|Mr|Ms|Mrs|Dr|Inc|Ltd|Co|Jr|Sr|U\.S|U\.K|No)\.", re.I)


def sentences(text):
    t = ABBREV.sub(lambda m: m.group(0).replace(".", "<dot>"), text)
    t = re.sub(r"(\d)\.(\d)", r"\1<dot>\2", t)            # 1.6M, 4.5
    parts = re.split(r"(?<=[.!?])\s+|\n+", t.strip())
    return [p for p in parts if re.search(r"[A-Za-z]{2,}", p)]


def strings(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("text", "value") and isinstance(v, str):
                yield v
            else:
                yield from strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from strings(v)


cur_file = ROOT / "data" / "current_job.json"
if not cur_file.exists():
    sys.exit(0)
limit = json.loads(cur_file.read_text()).get("max_sentences", 2)
event = json.load(sys.stdin)
for s in strings(event.get("tool_input") or {}):
    n = len(sentences(s))
    if n > limit:
        print(f"Blocked: this answer is {n} sentences; the limit is {limit}. Rewrite it in "
              f"{limit} sentences or fewer and type it again:\n{s[:300]}", file=sys.stderr)
        sys.exit(2)
sys.exit(0)
