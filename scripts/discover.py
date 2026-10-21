#!/usr/bin/env python3
"""Find matching postings across every company in data/companies.json.

  python3 scripts/discover.py run [--full] [--ats ashby,greenhouse] [--limit N]
      Poll boards, filter, write data/queue.json (all candidates), data/to_score.json (the next
      batch for the scorer) and data/runs/census-<date>.md (counts at every stage).
      Daily runs poll seeds + companies that have had roles in your regions; a full sweep of
      every company runs on the first run, every full_sweep_every_days, or with --full.
  python3 scripts/discover.py rebatch [--size N]
      Rebuild data/to_score.json from the current queue (no polling): the next N unscored postings.
  python3 scripts/discover.py add-company <careers or job URL> [...]
      Add companies found some other way (e.g. a web search) to the company list.
  python3 scripts/discover.py add --url URL --company NAME --title TITLE --location LOC [...]
      Add a single posting by hand.
"""
import argparse
import csv
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ats  # noqa: E402

try:
    import tomllib
except ImportError:  # Python 3.10 or older (e.g. the Mac's built-in 3.9): use bundled copy
    sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
CONFIG, DATA = ROOT / "config", ROOT / "data"
QUEUE, TO_SCORE, TRACKER = DATA / "queue.json", DATA / "to_score.json", DATA / "tracker.csv"
COMPANIES, PROFILES, STATE = DATA / "companies.json", DATA / "company_profiles.json", DATA / "state.json"
SCORE_DESC_CHARS = 2500


def load_toml(name):
    if not (CONFIG / name).exists():
        sys.exit(f"config/{name} is missing. Copy config/rules.example.toml to config/rules.toml and fill it in.")
    with open(CONFIG / name, "rb") as f:
        return tomllib.load(f)


def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path, obj, indent=1):
    DATA.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=indent, ensure_ascii=False, sort_keys=True))
    tmp.replace(path)


def kw_match(kw, text):
    return re.search(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])", text) is not None


def norm_title(t):
    return re.sub(r"\s+and\s+", " & ", (t or "").lower())


def norm_url(u):
    # keep Greenhouse's gh_jid: company-hosted boards (Coupang, Databricks, Stripe...) put every job
    # on one path and tell them apart only by ?gh_jid=N
    jid = re.search(r"[?&]gh_jid=(\d+)", u or "")
    base = (u or "").split("?")[0].split("#")[0].rstrip("/").lower()
    return f"{base}?gh_jid={jid.group(1)}" if jid else base


UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def native_id(url):
    """The site's own posting id, so the same job matches across URL variants."""
    u = norm_url(url)
    m = UUID.search(u) or re.search(r"gh_jid=(\d+)$", u)
    if m:
        return m.group(m.lastindex or 0)
    m = re.search(r"(?:jobs|listing|positions|careers)/(\d{5,})$", u)
    return m.group(1) if m else ""

# ---------- filters ----------

def classify_region(job, rules):
    loc = (job.get("location") or "").lower()
    remote = bool(job.get("remote")) or kw_match("remote", loc)
    for name in rules["run"]["region_order"]:
        r = rules["regions"].get(name)
        if not r:
            continue
        # e.g. London, Ontario is not London, UK
        if any(kw_match(k, loc) for k in r.get("location_exclude", [])):
            continue
        if any(kw_match(k, loc) for k in r.get("location_keywords", [])):
            return name
        if remote and any(kw_match(k, loc) for k in r.get("remote_country_keywords", [])):
            return name
    return None


def title_ok(title, t, extra=()):
    """extra: the region's own title_include_extra, added to the shared title_include."""
    tl = norm_title(title)
    if any(kw_match(k, tl) for k in t.get("title_exclude", [])):
        return False
    return any(kw_match(norm_title(k), tl) for k in [*t.get("title_include", []), *extra])


def region_titles(rules, region):
    return rules["regions"].get(region, {}).get("title_include_extra", [])


