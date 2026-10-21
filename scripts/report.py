#!/usr/bin/env python3
"""Write data/report.html: every job the agent has touched, by status, city and date.

  python3 scripts/report.py        # then publish data/report.html (or open it in a browser)

Reads data/tracker.csv, data/queue.json, the latest data/runs/census-*.md and config/rules.toml.
Read-only on all of them.
"""
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10 or older: bundled copy
    sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "report.html"


def census():
    files = sorted(f for f in (DATA / "runs").glob("census-*.md") if "-partial" not in f.name)
    if not files:
        return {}
    text = files[-1].read_text()
    seen = re.search(r"Postings seen: (\d+)", text)
    lines = [l for l in text.splitlines() if l.startswith("|")]
    stages = {}
    if lines:
        regions = [c.strip() for c in lines[0].strip("|").split("|")][1:]
        for l in lines[2:]:
            cells = [c.strip() for c in l.strip("|").split("|")]
            stages[cells[0]] = dict(zip(regions, (int(x) if x.isdigit() else 0 for x in cells[1:])))
    return {"file": files[-1].name, "seen": int(seen.group(1)) if seen else None, "stages": stages,
            "kind": "full sweep" if "full sweep" in text.splitlines()[0] else "daily"}


def main():
    if not (ROOT / "config" / "rules.toml").exists():
        sys.exit("config/rules.toml is missing. Copy config/rules.example.toml to config/rules.toml and fill it in.")
    rules = tomllib.loads((ROOT / "config" / "rules.toml").read_text())
    if not (DATA / "tracker.csv").exists():
        sys.exit("No data/tracker.csv yet. Run /apply-run (or a dry run) first.")
    with open(DATA / "tracker.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    queue = json.loads((DATA / "queue.json").read_text()) if (DATA / "queue.json").exists() else []
    tracked = {r["job_id"] for r in rows}
    by_id = {j["id"]: j for j in queue}
    jobs = []
    for r in rows:
        q = by_id.get(r["job_id"], {})
        ats = r["job_id"].split(":")[0] if r["job_id"].count(":") >= 2 else "imported"
        jobs.append({"id": r["job_id"], "company": r["company"] or q.get("company", ""), "title": r["title"],
                     "region": r["region"] or q.get("region", ""), "status": r["status"],
                     "score": r["score"], "note": r["note"], "url": r["url"] or q.get("url", ""),
                     "proof": Path(r["proof"]).name if r["proof"] else "", "ats": ats,
                     "updated": r["updated_at"][:16].replace("T", " ")})
    unscored = [{"id": j["id"], "company": j.get("company", ""), "title": j.get("title", ""),
                 "region": j.get("region", ""), "status": "unscored", "score": "", "note": "Found, waiting to be scored",
                 "url": j.get("url", ""), "proof": "", "ats": j.get("ats", ""), "updated": j.get("discovered_at", "")}
                for j in queue if j["id"] not in tracked]
    regions = []
    for name in rules["run"]["region_order"]:
        g = rules["regions"].get(name, {})
        regions.append({"key": name, "currency": g.get("salary_currency", ""), "floor": g.get("salary_floor", 0),
                        "resume": Path(g.get("resume", "")).name or "?",
                        "sponsor": rules.get("answers", {}).get(name, {}).get("requires_sponsorship", "")})
    payload = {"generated": datetime.now().astimezone().strftime("%a %d %b %Y, %H:%M"),
               "jobs": jobs + unscored, "regions": regions, "census": census(),
               "cap": rules["run"].get("max_submissions_per_run")}
    # "<" as < keeps job text from closing the <script> block; it's still valid JSON
    OUT.write_text(TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")))
    print(f"Wrote {OUT} ({len(jobs)} tracked jobs, {len(unscored)} waiting to be scored)")


TEMPLATE = r"""<title>Application Ledger</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: one column ledger. Summary strip, then "needs you", then cities, then the filterable job list. */
:root {
  --bg: #f4f6f9; --surface: #ffffff; --ink: #18202d; --muted: #5a6475; --line: #dde2ea; --accent: #2c55c9;
  --ok: #1d7a52; --ok-bg: #e3f3eb; --wait: #2c55c9; --wait-bg: #e6ecfb; --warn: #9a6100; --warn-bg: #fbf0d9;
  --bad: #b5361c; --bad-bg: #fae6e0; --dim: #6b7383; --dim-bg: #eceff3;
  --display: "Bricolage Grotesque", "Avenir Next", "Segoe UI", sans-serif;
  --body: "IBM Plex Sans", "Helvetica Neue", Arial, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0f131a; --surface: #161c26; --ink: #e5e9f1; --muted: #9aa3b4; --line: #283142; --accent: #86a6ff;
  --ok: #5fd39f; --ok-bg: #16362a; --wait: #86a6ff; --wait-bg: #1c2848; --warn: #f2c264; --warn-bg: #3a2d12;
  --bad: #ff9378; --bad-bg: #3d1d16; --dim: #a1a9b8; --dim-bg: #222a37; color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #0f131a; --surface: #161c26; --ink: #e5e9f1; --muted: #9aa3b4; --line: #283142; --accent: #86a6ff;
  --ok: #5fd39f; --ok-bg: #16362a; --wait: #86a6ff; --wait-bg: #1c2848; --warn: #f2c264; --warn-bg: #3a2d12;
  --bad: #ff9378; --bad-bg: #3d1d16; --dim: #a1a9b8; --dim-bg: #222a37; color-scheme: dark; }
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--ink); font: 15px/1.5 var(--body); }
.wrap { max-width: 1120px; margin: 0 auto; padding-inline: 20px; padding-block: 28px 56px; display: grid; gap: 28px; }
h1, h2 { font-family: var(--display); text-wrap: balance; margin: 0; letter-spacing: -0.01em; }
h1 { font-size: clamp(26px, 4vw, 34px); font-weight: 700; }
h2 { font-size: 19px; font-weight: 700; }
.sub { color: var(--muted); margin: 4px 0 0; }
.label { font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); font-weight: 600; }
.num { font-variant-numeric: tabular-nums; }
.mono { font-family: var(--mono); font-size: 12.5px; }
header { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: end; gap: 12px; }
.stamp { color: var(--muted); font-size: 13px; }
.strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
.stat { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px;
  display: grid; gap: 2px; text-align: left; font: inherit; color: inherit; cursor: pointer; }
.stat:hover, .stat:focus-visible { border-color: var(--accent); outline: none; }
.stat b { font-family: var(--display); font-size: 30px; font-weight: 700; line-height: 1.1; }
.stat small { color: var(--muted); font-size: 12.5px; }
.stat .k { display: flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 600; }
.dot { width: 9px; height: 9px; border-radius: 50%; flex: none; }
.funnel { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; color: var(--muted); font-size: 13.5px; }
.funnel b { color: var(--ink); font-weight: 600; }
.funnel i { font-style: normal; opacity: .6; }
section { display: grid; gap: 12px; min-width: 0; }
.needs { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 10px; }
.need { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; display: grid; gap: 6px; min-width: 0; }
.need .who { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; }
.need .who strong { font-weight: 600; }
.need p { margin: 0; color: var(--muted); font-size: 13.5px; overflow-wrap: anywhere; }
.cities { overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.07em; color: var(--muted); font-weight: 600; white-space: nowrap; background: var(--surface); }
tr:last-child td { border-bottom: 0; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
.pill { display: inline-flex; align-items: center; gap: 6px; padding: 2px 9px; border-radius: 999px; font-size: 12.5px; font-weight: 600; white-space: nowrap; }
.s-submitted { color: var(--ok); background: var(--ok-bg); }
.s-shortlisted, .s-in_progress, .s-reopened, .s-unscored { color: var(--wait); background: var(--wait-bg); }
.s-needs_input { color: var(--warn); background: var(--warn-bg); }
.s-blocked, .s-failed { color: var(--bad); background: var(--bad-bg); }
.s-skipped, .s-dry_run_ok { color: var(--dim); background: var(--dim-bg); }
.controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.chip { border: 1px solid var(--line); background: var(--surface); color: var(--ink); border-radius: 999px; padding: 5px 12px; font: 500 13px var(--body); cursor: pointer; }
.chip[aria-pressed="true"] { background: var(--ink); color: var(--bg); border-color: var(--ink); }
.chip:focus-visible, input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
input[type="search"] { flex: 1 1 220px; min-width: 0; padding: 7px 12px; border-radius: 8px; border: 1px solid var(--line); background: var(--surface); color: var(--ink); font: 14px var(--body); }
.list { overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); }
.list td .t { font-weight: 500; }
.list td .c { color: var(--muted); font-size: 13px; }
.list td.note { color: var(--muted); font-size: 13px; max-width: 420px; overflow-wrap: anywhere; }
a { color: var(--accent); }
.empty { padding: 20px; color: var(--muted); }
.count { color: var(--muted); font-size: 13px; }
@media (max-width: 720px) { .hide-sm { display: none; } .list td.note { max-width: 220px; } }
@media (prefers-reduced-motion: no-preference) { .stat { transition: border-color .15s; } }
</style>

<div class="wrap">
  <header>
    <div>
      <h1>Application Ledger</h1>
      <p class="sub">Every job the agent has found, scored, applied to or set aside.</p>
    </div>
    <div class="stamp" id="stamp"></div>
  </header>

  <div class="strip" id="strip"></div>
  <div class="funnel" id="funnel"></div>

  <section id="needs-sec">
    <h2>Needs you</h2>
    <p class="sub" style="margin:0">Questions to answer in rules.toml, and forms to finish yourself (CAPTCHA, cover letter, AI-use pledges).</p>
    <div class="needs" id="needs"></div>
  </section>

  <section>
    <h2>By city</h2>
    <div class="cities"><table id="cities"></table></div>
  </section>

  <section>
    <h2>All jobs</h2>
    <div class="controls" id="status-chips"></div>
    <div class="controls" id="region-chips"></div>
    <div class="controls"><input type="search" id="q" placeholder="Search company, title or reason" aria-label="Search jobs"><span class="count" id="count"></span></div>
    <div class="list"><table id="jobs"></table></div>
  </section>
</div>

<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
const STATUS = {
  submitted: ["Applied", "--ok"], shortlisted: ["Queued to apply", "--wait"], in_progress: ["Applying now", "--wait"],
  needs_input: ["Needs your answer", "--warn"], blocked: ["Apply yourself", "--bad"], failed: ["Failed", "--bad"],
  skipped: ["Skipped", "--dim"], reopened: ["Re-scoring", "--wait"], unscored: ["Waiting to be scored", "--wait"],
  dry_run_ok: ["Dry run", "--dim"]
};
// region keys from rules.toml: new_york -> New York
const city = r => r ? r.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()) : "—";
const el = (tag, props = {}, kids = []) => { const e = document.createElement(tag); Object.assign(e, props); kids.forEach(k => e.append(k)); return e; };
const pill = s => { const [lab] = STATUS[s] || [s]; return el("span", { className: "pill s-" + s, textContent: lab }); };
const fmt = n => n == null ? "—" : Number(n).toLocaleString("en");

const jobs = D.jobs;
const live = jobs.filter(j => !j.id.startsWith("seed:") || j.status === "submitted");
const count = s => jobs.filter(j => j.status === s).length;
const dayAgo = Date.now() - 864e5;
const recent = jobs.filter(j => j.status === "submitted" && Date.parse(j.updated.replace(" ", "T") + "Z") > dayAgo).length;

document.getElementById("stamp").textContent = "Updated " + D.generated;

// summary strip: each tile filters the list
const tiles = [
  ["submitted", count("submitted"), recent + " in the last 24 hours"],
  ["shortlisted", count("shortlisted") + count("in_progress"), "scored and waiting their turn"],
  ["needs_input", count("needs_input"), "questions only you can answer"],
  ["blocked", count("blocked"), "CAPTCHA, cover letter, other blocks"],
  ["unscored", count("unscored") + count("reopened"), "found, not scored yet"],
  ["skipped", count("skipped"), "scored out: fit, seniority, language"],
];
const strip = document.getElementById("strip");
tiles.forEach(([s, n, small]) => {
  const [lab, tok] = STATUS[s];
  const b = el("button", { className: "stat", type: "button", id: "tile-" + s });
  const dot = el("span", { className: "dot" }); dot.style.background = `var(${tok})`;
  b.append(el("span", { className: "k" }, [dot, lab]), el("b", { className: "num", textContent: fmt(n) }), el("small", { textContent: small }));
  b.addEventListener("click", () => { state.status = s; render(); document.getElementById("jobs").scrollIntoView({ behavior: "smooth", block: "start" }); });
  strip.append(b);
});

// discovery funnel from the latest census
const C = D.census || {}, st = C.stages || {};
const total = k => Object.values(st[k] || {}).reduce((a, b) => a + b, 0);
if (C.seen) {
  const f = document.getElementById("funnel");
  const steps = [["postings checked", C.seen], ["in your cities", total("in your cities")], ["matching titles", total("title match")],
                 ["candidates", total("candidates")], ["applied, all time", count("submitted")]];
  f.append(el("span", { className: "label", textContent: "Last discovery (" + C.kind + ")" }));
  steps.forEach(([lab, n], i) => { if (i) f.append(el("i", { textContent: "→" })); f.append(el("span", {}, [el("b", { className: "num", textContent: fmt(n) }), " " + lab])); });
}

// needs you
const needs = document.getElementById("needs");
const needRows = jobs.filter(j => j.status === "needs_input" || j.status === "blocked").sort((a, b) => a.status.localeCompare(b.status));
needRows.forEach(j => {
  const who = el("div", { className: "who" }, [el("strong", { textContent: j.company + " · " + city(j.region) }), pill(j.status)]);
  const card = el("div", { className: "need" }, [who, el("div", { textContent: j.title }), el("p", { textContent: j.note })]);
  if (j.url) card.append(el("a", { href: j.url, target: "_blank", rel: "noopener", textContent: "Open posting" }));
  needs.append(card);
});
if (!needRows.length) needs.append(el("div", { className: "empty", textContent: "Nothing waiting on you." }));

// by city
const ct = document.getElementById("cities");
const head = ["City", "Applied", "Queued", "Needs you", "Skipped", "Candidates", "Pay floor", "Resume", "Sponsorship"];
ct.append(el("thead", {}, [el("tr", {}, head.map((h, i) => el("th", { className: i && i < 6 ? "n" : "", textContent: h })))]));
const tb = el("tbody");
D.regions.forEach(r => {
  const of = s => jobs.filter(j => j.region === r.key && (Array.isArray(s) ? s.includes(j.status) : j.status === s)).length;
  const cand = (st["candidates"] || {})[r.key];
  const tr = el("tr", {}, [
    el("td", { textContent: city(r.key) }),
    el("td", { className: "n", textContent: of("submitted") }),
    el("td", { className: "n", textContent: of(["shortlisted", "in_progress", "unscored", "reopened"]) }),
    el("td", { className: "n", textContent: of(["needs_input", "blocked"]) }),
    el("td", { className: "n", textContent: of("skipped") }),
    el("td", { className: "n", textContent: cand == null ? "—" : cand }),
    el("td", { className: "mono", textContent: r.floor ? r.currency + " " + fmt(r.floor) : "none" }),
    el("td", { textContent: r.resume }),
    el("td", { textContent: r.sponsor === "Yes" ? "Needed" : r.sponsor === "No" ? "Not needed" : "—" }),
  ]);
  tb.append(tr);
});
ct.append(tb);

// job list with filters
const state = { status: "all", region: "all", q: "" };
try { const saved = JSON.parse(localStorage.getItem("ledger-filters") || "{}"); Object.assign(state, saved); } catch (e) {}
const statusOrder = ["all", "submitted", "shortlisted", "needs_input", "blocked", "unscored", "skipped", "failed"];
const sc = document.getElementById("status-chips"), rc = document.getElementById("region-chips");
statusOrder.forEach(s => {
  const n = s === "all" ? jobs.length : s === "shortlisted" ? count("shortlisted") + count("in_progress") : s === "unscored" ? count("unscored") + count("reopened") : count(s);
  const b = el("button", { className: "chip", type: "button", id: "st-" + s, textContent: (s === "all" ? "All" : STATUS[s][0]) + " " + n });
  b.addEventListener("click", () => { state.status = s; render(); });
  sc.append(b);
});
["all", ...D.regions.map(r => r.key)].forEach(r => {
  const b = el("button", { className: "chip", type: "button", id: "rg-" + r, textContent: r === "all" ? "All cities" : city(r) });
  b.addEventListener("click", () => { state.region = r; render(); });
  rc.append(b);
});
const q = document.getElementById("q");
q.value = state.q || "";
q.addEventListener("input", () => { state.q = q.value; render(); });

const jt = document.getElementById("jobs");
const rank = { in_progress: 0, shortlisted: 1, needs_input: 2, blocked: 3, submitted: 4, unscored: 5, reopened: 5, failed: 6, skipped: 7 };
function render() {
  try { localStorage.setItem("ledger-filters", JSON.stringify(state)); } catch (e) {}
  sc.querySelectorAll(".chip").forEach(b => b.setAttribute("aria-pressed", b.id === "st-" + state.status));
  rc.querySelectorAll(".chip").forEach(b => b.setAttribute("aria-pressed", b.id === "rg-" + state.region));
  const needle = (state.q || "").toLowerCase();
  const match = j => (state.status === "all" || j.status === state.status
      || (state.status === "shortlisted" && j.status === "in_progress") || (state.status === "unscored" && j.status === "reopened"))
    && (state.region === "all" || j.region === state.region)
    && (!needle || (j.company + " " + j.title + " " + j.note).toLowerCase().includes(needle));
  const rows = jobs.filter(match).sort((a, b) => (rank[a.status] ?? 9) - (rank[b.status] ?? 9) || b.updated.localeCompare(a.updated));
  document.getElementById("count").textContent = rows.length + " shown";
  jt.replaceChildren();
  jt.append(el("thead", {}, [el("tr", {}, [
    el("th", { textContent: "Job" }), el("th", { textContent: "City" }), el("th", { textContent: "Status" }),
    el("th", { className: "n hide-sm", textContent: "Score" }), el("th", { textContent: "Reason / proof" }),
    el("th", { className: "hide-sm", textContent: "Updated" })])]));
  const body = el("tbody");
  rows.forEach(j => {
    const job = el("td", {}, [el("div", { className: "t" }, [j.url ? el("a", { href: j.url, target: "_blank", rel: "noopener", textContent: j.title || "(untitled)" }) : (j.title || "(untitled)")]),
                             el("div", { className: "c", textContent: j.company + (j.ats && j.ats !== "imported" ? " · " + j.ats : "") })]);
    const note = j.status === "submitted" ? (j.proof ? "Confirmation saved: " + j.proof : "Imported from earlier tracker") : j.note;
    body.append(el("tr", {}, [job, el("td", { textContent: city(j.region) }), el("td", {}, [pill(j.status)]),
      el("td", { className: "n hide-sm", textContent: j.score || "" }), el("td", { className: "note", textContent: note }),
      el("td", { className: "mono hide-sm", textContent: j.updated })]));
  });
  jt.append(body);
  if (!rows.length) jt.append(el("tbody", {}, [el("tr", {}, [el("td", { colSpan: 6, className: "empty", textContent: "No jobs match these filters." })])]));
}
render();
</script>
"""

if __name__ == "__main__":
    main()
