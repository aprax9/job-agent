#!/usr/bin/env python3
"""Build data/companies.json: every company Common Crawl has seen on the 9 no-login job sites.

  python3 scripts/universe.py                 # sites not refreshed in 7 days, 2 most recent crawls (10-40 min)
  python3 scripts/universe.py --ats ashby,greenhouse,lever --crawls 1   # quick start
  python3 scripts/universe.py --seeds-only    # just config/boards.toml + past applications

Safe to rerun: it merges into the existing file and keeps each company's polling history.
Each site's last good crawl is kept in data/state.json (universe_sites); universe_refreshed moves
only when every site is current, so a crawl that Common Crawl didn't answer is retried next run.
Common Crawl is a free public web archive; its index server asks for gentle request rates,
so this runs one request at a time and backs off when told to slow down.
"""
import argparse
import csv
import json
import sys
import time
import urllib.parse
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ats  # noqa: E402

try:
    import tomllib
except ImportError:  # Python 3.10 or older (e.g. the Mac's built-in 3.9): use bundled copy
    sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
COMPANIES = DATA / "companies.json"
CC_QUERIES = {
    "ashby": ["jobs.ashbyhq.com/*"],
    "greenhouse": ["boards.greenhouse.io/*", "job-boards.greenhouse.io/*"],
    "lever": ["jobs.lever.co/*"],
    "bamboohr": ["*.bamboohr.com"],
    "breezy": ["*.breezy.hr"],
    "workable": ["apply.workable.com/*"],
    "recruitee": ["*.recruitee.com"],
    "smartrecruiters": ["jobs.smartrecruiters.com/*", "careers.smartrecruiters.com/*"],
    "rippling": ["ats.rippling.com/*"],
}


REFRESH_DAYS = 7
GIVE_UP_AFTER = 2  # requests in a row that never got an answer: Common Crawl is down, stop for today
unanswered_in_a_row = 0


def cc_get(url, tries=6):
    """Response body, b"" if the index has nothing there, None if Common Crawl never answered."""
    global unanswered_in_a_row
    if unanswered_in_a_row >= GIVE_UP_AFTER:
        return None
    for i in range(tries):
        try:
            body = ats.fetch(url, accept="application/json", timeout=60, retries=0)
            unanswered_in_a_row = 0
            return body
        except ats.Gone:
            unanswered_in_a_row = 0
            return b""
        except Exception as e:
            wait = min(60, 5 * (2 ** i))
            print(f"    Common Crawl busy ({type(e).__name__}); waiting {wait}s", flush=True)
            time.sleep(wait)
    unanswered_in_a_row += 1
    if unanswered_in_a_row >= GIVE_UP_AFTER:
        print("    Common Crawl looks down; skipping the remaining queries for this run", flush=True)
    return None


def crawl_slugs(api, query, max_pages):
    """(companies found, whether every request was answered)."""
    q = urllib.parse.urlencode({"url": query, "output": "json"}, safe="*/")
    meta = cc_get(f"{api}?{q}&showNumPages=true")
    if meta is None:
        return set(), False
    try:
        pages = int(json.loads(meta or b"{}").get("pages", 0))
    except ValueError:
        pages = 0
    found, complete = set(), True
    for p in range(min(pages, max_pages)):
        body = cc_get(f"{api}?{q}&fl=url&page={p}")
        if body is None:
            complete = False
            continue
        for line in body.decode("utf-8", "ignore").splitlines():
            try:
                hit = ats.parse_board_url(json.loads(line).get("url", ""))
            except ValueError:
                continue
            if hit:
                found.add(hit)
        print(f"    page {p + 1}/{min(pages, max_pages)}: {len(found)} companies so far", flush=True)
        time.sleep(1)
    return found, complete


def load():
    return json.loads(COMPANIES.read_text()) if COMPANIES.exists() else {}


def add(universe, ats_name, slug, source):
    key = f"{ats_name}:{slug.lower()}"
    if key not in universe:
        universe[key] = {"ats": ats_name, "slug": slug, "source": source, "added": date.today().isoformat()}
        return 1
    return 0


