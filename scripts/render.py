"""Render docs/index.html from the computed scorecard. Plain HTML, Chart.js from a CDN."""
from __future__ import annotations

import html
import json
import os
import shutil
from pathlib import Path

CHART_JS = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"

CSS = """
:root { --fg:#1a1a1a; --dim:#5f6368; --line:#d9d9d9; --bg:#ffffff; --pos:#1e6b34; --neg:#a8261b; --warn:#fff4d6; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 28px 20px 48px; }
h1 { font-size: 24px; margin: 0 0 4px; font-weight: 650; }
h2 { font-size: 17px; margin: 34px 0 10px; font-weight: 650; }
p { margin: 6px 0; }
.dim { color: var(--dim); }
.small { font-size: 13px; }
.warn { background: var(--warn); border: 1px solid #e6c766; padding: 10px 12px; margin: 14px 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); border-top: 1px solid var(--line); border-left: 1px solid var(--line); margin-top: 14px; }
.cell { padding: 14px 16px; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.cell .lbl { font-size: 13px; color: var(--dim); }
.cell .big { font-size: 30px; font-weight: 650; font-variant-numeric: tabular-nums; margin: 2px 0; }
.cell .sub { font-size: 13px; color: var(--dim); font-variant-numeric: tabular-nums; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
th { font-weight: 600; color: var(--dim); font-size: 13px; border-bottom: 1px solid #999; }
th:first-child, td:first-child { text-align: left; }
td.name { text-align: left; white-space: normal; color: var(--dim); }
.pos { color: var(--pos); } .neg { color: var(--neg); }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 28px; }
@media (max-width: 800px) { .two { grid-template-columns: 1fr; } }
.chart { position: relative; height: 280px; }
footer { margin-top: 40px; padding-top: 14px; border-top: 1px solid var(--line); font-size: 13px; color: var(--dim); }
footer p { margin: 6px 0; }
a { color: #1a4f8b; }
"""


def f1(x, suffix="%", signed=False):
    if x is None or x == "":
        return "n/a"
    x = float(x)
    s = f"{x:+.1f}" if signed else f"{x:.1f}"
    return s + suffix


def money(x):
    if x is None:
        return "n/a"
    return f"-${abs(x):.2f}" if x < 0 else f"${x:.2f}"


def cls(x):
    if x is None:
        return ""
    return "pos" if x > 0 else ("neg" if x < 0 else "")


def esc(s):
    return html.escape(str(s if s is not None else ""))


def fdate(d):
    from datetime import date
    return date.fromisoformat(d).strftime("%B %-d, %Y") if d else ""


def sdate(d):
    from datetime import date
    return date.fromisoformat(d).strftime("%b %-d") if d else ""


def bench_txt(b, k, signed=False):
    a, c = b.get(k + "_5y"), b.get(k + "_10y")
    if not a and not c:
        return ""
    parts = []
    if a:
        parts.append("5-yr avg " + f1(a["value"], signed=signed))
    if c:
        parts.append("10-yr avg " + f1(c["value"], signed=signed))
    return '<div class="sub">' + ", ".join(parts) + "</div>"