LEVEL = (r"native|fluent|fluency|fluently|bilingual|business[- ]level|business proficiency|"
         r"professional (?:working )?proficiency|working proficiency|full proficiency|"
         r"proficient|proficiency|advanced|jlpt\s*n?[123]\b|n[123] level|n[123] or above")
SOFT = r"\b(plus|preferred|preferable|nice[- ]to[- ]have|advantage|advantageous|bonus|desirable|ideally|asset|beneficial)\b"
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
CJK_LANGS = {"japanese", "chinese", "mandarin", "cantonese", "putonghua", "korean"}


STOPWORDS = {
    "english": set("the and of to in for with you we our your is are a an on as this that be will at by from or".split()),
    "french": set("le la les des du et est une pour avec dans sur vous nous votre notre au aux par sont être qui que ou ce cette ces son sa ses leur leurs".split()),
    "german": set("und der die das mit für wir sie ihre zu von ist auf eine ein den im bei dich du deine unsere oder auch".split()),
    "dutch": set("het van een voor met wij je zijn op bij naar ons onze jouw ook als aan door".split()),
    "danish": set("og at til med for vi du er af på som det har vores din dig eller også".split()),
    "portuguese": set("para com uma um os na no em que da do nossa você seu sua são pelo pela".split()),
}


def written_mainly_in(text, langs):
    """The blocked language a posting is mostly written in (by common-word counts), else None."""
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœäöüßåæø']+", text.lower())
    if len(words) < 60:
        return None
    en = sum(w in STOPWORDS["english"] for w in words)
    for lang in langs:
        stop = STOPWORDS.get(lang)
        if stop:
            n = sum(w in stop for w in words)
            if n > 2 * en and n / len(words) > 0.10:
                return lang
    return None


def language_block(job, t):
    """Reason string if the posting requires a blocked language above beginner, else None."""
    text = f"{job.get('title', '')}\n{job.get('description', '')}"
    langs = t.get("blocked_languages", [])
    if CJK_LANGS & {x.lower() for x in langs}:
        letters = re.findall(r"[A-Za-z]|" + CJK.pattern, text)
        if len(letters) > 200 and sum(1 for c in letters if CJK.match(c)) / len(letters) > 0.3:
            return "posting written mainly in Japanese/Chinese/Korean"
    mainly = written_mainly_in(text, langs)
    if mainly:
        return f"posting written mainly in {mainly.title()}"
    if not langs:
        return None
    # a language word, but not "Chinese market", "Japanese clients", etc.
    lang = (r"\b(" + "|".join(map(re.escape, langs)) + r")\b"
            r"(?!\W+(?:markets?|customers?|clients?|companies|business(?:es)?(?![- ]level)|region|economy|"
            r"government|regulat\w*|partners?|enterprises?|culture|stakeholders?)\b)")
    level_not_english = rf"\b(?:{LEVEL})\b(?!\W+(?:in\W+)?english\b)"
    near = re.compile(rf"(?:\b(?:{LEVEL})\b\W+(?:\w+\W+){{0,6}}?{lang})|(?:{lang}\W+(?:\w+\W+){{0,4}}?{level_not_english})", re.I)
    for sentence in re.split(r"[.;\n\u2022\u3002]+", text):
        m = near.search(sentence)
        if m and not re.search(SOFT, sentence, re.I):
            return f"requires {next(g for g in m.groups() if g).title()}"
    return None


EVERGREEN = re.compile(r"not (?:for |an |for an )?(?:existing|current) vacanc|talent (?:pool|community|pipeline)|"
                       r"future (?:opportunit|openings|roles)|evergreen (?:role|posting|requisition)|"
                       r"general (?:application|interest)", re.I)


def is_evergreen(job):
    return bool(EVERGREEN.search(job.get("title", "") + "\n" + job.get("description", "")))