def seeds(universe):
    n = 0
    with open(ROOT / "config" / "boards.toml", "rb") as f:
        boards = tomllib.load(f)
    for ats_name in ats.ATS_LIST:
        for slug in boards.get(ats_name, []):
            n += add(universe, ats_name, slug, "seed")
            universe[f"{ats_name}:{slug.lower()}"]["seed"] = True
    tracker = DATA / "tracker.csv"
    if tracker.exists():
        with open(tracker, newline="") as f:
            for r in csv.DictReader(f):
                hit = ats.parse_board_url(r.get("url", ""))
                if hit:
                    n += add(universe, hit[0], hit[1], "history")
    return n


def fresh(day):
    return bool(day) and (date.today() - date.fromisoformat(day)).days < REFRESH_DAYS


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ats", default="", help=f"sites to crawl (default: every site not refreshed in the last {REFRESH_DAYS} days)")
    p.add_argument("--crawls", type=int, default=2, help="how many recent monthly crawls to read")
    p.add_argument("--max-pages", type=int, default=300, help="cap per query per crawl")
    p.add_argument("--seeds-only", action="store_true")
    a = p.parse_args()

    DATA.mkdir(exist_ok=True)
    state_file = DATA / "state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    universe = load()
    print(f"Seeds and past applications: +{seeds(universe)} new")
    if a.ats:
        sites = [x.strip() for x in a.ats.split(",") if x.strip()]
    else:
        done_on = state.get("universe_sites", {})
        sites = [s for s in ats.ATS_LIST if not fresh(done_on.get(s))]
        skipped = [s for s in ats.ATS_LIST if s not in sites]
        if skipped:
            print(f"Refreshed in the last {REFRESH_DAYS} days, skipping: {', '.join(skipped)}")
    crawls = []
    answered = {s: False for s in sites}  # site -> at least one crawl answered every request
    if not a.seeds_only and not sites:
        print("Every site is up to date.")
    if not a.seeds_only and sites:
        crawls = json.loads(cc_get("https://index.commoncrawl.org/collinfo.json") or b"[]")[: a.crawls]
        if not crawls:
            print("Common Crawl index unreachable right now; kept the existing list. Try again later.")
        for c in crawls:
            print(f"Crawl {c['id']}")
            for ats_name in sites:
                before, ok = len(universe), True
                for query in CC_QUERIES.get(ats_name, []):
                    print(f"  {ats_name}: {query}", flush=True)
                    found, complete = crawl_slugs(c["cdx-api"], query, a.max_pages)
                    ok = ok and complete
                    for hit_ats, slug in found:
                        add(universe, hit_ats, slug, "commoncrawl")
                answered[ats_name] = answered[ats_name] or ok
                print(f"  {ats_name}: +{len(universe) - before} new companies"
                      + ("" if ok else " (Common Crawl didn't answer every request)"))
    COMPANIES.write_text(json.dumps(universe, indent=0, sort_keys=True))
    failed = [s for s in sites if not answered[s]]
    if not a.seeds_only:
        done_on = state.setdefault("universe_sites", {})
        for s in sites:
            if answered[s]:
                done_on[s] = date.today().isoformat()
        # a partial refresh isn't a refresh: the date moves only once every site is current,
        # so the next run retries just the sites that failed
        if all(fresh(done_on.get(s)) for s in ats.ATS_LIST):
            state["universe_refreshed"] = date.today().isoformat()
        state_file.write_text(json.dumps(state, indent=1, sort_keys=True))
    if failed and not a.seeds_only:
        print(f"INCOMPLETE: Common Crawl didn't answer for {', '.join(failed)}. Companies found so far are kept. "
              f"Retry later with: python3 scripts/universe.py --ats {','.join(failed)}")
    counts = {}
    for v in universe.values():
        counts[v["ats"]] = counts.get(v["ats"], 0) + 1
    print("Company list: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + f" (total {len(universe)})")


if __name__ == "__main__":
    main()