def render_site(out: dict, hist: list, cfg: dict, docs: Path) -> None:
    docs.mkdir(parents=True, exist_ok=True)
    e, v, g = out["eps"], out["revenue"], out["growth"]
    fsb = out.get("factset") or {}
    fs = fsb.get("latest") or {}
    b = fsb.get("benchmarks") or {}
    band = out["config"]["meet_band_pct"]
    uni, rep = out["universe"], out["reported"]
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    season = esc(out["season"])

    # ------------------------------------------------ FactSet official (headline)
    if fs:
        fs_date = fdate(fs["file_date"])
        n = fs.get("reported_n")

        def cnt(k):
            return f" ({fs[k]} of {n})" if n and fs.get(k) is not None else ""

        def card(label, val, extra="", signed=False, missing="not stated in this report"):
            big = f1(val, signed=signed) if val is not None else "n/a"
            sub = "" if val is not None else f'<div class="sub">{missing}</div>'
            return f'<div class="cell"><div class="lbl">{label}</div><div class="big">{big}</div>{sub}{extra}</div>'

        rep_extra = (f'<div class="sub">{n} of 500 companies</div>' if n else "")
        gk = (fs.get("eps_growth_kind") or "blended")
        rk = (fs.get("rev_growth_kind") or "blended")
        fs_cards = "".join([
            card("Share of S&amp;P 500 reported", fs.get("reported_pct"), rep_extra),
            card("EPS above estimate", fs.get("eps_beat_pct"),
                 (f'<div class="sub">{fs["eps_beat_n"]} of {n} companies</div>' if fs.get("eps_beat_n") is not None else "")
                 + bench_txt(b, "eps_beat_pct")),
            card("Revenue above estimate", fs.get("rev_beat_pct"),
                 (f'<div class="sub">{fs["rev_beat_n"]} of {n} companies</div>' if fs.get("rev_beat_n") is not None else "")
                 + bench_txt(b, "rev_beat_pct")),
            card("Aggregate EPS surprise", fs.get("eps_surprise_pct"), bench_txt(b, "eps_surprise_pct", True), True),
            card("Aggregate revenue surprise", fs.get("rev_surprise_pct"), bench_txt(b, "rev_surprise_pct", True), True),
            card(f"{gk.capitalize()} EPS growth, y/y", fs.get("eps_growth_pct"), bench_txt(b, "eps_growth_pct", True), True),
            card(f"{rk.capitalize()} revenue growth, y/y", fs.get("rev_growth_pct"), bench_txt(b, "rev_growth_pct", True), True),
        ])
        notes = []
        if fs.get("derived_from_counts"):
            notes.append("This early in the season FactSet reports counts rather than percentages; the percentages "
                         "above are computed from those counts.")
        if "estimated" in (gk, rk):
            notes.append('FactSet labels growth "estimated" until reporting is under way and "blended" '
                         "(actuals plus estimates) after that.")
        srcs = sorted({(x["source"], x["url"]) for x in b.values()})
        if srcs:
            notes.append("Averages: " + "; ".join(f'<a href="{esc(u)}">{esc(sname)}</a>' for sname, u in srcs)
                         + ". Each average comes from the latest report that states it.")
        fs_warn = ""
        if fsb.get("status") == "stale":
            fs_warn = (f'<div class="warn"><b>FactSet figures may be stale.</b> {esc(fsb.get("stale_reason"))} '
                       f'Showing the report of {fs_date}.</div>')
        fs_section = f"""
<h2>FactSet official scorecard</h2>
<p>As of <a href="{esc(fs['url'])}">FactSet Earnings Insight, {fs_date}</a> (published weekly, usually Fridays).</p>
{fs_warn}
<div class="grid">{fs_cards}</div>
<p class="dim small">{' '.join(notes)}</p>"""
    else:
        fs_section = ('<h2>FactSet official scorecard</h2><div class="warn"><b>No FactSet report available yet.</b> '
                      f'{esc(fsb.get("stale_reason") or "")}</div>')

    # ------------------------------------------------ running estimate (Nasdaq / Zacks)
    warn = ""
    if out.get("status") in ("stale", "partial"):
        when = out.get("last_success_pt") or out.get("generated_at_pt")
        warn = (f'<div class="warn"><b>Running estimate may be stale.</b> {esc(out.get("stale_reason"))} '
                f'Figures below are from the last successful update ({esc(when)}).</div>')
    since = out.get("reported_since_factset")
    since_txt = (f'<div class="sub">{since} reported on or after {sdate(fs.get("file_date"))}</div>'
                 if since is not None and fs else "")
    if v.get("available"):
        rev_cell = (f'<div class="big">{f1(v["above_pct"])}</div>'
                    f'<div class="sub">plus or minus {band:g}% band: beat {f1(v["beat_pct"])}, meet {f1(v["meet_pct"])}, miss {f1(v["miss_pct"])}</div>'
                    f'<div class="sub">aggregate surprise {f1(v["agg_surprise_pct"], signed=True)} (n={v["n"]})</div>')
    else:
        rev_cell = ('<div class="big">n/a</div><div class="sub">' +
                    {"not_configured": "Revenue needs a FINNHUB_API_KEY repo secret.",
                     "failed": "Revenue source (Finnhub) failed on the last run."}.get(
                        v.get("status"), "Revenue source returned no matched results yet.") + "</div>")
    if g.get("available"):
        g_cell = (f'<div class="big">{f1(g["value_pct"], signed=True)}</div>'
                  f'<div class="sub">actuals for reporters, consensus for the rest; {g["coverage"]} of {uni} companies</div>')
    else:
        g_cell = (f'<div class="big">n/a</div><div class="sub">year-ago EPS or estimates available for '
                  f'{g.get("coverage", 0)} companies; needs {g.get("min_coverage")}</div>')
    run_cards = f"""
<div class="grid">
  <div class="cell"><div class="lbl">Companies reported</div><div class="big">{rep}</div>
    <div class="sub">of {uni} ({f1(100.0 * rep / uni if uni else None)})</div>{since_txt}</div>
  <div class="cell"><div class="lbl">EPS above consensus (FactSet definition)</div>
    <div class="big">{f1(e["above_pct"])}</div><div class="sub">{e["above"]} of {e["n"]} with a consensus</div>
    <div class="sub">plus or minus {band:g}% band: beat {f1(e["beat_pct"])}, meet {f1(e["meet_pct"])}, miss {f1(e["miss_pct"])}</div></div>
  <div class="cell"><div class="lbl">Aggregate EPS surprise</div>
    <div class="big">{f1(e["agg_surprise_pct"], signed=True)}</div>
    <div class="sub">median company {f1(e["median_surprise_pct"], signed=True)}</div>
    <div class="sub">share-weighted</div></div>
  <div class="cell"><div class="lbl">Revenue above consensus</div>{rev_cell}</div>
  <div class="cell"><div class="lbl">Blended EPS growth, y/y</div>{g_cell}</div>
</div>"""

    # ------------------------------------------------ comparison table
    def trow(label, fv, rv, key, signed=False):
        a, c = b.get(key + "_5y"), b.get(key + "_10y")
        return (f"<tr><td>{label}</td><td>{f1(fv, signed=signed)}</td><td>{f1(rv, signed=signed)}</td>"
                f"<td>{f1(a['value'], signed=signed) if a else 'n/a'}</td><td>{f1(c['value'], signed=signed) if c else 'n/a'}</td></tr>")
    comp = (trow("EPS above estimate", fs.get("eps_beat_pct"), e["above_pct"], "eps_beat_pct")
            + trow("Aggregate EPS surprise", fs.get("eps_surprise_pct"), e["agg_surprise_pct"], "eps_surprise_pct", True)
            + trow("Revenue above estimate", fs.get("rev_beat_pct"), v["above_pct"] if v.get("available") else None, "rev_beat_pct")
            + trow("Aggregate revenue surprise", fs.get("rev_surprise_pct"), v["agg_surprise_pct"] if v.get("available") else None, "rev_surprise_pct", True)
            + trow("EPS growth, y/y", fs.get("eps_growth_pct"), g["value_pct"] if g.get("available") else None, "eps_growth_pct", True)
            + trow("Revenue growth, y/y", fs.get("rev_growth_pct"), None, "rev_growth_pct", True))
    comp_html = f"""
<h2>Side by side</h2>
<table><tr><th>Measure</th><th>FactSet {sdate(fs.get('file_date')) if fs else ''}</th><th>Running estimate</th><th>FactSet 5-yr avg</th><th>FactSet 10-yr avg</th></tr>{comp}</table>
<p class="dim small">FactSet averages cover full seasons for all 500 companies; early readings from a few reporters are noisy.
"n/a" means the source does not state it (FactSet) or this repo does not compute it (revenue growth).</p>"""

    sec_rows = "".join(
        f"<tr><td>{esc(s['sector'])}</td><td>{s['reported']} / {s['companies']}</td>"
        f"<td>{f1(s['eps_above_pct'])}</td><td>{f1(s['eps_beat_pct'])}</td><td>{f1(s['eps_miss_pct'])}</td>"
        f"<td class='{cls(s['eps_agg_surprise_pct'])}'>{f1(s['eps_agg_surprise_pct'], signed=True)}</td>"
        f"<td>{f1(s['rev_beat_pct']) if s['rev_n'] else 'n/a'}</td></tr>"
        for s in out["sectors"])

    def top_table(rows, title):
        body = "".join(
            f"<tr><td>{esc(r['ticker'])}</td><td class='name'>{esc(r['name'])}</td><td>{esc(r['date'])}</td>"
            f"<td>{money(r['eps_est'])}</td><td>{money(r['eps_actual'])}</td>"
            f"<td class='{cls(r['eps_surprise_pct'])}'>{f1(r['eps_surprise_pct'], signed=True)}</td></tr>"
            for r in rows) or "<tr><td colspan='6' class='name'>None yet.</td></tr>"
        return (f"<div><h2>{title}</h2><table><tr><th>Ticker</th><th style='text-align:left'>Company</th>"
                f"<th>Reported</th><th>Est.</th><th>Actual</th><th>Surprise</th></tr>{body}</table></div>")

    recent = sorted([c for c in out["companies"] if c["eps_actual"] is not None],
                    key=lambda c: (c["date"] or "", c["ticker"]), reverse=True)
    rec_rows = "".join(
        f"<tr><td>{esc(c['ticker'])}</td><td class='name'>{esc(c['name'])}</td><td class='name'>{esc(c['sector'])}</td>"
        f"<td>{esc(c['date'])}</td><td>{money(c['eps_est'])}</td><td>{money(c['eps_actual'])}</td>"
        f"<td class='{cls(c['eps_surprise_pct'])}'>{f1(c['eps_surprise_pct'], signed=True)}</td>"
        f"<td class='{cls(c['rev_surprise_pct'])}'>{f1(c['rev_surprise_pct'], signed=True)}</td></tr>"
        for c in recent)

    # ------------------------------------------------ chart series (union of daily and weekly dates)
    fsh = [h for h in fsb.get("history", []) if h.get("eps_beat_pct") is not None]
    daily = {h["date"]: h for h in hist}
    weekly = {h["file_date"]: h for h in fsh}
    dates = sorted(set(daily) | set(weekly))
    series = {
        "dates": dates,
        "fs_beat": [weekly[d]["eps_beat_pct"] if d in weekly else None for d in dates],
        "fs_surp": [weekly[d].get("eps_surprise_pct") if d in weekly else None for d in dates],
        "nq_above": [num(daily[d]["eps_above_pct"]) if d in daily else None for d in dates],
        "nq_agg": [num(daily[d]["eps_agg_surprise_pct"]) if d in daily else None for d in dates],
        "b": {k: (b[k]["value"] if k in b else None) for k in
              ("eps_beat_pct_5y", "eps_beat_pct_10y", "eps_surprise_pct_5y", "eps_surprise_pct_10y")},
    }
    has_replay = any(h.get("method") == "replay" for h in hist)
    no_fs_surp = fsh and all(h.get("eps_surprise_pct") is None for h in fsh)

    rev_src = ("Revenue actuals and estimates for the running estimate: Finnhub earnings calendar." if out["sources"].get("revenue")
               else "Revenue in the running estimate: not configured (add a FINNHUB_API_KEY secret to enable).")
    repo_link = (f' Code and methodology: <a href="https://github.com/{esc(repo)}">github.com/{esc(repo)}</a>.'
                 if repo else "")
    fs_foot = (f'<p>Headline figures: <a href="{esc(fs["url"])}">FactSet Earnings Insight, {fdate(fs["file_date"])}</a>, '
               f'by John Butters, FactSet Research Systems. FactSet counts a beat as an actual above the mean estimate.</p>'
               if fs else "")

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>S&amp;P 500 {season} Earnings: details</title>
<style>{CSS}</style>
</head>
<body>
<main>
<p class="small"><a href="index.html">Back to the summary</a></p>
<h1>S&amp;P 500 {season} Earnings: details</h1>
<p class="dim">Official numbers from FactSet's weekly Earnings Insight, plus a daily running estimate between reports.
Last updated {esc(out['generated_at_pt'])}.</p>
{fs_section}
<h2>Running estimate since FactSet's last report</h2>
<p class="dim small">Not FactSet. Built daily from company-level results on the Nasdaq earnings calendar (Zacks consensus),
counting every S&amp;P 500 reporter this season. Uses FactSet's beat definition (actual above consensus by any amount);
the plus or minus {band:g}% beat / meet / miss split is shown as detail. Expect gaps versus FactSet because the consensus differs.</p>
{warn}
{run_cards}
{comp_html}
<h2>Season to date</h2>
<div class="two">
  <div><div class="chart"><canvas id="c1"></canvas></div><p class="dim small">Share of reporters with EPS above estimate.</p></div>
  <div><div class="chart"><canvas id="c2"></canvas></div><p class="dim small">Aggregate EPS surprise.{' FactSet does not state an aggregate surprise this early in the season.' if no_fs_surp else ''}</p></div>
