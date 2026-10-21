"""Public, no-login job-board adapters. Standard library only; runs on Python 3.9+.

Every adapter returns postings in one shape:
  id, ats, slug, company, title, location, remote, url, apply_url, compensation,
  description, posted_at (ISO date or ""), industry, needs_detail
"""
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) job-agent/2.0 (personal job search)"
DESC_LIMIT = 8000
ATS_LIST = ["ashby", "greenhouse", "lever", "bamboohr", "breezy", "workable", "recruitee", "smartrecruiters",
            "rippling"]


class Gone(Exception):
    """The board doesn't exist (404/410 or a non-JSON marketing page)."""


def fetch(url, accept="application/json", timeout=20, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise Gone(url)
            last = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                continue
            raise
        except OSError as e:  # URLError, timeouts, resets
            last = e
            time.sleep(1 + attempt)
    raise last


def fetch_json(url, **kw):
    raw = fetch(url, **kw)
    try:
        return json.loads(raw)
    except ValueError:
        raise Gone(url)  # unknown subdomains often serve an HTML marketing page


def strip_html(s):
    s = html.unescape(html.unescape(s or ""))
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<(br|/p|/li|/h\d|/div|/tr)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def company_key(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def iso(value):
    """Epoch ms, epoch s, or a date string -> 'YYYY-MM-DD' (or '')."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(secs, timezone.utc).date().isoformat()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(value))
    return m.group(1) if m else ""


COUNTRY_CODES = {"ca": "Canada", "hk": "Hong Kong", "jp": "Japan", "us": "United States",
                 "gb": "United Kingdom", "sg": "Singapore", "au": "Australia", "cn": "China"}


def country_name(code_or_name):
    v = (code_or_name or "").strip()
    return COUNTRY_CODES.get(v.lower(), v)


def posting(**kw):
    base = dict(id="", source="ats", ats="", slug="", company="", title="", location="",
                remote=None, url="", apply_url="", compensation="", description="",
                posted_at="", industry="", needs_detail=False)
    base.update(kw)
    base["apply_url"] = base["apply_url"] or base["url"]
    base["description"] = (base["description"] or "")[:DESC_LIMIT]
    return base

# ---------- board URL -> (ats, slug) ----------

BOARD_PATTERNS = {
    "ashby": re.compile(r"^https?://jobs\.ashbyhq\.com/([^/?#]+)", re.I),
    "greenhouse": re.compile(r"^https?://(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([^/?#&]+)", re.I),
    "lever": re.compile(r"^https?://jobs\.lever\.co/([^/?#]+)", re.I),
    "bamboohr": re.compile(r"^https?://([a-z0-9-]+)\.bamboohr\.com(?:/|$)", re.I),
    "breezy": re.compile(r"^https?://([a-z0-9-]+)\.breezy\.hr(?:/|$)", re.I),
    "workable": re.compile(r"^https?://apply\.workable\.com/([^/?#]+)", re.I),
    "recruitee": re.compile(r"^https?://([a-z0-9-]+)\.recruitee\.com(?:/|$)", re.I),
    "smartrecruiters": re.compile(r"^https?://(?:jobs|careers)\.smartrecruiters\.com/([^/?#]+)", re.I),
    # ats.rippling.com/<slug>/jobs[/<id>], sometimes behind a locale: ats.rippling.com/en-GB/<slug>/jobs
    "rippling": re.compile(r"^https?://ats\.rippling\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?([^/?#]+)/jobs(?:[/?#]|$)", re.I),
}
RESERVED = {"api", "embed", "v1", "v0", "static", "assets", "j", "www", "app", "help", "support",
            "blog", "marketing", "jobs", "careers", "login", "signin", "oneclick-ui", "sitemap.xml",
            "robots.txt", "favicon.ico", "privacy", "terms", "search", "en", "de", "fr"}
SLUG_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def parse_board_url(url):
    for ats, pat in BOARD_PATTERNS.items():
        m = pat.match((url or "").strip())
        if m:
            slug = urllib.parse.unquote(m.group(1)).strip()
            if slug.lower() in RESERVED or not SLUG_OK.match(slug):
                return None
            return ats, slug
    return None

# ---------- listing adapters ----------

def greenhouse(slug):
    d = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    for j in d.get("jobs", []):
        url = j.get("absolute_url", "")
        yield posting(id=f"greenhouse:{slug}:{j['id']}", ats="greenhouse", slug=slug,
                      company=j.get("company_name") or slug, title=j.get("title", ""),
                      location=(j.get("location") or {}).get("name", ""), url=url,
                      description=strip_html(j.get("content", "")),
                      posted_at=iso(j.get("first_published") or j.get("updated_at")))


def lever(slug):
    d = fetch_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(d, list):  # unknown slugs answer 200 with {"ok": false, "error": "Document not found"}
        raise Gone(slug)
    for j in d:
        cats = j.get("categories") or {}
        loc = "; ".join(cats.get("allLocations") or []) or cats.get("location", "")
        sal = j.get("salaryRange") or {}
        comp = (f"{sal.get('currency', '')} {sal.get('min', '')}-{sal.get('max', '')} "
                f"{sal.get('interval', '')}").strip() if sal else ""
        parts = [j.get("descriptionPlain", "")]
        parts += [f"{l.get('text', '')}\n{strip_html(l.get('content', ''))}" for l in j.get("lists", [])]
        parts.append(j.get("additionalPlain", ""))
        yield posting(id=f"lever:{slug}:{j['id']}", ats="lever", slug=slug, company=slug,
                      title=j.get("text", ""), location=loc, remote=j.get("workplaceType") == "remote",
                      url=j.get("hostedUrl", ""), apply_url=j.get("applyUrl", ""), compensation=comp,
                      description="\n".join(parts), posted_at=iso(j.get("createdAt")))


def ashby(slug):
    d = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")]
        for s in j.get("secondaryLocations") or []:
            locs.append(s.get("location", "") if isinstance(s, dict) else str(s))
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        yield posting(id=f"ashby:{slug}:{j['id']}", ats="ashby", slug=slug, company=slug,
                      title=j.get("title", ""), location="; ".join(x for x in locs if x),
                      remote=bool(j.get("isRemote")), url=j.get("jobUrl", ""), apply_url=j.get("applyUrl", ""),
                      compensation=comp,
                      description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", "")),
                      posted_at=iso(j.get("publishedAt") or j.get("updatedAt")))


def bamboohr(slug):
    d = fetch_json(f"https://{slug}.bamboohr.com/careers/list")
    for j in d.get("result", []):
        loc, al = j.get("location") or {}, j.get("atsLocation") or {}
        parts = [loc.get("city") or al.get("city"), loc.get("state") or al.get("state") or al.get("province"),
                 al.get("country")]
        jid = j.get("id")
        yield posting(id=f"bamboohr:{slug}:{jid}", ats="bamboohr", slug=slug, company=slug,
                      title=j.get("jobOpeningName", ""), location=", ".join(p for p in parts if p),
                      remote=bool(j.get("isRemote")) or str(j.get("locationType")) == "1",
                      url=f"https://{slug}.bamboohr.com/careers/{jid}", needs_detail=True)


def breezy(slug):
    for j in fetch_json(f"https://{slug}.breezy.hr/json"):
        loc = j.get("location") or {}
        parts = [loc.get("city"), (loc.get("state") or {}).get("name"), (loc.get("country") or {}).get("name")]
        name = loc.get("name") or ", ".join(p for p in parts if p)
        yield posting(id=f"breezy:{slug}:{j.get('id')}", ats="breezy", slug=slug,
                      company=(j.get("company") or {}).get("name") or slug, title=j.get("name", ""),
                      location=name, remote=bool(loc.get("is_remote")), url=j.get("url", ""),
                      compensation=str(j.get("salary") or ""), posted_at=iso(j.get("published_date")),
                      needs_detail=True)


def workable(slug):
    d = fetch_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    company = d.get("name") or slug
    for j in d.get("jobs", []):
        locs = j.get("locations") or [{"city": j.get("city"), "region": j.get("state"), "country": j.get("country")}]
        loc = "; ".join(", ".join(x for x in (l.get("city"), l.get("region"), country_name(l.get("country") or l.get("countryCode"))) if x)
                        for l in locs if not l.get("hidden"))
        code = j.get("shortcode", "")
        yield posting(id=f"workable:{slug}:{code}", ats="workable", slug=slug, company=company,
                      title=j.get("title", ""), location=loc, remote=bool(j.get("telecommuting")),
                      url=j.get("url") or f"https://apply.workable.com/{slug}/j/{code}/",
                      apply_url=j.get("application_url", ""), description=strip_html(j.get("description", "")),
                      posted_at=iso(j.get("published_on") or j.get("created_at")), industry=j.get("industry") or "")


def recruitee(slug):
    d = fetch_json(f"https://{slug}.recruitee.com/api/offers/")
    for j in d.get("offers", []):
        sal = j.get("salary") or {}
        comp = (f"{sal.get('currency', '')} {sal.get('min', '')}-{sal.get('max', '')} {sal.get('period', '')}".strip()
                if isinstance(sal, dict) and (sal.get("min") or sal.get("max")) else "")
        loc = j.get("location") or ", ".join(x for x in (j.get("city"), j.get("country")) if x)
        yield posting(id=f"recruitee:{slug}:{j.get('id')}", ats="recruitee", slug=slug,
                      company=j.get("company_name") or slug, title=j.get("title", ""), location=loc,
                      remote=bool(j.get("remote")), url=j.get("careers_url", ""),
                      apply_url=j.get("careers_apply_url", ""), compensation=comp,
                      description=strip_html((j.get("description") or "") + "\n" + (j.get("requirements") or "")),
                      posted_at=iso(j.get("published_at") or j.get("created_at")))


def smartrecruiters(slug):
    offset, total = 0, None
    while total is None or (offset < total and offset < 500):
        d = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}")
        total = d.get("totalFound", 0)
        for j in d.get("content", []):
            loc = j.get("location") or {}
            where = loc.get("fullLocation") or ", ".join(
                x for x in (loc.get("city"), loc.get("region"), country_name(loc.get("country"))) if x)
            yield posting(id=f"smartrecruiters:{slug}:{j.get('id')}", ats="smartrecruiters", slug=slug,
                          company=(j.get("company") or {}).get("name") or slug, title=j.get("name", ""),
                          location=where, remote=bool(loc.get("remote")),
                          url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                          posted_at=iso(j.get("releasedDate")), industry=(j.get("industry") or {}).get("label", ""),
                          needs_detail=True, detail_ref=j.get("ref", ""))
        offset += 100
        if not d.get("content"):
            break


def rippling(slug):
    page, pages = 0, 1
    while page < pages and page < 10:
        d = fetch_json(f"https://ats.rippling.com/api/v2/board/{slug}/jobs?page={page}&pageSize=100")
        pages = d.get("totalPages") or 0
        for j in d.get("items", []):
            locs = j.get("locations") or []
            yield posting(id=f"rippling:{slug}:{j['id']}", ats="rippling", slug=slug, company=slug,
                          title=j.get("name", ""), location="; ".join(l["name"] for l in locs if l.get("name")),
                          remote=any(l.get("workplaceType") == "REMOTE" for l in locs),
                          url=j.get("url") or f"https://ats.rippling.com/{slug}/jobs/{j['id']}", needs_detail=True)
        page += 1


def rippling_pay(ranges):
    """payRangeDetails -> 'CAD 135000-225000 per year (CA); USD 177000-295000 per year (US)'.
    'per month' / 'per hour' wording is what max_annual_pay looks for."""
    def num(v):
        return f"{v:.0f}" if isinstance(v, (int, float)) else str(v or "")
    out = []
    for r in ranges or []:
        if isinstance(r, dict) and (r.get("rangeStart") or r.get("rangeEnd")):
            freq = str(r.get("frequency") or "").lower()
            per = f"per {freq}" if freq in ("year", "month", "hour") else freq
            where = f" ({r['location']})" if r.get("location") else ""
            out.append(f"{r.get('currency', '')} {num(r.get('rangeStart'))}-{num(r.get('rangeEnd'))} {per}".strip()
                       + where)
    return "; ".join(out)


LISTERS = {"ashby": ashby, "greenhouse": greenhouse, "lever": lever, "bamboohr": bamboohr,
           "breezy": breezy, "workable": workable, "recruitee": recruitee, "smartrecruiters": smartrecruiters,
           "rippling": rippling}

# ---------- detail fetchers (only for postings that already passed title + region) ----------

def fetch_detail(job):
    """Fill in description (and pay, when exposed) for sites whose listings omit it."""
    ats = job["ats"]
    try:
        if ats == "bamboohr":
            jid = job["id"].rsplit(":", 1)[1]
            d = fetch_json(f"https://{job['slug']}.bamboohr.com/careers/{jid}/detail")
            jo = (d.get("result") or {}).get("jobOpening") or {}
            job["description"] = strip_html(jo.get("description", ""))[:DESC_LIMIT]
            job["compensation"] = str(jo.get("compensation") or job["compensation"] or "")
            job["posted_at"] = job["posted_at"] or iso(jo.get("datePosted"))
        elif ats == "smartrecruiters" and job.get("detail_ref"):
            d = fetch_json(job["detail_ref"])
            secs = ((d.get("jobAd") or {}).get("sections") or {})
            text = "\n".join(strip_html((secs.get(k) or {}).get("text", ""))
                             for k in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"))
            job["description"] = text[:DESC_LIMIT]
        elif ats == "breezy":
            job["description"] = strip_html(fetch(job["url"], accept="text/html").decode("utf-8", "ignore"))[:DESC_LIMIT]
        elif ats == "rippling":
            jid = job["id"].rsplit(":", 1)[1]
            d = fetch_json(f"https://ats.rippling.com/api/v2/board/{job['slug']}/jobs/{jid}")
            desc = d.get("description") or {}
            job["description"] = strip_html(f"{desc.get('role', '')}\n{desc.get('company', '')}")[:DESC_LIMIT]
            job["compensation"] = rippling_pay(d.get("payRangeDetails")) or job["compensation"]
            job["posted_at"] = job["posted_at"] or iso(d.get("createdOn"))
    except Exception as e:  # keep the posting; the scorer sees an empty description
        job["detail_error"] = f"{type(e).__name__}"
    job["needs_detail"] = False
    return job


def company_about(ats, slug):
    """Company name and blurb where the site exposes one (used for the tech-company check)."""
    try:
        if ats == "greenhouse":
            d = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
            return {"name": d.get("name") or slug, "about": strip_html(d.get("content", ""))[:1500]}
        if ats == "workable":
            d = fetch_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
            return {"name": d.get("name") or slug, "about": strip_html(d.get("description", ""))[:1500]}
        if ats == "rippling":  # no board endpoint; every job carries the company blurb
            first = next(rippling(slug), None)
            if first:
                jid = first["id"].rsplit(":", 1)[1]
                d = fetch_json(f"https://ats.rippling.com/api/v2/board/{slug}/jobs/{jid}")
                return {"name": d.get("companyName") or slug,
                        "about": strip_html((d.get("description") or {}).get("company", ""))[:1500]}
    except Exception:
        pass
    return {}

# ---------- pay parsing ----------

CURRENCY_TOKENS = {
    "USD": r"(?:US\$|USD\b|(?<![A-Za-z])\$)",  # bare $, but not CA$, C$, HK$ or S$
    "CAD": r"(?:CA\$|C\$|CAD\b|\bCDN\b)",
    "HKD": r"(?:HK\$|HKD\b)",
    "JPY": r"(?:¥|JPY\b|円)",
    "GBP": r"(?:£|GBP\b)",
    "EUR": r"(?:€|EUR\b)",
    "DKK": r"(?:DKK\b)",                 # not bare "kr": SEK and NOK use it too
    "SGD": r"(?:(?<![A-Z])S\$|SGD\b)",   # S$, but not the tail of US$
}
SANE_ANNUAL = {"USD": (30_000, 1_500_000), "CAD": (30_000, 1_000_000), "HKD": (120_000, 12_000_000), "JPY": (2_000_000, 100_000_000),
               "GBP": (20_000, 1_000_000), "EUR": (20_000, 1_000_000), "DKK": (150_000, 8_000_000),
               "SGD": (30_000, 1_500_000)}
AMOUNT = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m|million|万)?(?![\w])", re.I)


def max_annual_pay(text, currency):
    """Highest annual amount stated in `currency` (explicitly marked), or None if unclear."""
    tok = CURRENCY_TOKENS.get(currency)
    if not tok or not text:
        return None
    best = None
    for line in re.split(r"[\n.;•]+(?=\s|$)|\n", text):
        if not re.search(tok, line, re.I):
            continue
        monthly = re.search(r"per\s+month|/\s*month|/\s*mo\b|monthly|a month", line, re.I)
        hourly = re.search(r"per\s+hour|/\s*h(?:ou)?r\b|hourly", line, re.I)
        if hourly:
            continue
        for num, mult in AMOUNT.findall(line):
            v = float(num.replace(",", ""))
            m = (mult or "").lower()
            v *= {"k": 1e3, "m": 1e6, "million": 1e6, "万": 1e4}.get(m, 1)
            if monthly:
                v *= 12
            lo, hi = SANE_ANNUAL[currency]
            if lo <= v <= hi:
                best = v if best is None else max(best, v)
    return best