def pay_too_low(job, rules):
    region = rules["regions"].get(job["region"], {})
    floor, cur = region.get("salary_floor", 0), region.get("salary_currency", "")
    if not floor:
        return False
    top = ats.max_annual_pay(f"{job.get('compensation', '')}\n{job.get('description', '')}", cur)
    return top is not None and top < floor


def days_old(job):
    try:
        return (date.today() - date.fromisoformat(job.get("posted_at") or "")).days
    except ValueError:
        return None


def company_verdict(job, rules, profiles):
    """'yes' / 'no' / None (unknown: the scorer decides once, then it's cached)."""
    c = rules.get("companies", {})
    if not c.get("tech_only", True):
        return "yes"
    key = f"{job['ats']}:{job['slug'].lower()}"
    prof = profiles.get(key, {})
    if prof.get("tech") in ("yes", "no"):
        return prof["tech"]
    name = f"{job.get('company', '')} {job.get('slug', '')}".lower()
    if any(kw_match(k, name) for k in c.get("name_denylist", [])):
        profiles[key] = {**prof, "name": job["company"], "tech": "no", "reason": "name rule", "by": "rule"}
        return "no"
    ind = (job.get("industry") or "").lower()
    if ind:
        if any(ind == x.lower() for x in c.get("industry_denylist", [])):
            profiles[key] = {**prof, "name": job["company"], "tech": "no", "reason": f"industry: {ind}", "by": "rule"}
            return "no"
        if any(ind == x.lower() for x in c.get("industry_allowlist", [])):
            profiles[key] = {**prof, "name": job["company"], "tech": "yes", "reason": f"industry: {ind}", "by": "rule"}
            return "yes"
    return None

# ---------- tracker index ----------

TERMINAL = {"submitted", "blocked", "skipped"}


def tracker_rows():
    if not TRACKER.exists():
        return []
    with open(TRACKER, newline="") as f:
        return list(csv.DictReader(f))


def handled_index(rules, rows):
    """Jobs to leave alone (done, or not retryable yet), plus jobs already scored and waiting."""
    ids, urls, pairs, natives, shortlisted = set(), set(), set(), set(), set()
    rules_mtime = datetime.fromtimestamp((CONFIG / "rules.toml").stat().st_mtime, timezone.utc)
    for r in rows:
        st, attempts = r.get("status", ""), int(r.get("attempts") or 0)
        if st == "shortlisted":
            shortlisted.add(r["job_id"])
            continue
        done = st in TERMINAL or (st == "failed" and attempts >= 2)
        done |= st == "dry_run_ok" and rules["run"].get("dry_run", True)
        if st == "needs_input":  # retry only once rules.toml has been edited since
            try:
                done |= datetime.fromisoformat(r["updated_at"]) >= rules_mtime
            except (KeyError, ValueError):
                pass
        if done:
            ids.add(r["job_id"])
            urls.add(norm_url(r.get("url")))
            pairs.add((ats.company_key(r.get("company")), norm_title(r.get("title"))))
            if native_id(r.get("url")):
                natives.add(native_id(r.get("url")))
    return ids, urls, pairs, natives, shortlisted


def is_handled(job, idx):
    ids, urls, pairs, natives, _ = idx
    nid = native_id(job.get("url")) or native_id(job.get("apply_url"))
    return bool(job["id"] in ids or norm_url(job.get("url")) in urls or (nid and nid in natives)
                or (ats.company_key(job["company"]), norm_title(job["title"])) in pairs)