</div>
<p class="dim small">Dark points: FactSet, one per weekly report. Light line: running estimate, one point per day{' (days before the first scheduled run were rebuilt from report dates)' if has_replay else ''}. Dashed: FactSet 5-yr and 10-yr averages.</p>
<h2>By sector (GICS), running estimate</h2>
<table><tr><th>Sector</th><th>Reported</th><th>Above est.</th><th>Beat (band)</th><th>Miss (band)</th><th>Agg. EPS surprise</th><th>Revenue beat</th></tr>{sec_rows}</table>
<div class="two">
{top_table(out["top_beats"], "Top 10 EPS beats")}
{top_table(out["top_misses"], "Top 10 EPS misses")}
</div>
<p class="dim small">Running estimate data. Ranked by EPS surprise percent; companies with a consensus below ${out['config']['rank_min_abs_estimate']:.2f} a share are left out of the ranking because tiny estimates inflate percentages.</p>
<h2>All reporters</h2>
<table><tr><th>Ticker</th><th style="text-align:left">Company</th><th style="text-align:left">Sector</th><th>Reported</th><th>EPS est.</th><th>EPS actual</th><th>EPS surprise</th><th>Revenue surprise</th></tr>{rec_rows}</table>

<footer>
<p>Last updated {esc(out['generated_at_pt'])}. The page rebuilds twice each weekday; FactSet's report is checked on every run.</p>
{fs_foot}
<p>Running estimate: EPS actuals and consensus from the Nasdaq earnings calendar (api.nasdaq.com), which republishes Zacks
consensus; shares outstanding for weighting from the Nasdaq stock screener. {rev_src} S&amp;P 500 membership and GICS sectors:
<a href="https://github.com/datasets/s-and-p-500-companies">datasets/s-and-p-500-companies</a>.</p>
<p>Method (running estimate): a company is in the {season} season if its fiscal quarter ends in {esc(', '.join(out['config']['fiscal_quarter_endings']))}.
Aggregate surprise weights EPS by shares outstanding (total earnings dollars), as FactSet does.{repo_link}</p>
<p>Raw data: <a href="data/q3_2026.json">q3_2026.json</a>, <a href="data/history.csv">history.csv</a>, <a href="data/factset.json">factset.json</a>. Not investment advice.</p>
</footer>
</main>
<script src="{CHART_JS}"></script>
<script>
const S = {json.dumps(series)};
const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const LBL = S.dates.map(d => MON[+d.slice(5, 7) - 1] + ' ' + (+d.slice(8, 10)));
const ref = (v, label) => v == null ? null : ({{ label, data: S.dates.map(() => v), borderColor: '#9aa0a6', borderDash: [5, 4], borderWidth: 1, pointRadius: 0, fill: false }});
const official = (label, data) => ({{ label, data, borderColor: '#1a1a1a', backgroundColor: '#1a1a1a', borderWidth: 2, pointRadius: 4, pointHoverRadius: 5, spanGaps: true }});
const light = (label, data) => ({{ label, data, borderColor: '#8fb0d6', backgroundColor: '#8fb0d6', borderWidth: 1.5, pointRadius: 1.5, spanGaps: true }});
const opts = (yl) => ({{ responsive: true, maintainAspectRatio: false, animation: false,
  interaction: {{ mode: 'index', intersect: false }},
  plugins: {{ legend: {{ labels: {{ boxWidth: 14, font: {{ size: 12 }} }} }} }},
  scales: {{ y: {{ title: {{ display: true, text: yl }}, grid: {{ color: '#eeeeee' }} }}, x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 10, maxRotation: 0 }} }} }} }});