def company_counts(rules, rows):
    """Applications per company inside the cap window (submitted, or dry_run_ok while dry-running)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=rules["run"].get("company_cap_days", 60))
    counted = {"submitted"} | ({"dry_run_ok"} if rules["run"].get("dry_run", True) else set())
    counts = Counter()
    for r in rows:
        if r.get("status") not in counted:
            continue
        try:
            if datetime.fromisoformat(r["updated_at"]) < cutoff:
                continue
        except (KeyError, ValueError):
            pass
        counts[ats.company_key(r.get("company"))] += 1
        parts = r["job_id"].split(":")
        hit = (parts[0], parts[1]) if len(parts) >= 3 else ats.parse_board_url(r.get("url"))  # seed rows: use the URL
        if hit:
            counts[f"{hit[0]}:{hit[1].lower()}"] += 1
    return counts

# ---------- polling ----------

def boards_to_poll(rules, universe, args, state):
    disc = rules.get("discovery", {})
    if args.boards:  # just these companies; the carry-over in cmd_run keeps everyone else's postings
        return [k for k in (b.strip().lower() for b in args.boards.split(",")) if k in universe], False
    allowed = set(args.ats.split(",")) if args.ats else set(disc.get("ats", ats.ATS_LIST))
    last_full = state.get("last_full_sweep")
    full = args.full or not last_full or (
        date.today() - date.fromisoformat(last_full)).days >= disc.get("full_sweep_every_days", 7)
    memory = disc.get("geo_memory_days", 120)
    picked = []
    for key, c in universe.items():
        if c["ats"] not in allowed or c.get("fails", 0) >= 3:
            continue
        if full or c.get("seed") or "last_ok" not in c:
            picked.append(key)
            continue
        geo = c.get("geo_hit")
        if geo and (date.today() - date.fromisoformat(geo)).days <= memory:
            picked.append(key)
    random.shuffle(picked)  # spread load across sites
    if args.limit:
        seeds = [k for k in picked if universe[k].get("seed")]
        picked = (seeds + [k for k in picked if k not in set(seeds)])[: args.limit]
    return picked, full


def poll_one(key, c):
    try:
        return key, "ok", list(ats.LISTERS[c["ats"]](c["slug"]))
    except ats.Gone:
        return key, "gone", []
    except Exception as e:
        return key, f"error:{type(e).__name__}", []

# ---------- commands ----------

def cmd_run(args):
    rules, universe = load_toml("rules.toml"), load_json(COMPANIES, {})
    if not universe:
        sys.exit("No company list yet. Run: python3 scripts/universe.py")
    t, disc = rules["targeting"], rules.get("discovery", {})
    profiles, state = load_json(PROFILES, {}), load_json(STATE, {})
    rows = tracker_rows()
    idx, counts = handled_index(rules, rows), company_counts(rules, rows)
    cap = rules["run"].get("max_per_company", 2)
    excluded = [ats.company_key(x) for x in t.get("exclude_companies", [])]
    max_age = disc.get("max_posting_age_days", 45)
    today = date.today().isoformat()

    keys, full = boards_to_poll(rules, universe, args, state)
    print(f"Polling {len(keys)} company boards ({'full sweep' if full else 'daily'}).", flush=True)
    stage, board, matched, done = defaultdict(Counter), Counter(), [], 0
    answered = set()  # boards that said ok or gone; jobs from any other board are carried over below
    with ThreadPoolExecutor(max_workers=disc.get("poll_workers", 16)) as pool:
        futures = [pool.submit(poll_one, k, universe[k]) for k in keys]
        for fut in as_completed(futures):
            key, status, jobs = fut.result()
            c = universe[key]
            done += 1
            if status in ("ok", "gone"):
                answered.add(key)
            if done % 500 == 0:
                print(f"  {done}/{len(keys)} boards checked, {len(matched)} title+city matches so far", flush=True)
            if status == "ok":
                c["last_ok"], c["fails"] = today, 0
                board[f"ok:{c['ats']}"] += 1
            elif status == "gone":
                c["fails"] = c.get("fails", 0) + 1
                board["gone"] += 1
                continue
            else:
                board["error"] += 1
                continue
            for job in jobs:
                stage["1 postings seen"][job["ats"]] += 1
                job["region"] = classify_region(job, rules)
                if not job["region"]:
                    continue
                c["geo_hit"] = today
                stage["2 in your cities"][job["region"]] += 1
                if not title_ok(job["title"], t, region_titles(rules, job["region"])):
                    continue
                stage["3 title match"][job["region"]] += 1
                matched.append(job)

    # cheap filters first, then fetch full descriptions only for what's left
    survivors = []
    for job in matched:
        r, ck = job["region"], ats.company_key(job["company"])
        if any(e and (e == ck or e in ck) for e in excluded):
            stage["x excluded company"][r] += 1
        elif is_handled(job, idx):
            stage["x already applied or decided"][r] += 1
        elif counts[ck] >= cap or counts[f"{job['ats']}:{job['slug'].lower()}"] >= cap:
            stage["x company cap reached"][r] += 1
        elif days_old(job) is not None and days_old(job) > max_age:
            stage[f"x posted over {max_age} days ago"][r] += 1
        else:
            survivors.append(job)
    need = [j for j in survivors if j.get("needs_detail")]
    if need:
        print(f"Fetching full descriptions for {len(need)} postings", flush=True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(ats.fetch_detail, need))

    candidates = []
    for job in survivors:
        r, age = job["region"], days_old(job)
        if age is not None and age > max_age:
            stage[f"x posted over {max_age} days ago"][r] += 1
        elif is_evergreen(job):
            stage["x talent pool, not a real vacancy"][r] += 1
        elif language_block(job, t):
            stage["x language requirement"][r] += 1
        elif pay_too_low(job, rules):
            stage["x posted pay below your floor"][r] += 1
        elif company_verdict(job, rules, profiles) == "no":
            stage["x not a tech company"][r] += 1
        else:
            job["company_status"] = company_verdict(job, rules, profiles) or "unclassified"
            job["days_old"] = age
            candidates.append(job)
            stage["4 candidates"][r] += 1
            if age is not None and age <= 7:
                stage["5 candidates posted in the last 7 days"][r] += 1

    # company blurbs for unclassified companies (one fetch per company, cached)
    unknown = sorted({(j["ats"], j["slug"]) for j in candidates if j["company_status"] == "unclassified"})
    todo = [u for u in unknown if "about" not in profiles.get(f"{u[0]}:{u[1].lower()}", {})]
    fetched = {}
    if todo:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for (a, s), info in zip(todo, pool.map(lambda u: ats.company_about(*u), todo)):
                fetched[f"{a}:{s.lower()}"] = {**info, "about": info.get("about", "")}
    # merge into the file as it is now: the scorer may have recorded verdicts while we were polling
    profiles = load_json(PROFILES, {})
    for k, info in fetched.items():
        profiles[k] = {**profiles.get(k, {}), **info}

    order = region_rank(rules, args.order)
    candidates.sort(key=lambda j: (order.get(j["region"], 99), j["days_old"] if j["days_old"] is not None else 999))
    for j in candidates:
        j["discovered_at"] = today
    cand_ids = {c["id"] for c in candidates}
    old_queue = load_json(QUEUE, [])
    leads = [j for j in old_queue if j.get("source") != "ats"
             and j["id"] not in cand_ids and not is_handled(j, idx)]
    # keep postings from boards this run didn't hear back from (errors, or not polled with --ats/--limit),
    # so a shortlisted job isn't dropped as "no longer listed" just because its board timed out
    carried = [j for j in old_queue if j.get("source") == "ats" and j["id"] not in cand_ids
               and f"{j['ats']}:{j['slug'].lower()}" not in answered and not is_handled(j, idx)]
    queue = candidates + carried + leads
    if args.order:  # carried postings and leads follow the requested priority too
        queue.sort(key=lambda j: order.get(j.get("region"), 99))
    save_json(QUEUE, queue)

    batch = build_batch(queue, idx, profiles, rules["run"].get("score_batch_size", 60), rules)
    save_json(TO_SCORE, batch)
    save_json(PROFILES, profiles)
    save_json(COMPANIES, universe, indent=0)
    if full and not args.limit and not args.ats:
        state["last_full_sweep"] = today
    save_json(STATE, state)
    report(stage, board, keys, full, candidates, batch, rules, partial=bool(args.limit or args.ats or args.boards))


def region_rank(rules, override=""):
    """Region -> rank for scoring order. override: comma list of regions, where '*' stands for every
    region not named (in region_order). Only ordering changes: classify_region still uses region_order."""
    base = rules["run"]["region_order"]
    if not override:
        return {n: i for i, n in enumerate(base)}
    names = [x.strip() for x in override.split(",") if x.strip()]
    unknown = [n for n in names if n != "*" and n not in base]
    if unknown:
        sys.exit(f"--order: unknown region(s) {', '.join(unknown)}. Known: {', '.join(base)}")
    rest = [n for n in base if n not in names]
    seq = [m for n in names for m in (rest if n == "*" else [n])]
    seq += [n for n in rest if n not in seq]
    return {n: i for i, n in enumerate(seq)}


def build_batch(queue, idx, profiles, size, rules):
    """The next postings for the scorer: in queue order, skipping anything shortlisted or already decided."""
    batch = []
    for j in queue:
        if j["id"] in idx[4] or is_handled(j, idx):
            continue
        if j.get("slug"):  # company status as of now (rules or verdicts may have changed since discovery)
            verdict = company_verdict(j, rules, profiles)
            if verdict == "no":
                continue
            j = {**j, "company_status": verdict or "unclassified"}
        ckey = f"{j['ats']}:{j['slug'].lower()}" if j.get("slug") else ats.company_key(j["company"])
        entry = {k: j.get(k) for k in ("id", "company", "title", "location", "region", "compensation",
                                       "posted_at", "days_old", "industry", "company_status", "url")}
        entry.update(company_key=ckey, company_about=profiles.get(ckey, {}).get("about", "")[:600],
                     description=(j.get("description") or "")[:SCORE_DESC_CHARS])
        batch.append(entry)
        if size and len(batch) >= size:
            break
    return batch


def report(stage, board, keys, full, candidates, batch, rules, partial=False):
    regions = rules["run"]["region_order"]
    ok = {k[3:]: v for k, v in board.items() if k.startswith("ok:")}
    lines = [f"# Census {date.today()} ({'full sweep' if full else 'daily'})", "",
             f"Boards polled: {len(keys)}. Responded: {sum(ok.values())} "
             f"({', '.join(f'{k} {v}' for k, v in sorted(ok.items()))}). "
             f"Gone: {board['gone']}. Errors: {board['error']}.", "",
             f"Postings seen: {sum(stage['1 postings seen'].values())}", "",
             "| Stage | " + " | ".join(regions) + " |", "|---|" + "---|" * len(regions)]
    for name in sorted(k for k in stage if k != "1 postings seen"):
        lines.append(f"| {name[2:]} | " + " | ".join(str(stage[name].get(r, 0)) for r in regions) + " |")
    new7 = sum(stage["5 candidates posted in the last 7 days"].values())
    lines += ["", f"Queue: {len(candidates)} candidates. Next scoring batch: {len(batch)}.",
              f"New supply: {new7} candidates posted in the last 7 days (about {new7 / 7:.1f} a day).", ""]
    top = Counter(j["company"] for j in candidates).most_common(15)
    if top:
        lines.append("Most candidates by company: " + ", ".join(f"{c} {n}" for c, n in top))
    # a partial run (--limit/--ats/--boards) mustn't overwrite the day's real census
    out = DATA / "runs" / f"census-{date.today()}{'-partial' if partial else ''}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def cmd_rebatch(args):
    """Rebuild data/to_score.json from the current queue without polling, to score the next batch now."""
    rules = load_toml("rules.toml")
    idx, queue, profiles = handled_index(rules, tracker_rows()), load_json(QUEUE, []), load_json(PROFILES, {})
    if args.order:
        order = region_rank(rules, args.order)
        queue.sort(key=lambda j: order.get(j.get("region"), 99))
    waiting = build_batch(queue, idx, profiles, 0, rules)
    size = args.size or rules["run"].get("score_batch_size", 60)
    save_json(TO_SCORE, waiting[:size])
    print(f"Next scoring batch: {len(waiting[:size])} postings. Unscored after this batch: {len(waiting[size:])}.")


def cmd_add_company(args):
    universe, added = load_json(COMPANIES, {}), []
    for url in args.urls:
        hit = ats.parse_board_url(url)
        if not hit:
            print(f"  not a supported job site: {url}")
            continue
        key = f"{hit[0]}:{hit[1].lower()}"
        if key not in universe:
            universe[key] = {"ats": hit[0], "slug": hit[1], "source": "search", "added": date.today().isoformat()}
            added.append(key)
    save_json(COMPANIES, universe, indent=0)
    print(f"Added {len(added)} companies: {', '.join(added) or 'none new'}")


def cmd_add(args):
    rules = load_toml("rules.toml")
    job = ats.posting(id=f"{args.source}:{hashlib.sha1(norm_url(args.url).encode()).hexdigest()[:12]}",
                      source=args.source, company=args.company, title=args.title, location=args.location,
                      url=args.url, compensation=args.compensation or "",
                      description=Path(args.description_file).read_text() if args.description_file else "")
    job["discovered_at"] = date.today().isoformat()
    job["region"] = classify_region(job, rules)
    if not job["region"]:
        sys.exit(f"Not added: location '{job['location']}' isn't in a target region.")
    if not args.force and not title_ok(job["title"], rules["targeting"], region_titles(rules, job["region"])):
        sys.exit(f"Not added: title '{job['title']}' doesn't match targeting (use --force to override).")
    reason = language_block(job, rules["targeting"])
    if reason:
        sys.exit(f"Not added: {reason}")
    if is_handled(job, handled_index(rules, tracker_rows())):
        sys.exit("Not added: already applied, blocked, or skipped.")
    queue = load_json(QUEUE, [])
    if any(j["id"] == job["id"] for j in queue):
        sys.exit("Not added: already in the queue.")
    job["company_status"] = "unclassified"
    save_json(QUEUE, queue + [job])
    print(f"Added {job['id']} ({job['region']}): {job['company']} - {job['title']}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--full", action="store_true", help="poll every company, not just ones with roles in your cities")
    r.add_argument("--ats", default="", help="comma list to limit which sites are polled")
    r.add_argument("--limit", type=int, default=0, help="poll at most N boards (quick test)")
    r.add_argument("--boards", default="", help="comma list of ats:slug boards to poll; the rest of the queue is kept")
    order_help = "region priority for this run, e.g. london,*,new_york ('*' = every other region)"
    r.add_argument("--order", default="", help=order_help)
    b = sub.add_parser("rebatch", help="rebuild data/to_score.json from the queue without polling")
    b.add_argument("--size", type=int, default=0, help="postings in the batch (default: score_batch_size)")
    b.add_argument("--order", default="", help=order_help)
    c = sub.add_parser("add-company")
    c.add_argument("urls", nargs="+")
    a = sub.add_parser("add")
    a.add_argument("--url", required=True)
    a.add_argument("--company", required=True)
    a.add_argument("--title", required=True)
    a.add_argument("--location", required=True)
    a.add_argument("--description-file")
    a.add_argument("--compensation")
    a.add_argument("--source", default="lead")
    a.add_argument("--force", action="store_true")
    args = p.parse_args()
    {"run": cmd_run, "rebatch": cmd_rebatch, "add-company": cmd_add_company, "add": cmd_add}[args.cmd](args)


if __name__ == "__main__":
    main()