if (window.Chart) {{
  new Chart(document.getElementById('c1'), {{ type: 'line', data: {{ labels: LBL, datasets: [
    official('FactSet (weekly)', S.fs_beat), light('Running estimate (daily)', S.nq_above),
    ref(S.b.eps_beat_pct_5y, 'FactSet 5-yr avg'), ref(S.b.eps_beat_pct_10y, 'FactSet 10-yr avg')
  ].filter(Boolean) }}, options: opts('% of reporters') }});
  new Chart(document.getElementById('c2'), {{ type: 'line', data: {{ labels: LBL, datasets: [
    official('FactSet (weekly)', S.fs_surp), light('Running estimate (daily)', S.nq_agg),
    ref(S.b.eps_surprise_pct_5y, 'FactSet 5-yr avg'), ref(S.b.eps_surprise_pct_10y, 'FactSet 10-yr avg')
  ].filter(Boolean) }}, options: opts('% vs estimate') }});
}}
</script>
</body>
</html>
"""
    (docs / "details.html").write_text(page)
    render_index(out, hist, docs)
    d = docs / "data"
    d.mkdir(exist_ok=True)
    root = docs.parent / "data"
    for name in ("q3_2026.json", "history.csv", "factset.json"):
        if (root / name).exists():
            shutil.copyfile(root / name, d / name)


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ====================================================================== one-screen summary (index.html)
ACCENT = "#2563a8"

INDEX_CSS = """
:root { --fg:#1f2328; --dim:#6b7280; --faint:#9ca3af; --line:#e5e7eb; --acc:#2563a8; --acc2:#a9c1e0; }
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body { background: #fff; color: var(--fg); font: 14px/1.4 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
a { color: var(--acc); text-decoration: none; }
a:hover { text-decoration: underline; }
.wrap { height: 100vh; max-width: 1360px; margin: 0 auto; padding: 20px 32px 14px; display: flex; flex-direction: column; gap: 18px; }
header { display: flex; align-items: flex-end; justify-content: space-between; gap: 32px; flex-wrap: wrap; }
h1 { font-size: 22px; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
.meta { font-size: 12.5px; color: var(--dim); margin-top: 3px; }
.prog { width: 340px; max-width: 100%; }
.prog .lbl { font-size: 12.5px; color: var(--dim); display: flex; justify-content: space-between; margin-bottom: 5px; }
.prog .lbl b { color: var(--fg); font-weight: 600; }
.bar { height: 8px; background: var(--line); display: flex; }
.bar span { display: block; height: 100%; background: var(--acc); }
.bar span.since { background: var(--acc2); }
.warn { font-size: 12.5px; color: #7a5b00; border-left: 3px solid #d4a400; padding: 2px 10px; }
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.tile { padding: 14px 20px 14px 0; }
.tile + .tile { padding-left: 20px; border-left: 1px solid var(--line); }
.tile .t { font-size: 12.5px; color: var(--dim); }
.tile .v { font-size: 40px; font-weight: 600; line-height: 1.15; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; margin: 2px 0 2px; }
.tile .v.na { color: var(--faint); font-size: 30px; line-height: 1.5; }
.tile .s { font-size: 12.5px; color: var(--dim); font-variant-numeric: tabular-nums; }
.tile .c { font-size: 12.5px; color: var(--fg); font-variant-numeric: tabular-nums; margin-top: 1px; }
.charts { flex: 1 1 auto; min-height: 0; display: grid; grid-template-columns: 1.4fr 1fr; gap: 40px; }
.panel { display: flex; flex-direction: column; min-height: 0; }
.col { display: flex; flex-direction: column; gap: 22px; min-height: 0; }
.col .panel.grow { flex: 1 1 auto; }
.pt { font-size: 13.5px; font-weight: 600; }
.pn { font-size: 12px; color: var(--dim); margin: 2px 0 8px; }
.key { display: inline-flex; align-items: center; gap: 6px; margin-right: 14px; white-space: nowrap; }
.sw { display: inline-block; width: 18px; height: 0; border-top: 2px solid var(--acc); }
.sw.dot { border-top: 2px dotted var(--faint); }
.sw.dash { border-top: 1px dashed var(--dim); }
.cv { position: relative; flex: 1 1 auto; min-height: 0; }
.cv canvas { position: absolute; inset: 0; }
.stack { display: flex; height: 22px; margin-top: 4px; }
.stack span { display: block; height: 100%; }
.stack .b { background: var(--acc); } .stack .m { background: #c7ccd4; } .stack .x { background: #6b7280; }
.slbl { display: flex; gap: 18px; font-size: 12.5px; margin-top: 6px; font-variant-numeric: tabular-nums; flex-wrap: wrap; }
.slbl i { display: inline-block; width: 9px; height: 9px; margin-right: 5px; vertical-align: 0; }
footer { font-size: 11.5px; color: var(--dim); border-top: 1px solid var(--line); padding-top: 8px; line-height: 1.5; }
@media (max-height: 760px) {
  .wrap { gap: 14px; padding-top: 16px; }
  .tile { padding-top: 10px; padding-bottom: 10px; }
  .tile .v { font-size: 34px; }
  .tile .v.na { font-size: 26px; }
}
@media (max-width: 900px), (max-height: 540px) {
  .wrap { height: auto; padding: 16px 16px 20px; }
  .charts { grid-template-columns: 1fr; gap: 26px; }
  .cv { flex: none; height: 240px; }
  .charts > .panel .cv { height: 260px; }
}
@media (max-width: 640px) {
  .tiles { grid-template-columns: 1fr 1fr; }
  .tile { padding: 12px 12px 12px 0; }
  .tile + .tile { padding-left: 12px; }
  .tile:nth-child(3) { padding-left: 0; border-left: 0; border-top: 1px solid var(--line); }
  .tile:nth-child(4) { border-top: 1px solid var(--line); }
  .tile .v { font-size: 32px; }
  .prog { width: 100%; }
}
"""

SECTOR_SHORT = {
    "Communication Services": "Comm. Services", "Consumer Discretionary": "Cons. Discretionary",
    "Consumer Staples": "Cons. Staples", "Information Technology": "Info. Technology",
}


def _pct(x, signed=False, dec=1):
    if x is None:
        return None
    x = float(x)
    s = f"{x:+.{dec}f}" if signed else f"{x:.{dec}f}"
    if s.endswith(".0") and abs(x) >= 10:
        s = s[:-2]
    return s + "%"


def render_index(out: dict, hist: list, docs: Path) -> None:
    from datetime import date
    fsb = out.get("factset") or {}
    fs = fsb.get("latest") or {}
    b = fsb.get("benchmarks") or {}
    e = out["eps"]
    band = out["config"]["meet_band_pct"]
    uni = out["universe"] or 500
    season = esc(out["season"])
    fs_short = date.fromisoformat(fs["file_date"]).strftime("%b %-d") if fs else ""
    upd = esc(out.get("generated_at_pt") or "")

    def avg(k, signed=False):
        a = b.get(k + "_5y")
        return f"5-yr avg {_pct(a['value'], signed)}" if a else "5-yr avg not available"

    # ---------------------------------------------------------------- header / progress
    n_fs = fs.get("reported_n")
    since = out.get("reported_since_factset") or 0
    rep_main = n_fs if n_fs is not None else out["reported"]
    w1 = 100.0 * rep_main / uni
    w2 = 100.0 * since / uni if fs else 0.0
    since_txt = f" + {since} since {fs_short} (running count)" if fs and since else ""
    src_link = (f'<a href="{esc(fs["url"])}">FactSet Earnings Insight, {fs_short}</a>' if fs
                else "FactSet Earnings Insight not available")

    # ---------------------------------------------------------------- tiles
    def tile(title, val, sub, comp, signed=False, na_txt="n/a yet"):
        v = _pct(val, signed)
        vh = f'<div class="v">{v}</div>' if v else f'<div class="v na">{na_txt}</div>'
        return (f'<div class="tile"><div class="t">{title}</div>{vh}'
                f'<div class="s">{sub}</div><div class="c">{comp}</div></div>')

    def of_n(k):
        return f"{fs[k]} of {n_fs} reporters" if fs.get(k) is not None and n_fs else "&nbsp;"

    gk = (fs.get("eps_growth_kind") or "blended")
    surp_sub = (f"not in the {fs_short} report" if fs else "no FactSet report")
    if fs.get("eps_surprise_pct") is None and e.get("agg_surprise_pct") is not None:
        surp_sub += f"; running est. {_pct(e['agg_surprise_pct'], True)}"
    tiles = "".join([
        tile("EPS beat rate", fs.get("eps_beat_pct"), of_n("eps_beat_n"), avg("eps_beat_pct")),
        tile("Revenue beat rate", fs.get("rev_beat_pct"), of_n("rev_beat_n"), avg("rev_beat_pct")),
        tile(f"EPS growth, y/y ({esc(gk)})", fs.get("eps_growth_pct"), f"{season} vs a year earlier",
             avg("eps_growth_pct", True), signed=True),
        tile("Aggregate EPS surprise", fs.get("eps_surprise_pct"), surp_sub, avg("eps_surprise_pct", True), signed=True),
    ])

    # ---------------------------------------------------------------- beat / meet / miss
    if fs.get("eps_equal_pct") is not None and fs.get("eps_below_pct") is not None:
        bm = [("Above", fs.get("eps_beat_pct")), ("In line", fs.get("eps_equal_pct")), ("Below", fs.get("eps_below_pct"))]
        bm_note = f"FactSet, {fs_short}: actual EPS vs mean estimate."
    else:
        bm = [("Beat", e.get("beat_pct")), ("Meet", e.get("meet_pct")), ("Miss", e.get("miss_pct"))]
        bm_note = (f"Running estimate (Nasdaq/Zacks, not FactSet), {e.get('n', 0)} reporters. "
                   f"Meet means within {band:g}% of consensus.")
    cls_ = ("b", "m", "x")
    stack = "".join(f'<span class="{c}" style="width:{(v or 0):.2f}%"></span>' for c, (_, v) in zip(cls_, bm))
    colors = (ACCENT, "#c7ccd4", "#6b7280")
    slbl = "".join(f'<span><i style="background:{col}"></i>{lab} <b>{_pct(v) or "n/a"}</b></span>'
                   for col, (lab, v) in zip(colors, bm))

    # ---------------------------------------------------------------- series
    fsh = [h for h in fsb.get("history", []) if h.get("eps_beat_pct") is not None]
    daily = {h["date"]: h for h in hist}
    weekly = {h["file_date"]: h for h in fsh}
    dates = sorted(set(daily) | set(weekly))
    a5 = b.get("eps_beat_pct_5y")
    secs = sorted([s for s in out["sectors"] if s.get("eps_n")],
                  key=lambda s: (-(s["eps_above_pct"] or 0), s["sector"]))
    series = {
        "dates": dates,
        "fs": [weekly[d]["eps_beat_pct"] if d in weekly else None for d in dates],
        "nq": [num(daily[d]["eps_above_pct"]) if d in daily else None for d in dates],
        "avg5": a5["value"] if a5 else None,
        "sec_lbl": [f"{SECTOR_SHORT.get(s['sector'], s['sector'])} ({s['eps_n']})" for s in secs],
        "sec_val": [s["eps_above_pct"] for s in secs],
    }
    has_nq = any(x is not None for x in series["nq"])
    legend = ('<span class="key"><span class="sw"></span>FactSet, weekly</span>'
              + ('<span class="key"><span class="sw dot"></span>Running estimate, daily</span>' if has_nq else "")
              + (f'<span class="key"><span class="sw dash"></span>FactSet 5-yr avg ({_pct(a5["value"])})</span>' if a5 else ""))

    # ---------------------------------------------------------------- warnings + footer
    warns = []
    if fsb.get("status") == "stale":
        warns.append(f"FactSet figures may be stale: {esc(fsb.get('stale_reason'))} Showing the {fs_short} report.")
    if not fs:
        warns.append(f"No FactSet report available yet. {esc(fsb.get('stale_reason') or '')}")
    if out.get("status") in ("stale", "partial"):
        warns.append(f"Running estimate may be stale: {esc(out.get('stale_reason'))} "
                     f"Last good update {esc(out.get('last_success_pt') or '')}.")
    warn_html = "".join(f'<div class="warn">{w}</div>' for w in warns)
    avg_srcs = sorted({x["source"].replace("FactSet Earnings Insight, ", "") for x in b.values()})
    fs_cite = (f'Source: <a href="{esc(fs["url"])}">FactSet Earnings Insight, {fdate(fs["file_date"])}</a> '
               f'(John Butters, FactSet). Beat = actual above the mean estimate. ' if fs else "")
    avg_cite = (f"5-yr averages from FactSet Earnings Insight ({'; '.join(esc(x) for x in avg_srcs)}). " if avg_srcs else "")

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>S&amp;P 500 {season} Earnings</title>
<style>{INDEX_CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1>S&amp;P 500 {season} Earnings</h1>
    <div class="meta">{src_link} | updated {upd}</div>
  </div>
  <div class="prog">
    <div class="lbl"><span><b>{rep_main} of {uni}</b> companies reported{since_txt}</span><span>{_pct(w1 + w2)}</span></div>
    <div class="bar"><span style="width:{w1:.2f}%"></span><span class="since" style="width:{w2:.2f}%"></span></div>
  </div>
</header>
{warn_html}
<section class="tiles">{tiles}</section>
<section class="charts">
  <div class="col">
    <div class="panel grow">
      <div class="pt">EPS beat rate, season to date</div>
      <div class="pn">{legend}</div>
      <div class="cv"><canvas id="c1" aria-label="EPS beat rate by week"></canvas></div>
    </div>
    <div class="panel">
      <div class="pt">Beat, meet, miss</div>
      <div class="pn">{bm_note}</div>
      <div class="stack">{stack}</div>
      <div class="slbl">{slbl}</div>
    </div>
  </div>
  <div class="panel">
    <div class="pt">EPS beat rate by sector</div>
    <div class="pn">Running estimate, not FactSet. Sectors with reporters only; (n) = companies reported.</div>
    <div class="cv"><canvas id="c3" aria-label="EPS beat rate by sector"></canvas></div>
  </div>
</section>
<footer>
{fs_cite}{avg_cite}Running estimate: company results from the Nasdaq earnings calendar (Zacks consensus), not FactSet.<br>
<a href="details.html">Details, tables and methodology</a> | <a href="data/q3_2026.json">Raw data</a> | Not investment advice.
</footer>
</div>
<script src="{CHART_JS}"></script>
<script>
const S = {json.dumps(series)};
const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const DAY = 86400000;
const X = S.dates.map(d => Date.UTC(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10)) / DAY);
const xy = arr => arr.map((v, i) => ({{ x: X[i], y: v }})).filter(p => p.y !== null);
const fmtDay = v => {{ const t = new Date(v * DAY); return MON[t.getUTCMonth()] + ' ' + t.getUTCDate(); }};
if (window.Chart) {{
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 11;
  Chart.defaults.color = '#6b7280';
  const pct = v => v + '%';
  const tip = {{ callbacks: {{ label: c => c.dataset.label + ': ' + (c.parsed.y ?? c.parsed.x).toFixed(1) + '%' }} }};
  const ds = [
    {{ label: 'FactSet', data: xy(S.fs), borderColor: '{ACCENT}', backgroundColor: '{ACCENT}', borderWidth: 2, pointRadius: 3.5, spanGaps: true, clip: false }},
  ];
  if (S.nq.some(v => v !== null)) ds.push({{ label: 'Running estimate', data: xy(S.nq), borderColor: '#9ca3af', borderDash: [2, 3], borderWidth: 1.5, pointRadius: 0, spanGaps: true }});
  if (S.avg5 !== null) ds.push({{ label: 'FactSet 5-yr avg', data: X.length ? [{{ x: X[0], y: S.avg5 }}, {{ x: X[X.length - 1], y: S.avg5 }}] : [], borderColor: '#6b7280', borderDash: [5, 4], borderWidth: 1, pointRadius: 0 }});
  new Chart(document.getElementById('c1'), {{ type: 'line', data: {{ datasets: ds }},
    options: {{ responsive: true, maintainAspectRatio: false, animation: false, layout: {{ padding: {{ top: 6, right: 8 }} }},
      interaction: {{ mode: 'nearest', axis: 'x', intersect: false }},
      plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ title: it => it.length ? fmtDay(it[0].parsed.x) : '', label: tip.callbacks.label }} }} }},
      scales: {{ y: {{ min: 0, max: 100, ticks: {{ stepSize: 25, callback: pct }}, grid: {{ color: '#f0f1f3' }}, border: {{ display: false }} }},
                x: {{ type: 'linear', min: X[0], max: X[X.length - 1], grid: {{ display: false }}, ticks: {{ stepSize: 7, maxRotation: 0, callback: fmtDay }} }} }} }} }});
  const valueLabels = {{ id: 'valueLabels', afterDatasetsDraw(ch) {{
    const c = ch.ctx; c.save(); c.font = '11px ' + Chart.defaults.font.family; c.fillStyle = '#1f2328'; c.textBaseline = 'middle';
    ch.getDatasetMeta(0).data.forEach((bar, i) => {{ const v = ch.data.datasets[0].data[i];
      if (v === null) return; const t = v.toFixed(0) + '%'; const w = c.measureText(t).width;
      if (bar.x + 6 + w > ch.chartArea.right) {{ c.fillStyle = '#fff'; c.fillText(t, bar.x - w - 6, bar.y); c.fillStyle = '#1f2328'; }}
      else c.fillText(t, bar.x + 6, bar.y); }}); c.restore(); }} }};
  new Chart(document.getElementById('c3'), {{ type: 'bar',
    data: {{ labels: S.sec_lbl, datasets: [{{ label: 'Beat rate', data: S.sec_val, backgroundColor: '{ACCENT}', barThickness: 'flex', maxBarThickness: 18 }}] }},
    plugins: [valueLabels],
    options: {{ indexAxis: 'y', responsive: true, maintainAspectRatio: false, animation: false,
      plugins: {{ legend: {{ display: false }}, tooltip: tip }},
      scales: {{ x: {{ min: 0, max: 100, ticks: {{ stepSize: 50, callback: pct }}, grid: {{ color: '#f0f1f3' }}, border: {{ display: false }} }},
                y: {{ grid: {{ display: false }}, border: {{ display: false }} }} }} }} }});
}}
</script>
</body>
</html>
"""
    (docs / "index.html").write_text(page)


if __name__ == "__main__":
    # Re-render from the saved data without fetching anything: python scripts/render.py
    import csv
    root = Path(__file__).resolve().parent.parent
    out = json.loads((root / "data" / "q3_2026.json").read_text())
    hp = root / "data" / "history.csv"
    hist = list(csv.DictReader(hp.open())) if hp.exists() else []
    cfg = json.loads((root / "config.json").read_text())
    render_site(out, hist, cfg, root / "docs")
    print("rendered docs/index.html and docs/details.html")
