"""FactSet Earnings Insight: find the latest weekly PDF and parse the headline scorecard.

FactSet publishes the report as a free PDF, usually on Fridays, at
  https://go.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/EarningsInsight_MMDDYY.pdf
(advantage.factset.com serves the same path). Nothing here invents numbers: a field that the
report does not state is left as None. When FactSet gives counts instead of percentages (very
early in a season), the percentage is computed from those counts and flagged as derived.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta, timezone

import requests

HOSTS = ("https://go.factset.com", "https://advantage.factset.com")
PATH = "/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/EarningsInsight_{d}.pdf"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
ORDINAL = {"1": "first", "2": "second", "3": "third", "4": "fourth"}
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
NUM = r"(-?\d+(?:\.\d+)?)"


def url_for(d: date, host: str = HOSTS[0]) -> str:
    return host + PATH.format(d=d.strftime("%m%d%y"))


def fetch_pdf(d: date, session: requests.Session | None = None):
    """Returns (url, bytes) for the report dated d, or (None, None) if it does not exist."""
    s = session or requests
    for host in HOSTS:
        u = url_for(d, host)
        try:
            r = s.get(u, headers=UA, timeout=40)
        except requests.RequestException:
            continue
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            return u.replace(host, HOSTS[0]), r.content
    return None, None


def pdf_text(data: bytes, max_pages: int = 16) -> str:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = pdf.pages[:max_pages]
        return "\n".join((p.extract_text() or "") for p in pages)


def normalize(t: str) -> str:
    t = t.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(\d+)\s*-\s*year", r"\1-year", t)      # "5- year", "10 -year"
    t = re.sub(r"year\s*-\s*over\s*-\s*year", "year-over-year", t)
    t = re.sub(r"Q\s*([1-4])\s+(20\d\d)", r"Q\1 \2", t)  # "Q 3 2026"
    t = re.sub(r"compan\s+ies", "companies", t)
    return t


def _f(x):
    return float(x) if x is not None else None


def _averages(chunk: str, paren: bool = False):
    """Pull '5-year average of 78%' / '5-year average (78%)' and the 10-year one from a sentence."""
    if paren:
        a = re.search(r"5-year average \(" + NUM + r"%\)", chunk)
        b = re.search(r"10-year average \(" + NUM + r"%\)", chunk)
    else:
        a = re.search(r"5-year average(?: [a-z ]+?)? of " + NUM + "%", chunk)
        b = re.search(r"10-year average(?: [a-z ]+?)? of " + NUM + "%", chunk)
    return (_f(a.group(1)) if a else None, _f(b.group(1)) if b else None)


def _sentence_after(t: str, start: int, n: int = 700) -> str:
    return t[start:start + n]


def parse(text: str, season: str = "Q3 2026") -> dict:
    """Parse the headline numbers for `season` (e.g. 'Q3 2026'). Missing fields stay None."""
    t = normalize(text)
    qn, year = season[1], season.split()[1]
    qword = ORDINAL[qn]
    qpat = rf"(?:Q{qn} {year}|the {qword} quarter)"
    out = {"season": season, "report_date": None,
           "reported_pct": None, "reported_n": None,
           "eps_beat_pct": None, "eps_beat_n": None, "eps_equal_pct": None, "eps_below_pct": None,
           "rev_beat_pct": None, "rev_beat_n": None,
           "eps_surprise_pct": None, "rev_surprise_pct": None,
           "eps_growth_pct": None, "eps_growth_kind": None,
           "rev_growth_pct": None, "rev_growth_kind": None,
           "derived_from_counts": False,
           "avg": {}}

    m = re.search(rf"({MONTHS}) (\d{{1,2}}), (20\d\d)", t)
    if m:
        out["report_date"] = datetime.strptime(" ".join(m.groups()), "%B %d %Y").date().isoformat()

    # Key Metrics scorecard: percentages mid-season, counts very early in the season
    m = re.search(rf"Earnings Scorecard: For Q{qn} {year} \(with {NUM}(%| S&P 500 compan(?:ies|y))[^)]*\),"
                  rf" {NUM}(%| S&P 500 compan(?:ies|y))[^.]*?positive EPS surprise and {NUM}(%| S&P 500 compan(?:ies|y))"
                  r"[^.]*?positive revenue surprise", t)
    if m:
        rep, rep_u, eb, eb_u, rb, rb_u = m.groups()
        if rep_u == "%":
            out["reported_pct"] = _f(rep)
        else:
            out["reported_n"] = int(float(rep))
            out["reported_pct"] = round(int(float(rep)) / 500 * 100, 1)
            out["derived_from_counts"] = True
        if eb_u == "%":
            out["eps_beat_pct"] = _f(eb)
        elif out["reported_n"]:
            out["eps_beat_n"] = int(float(eb))
            out["eps_beat_pct"] = round(out["eps_beat_n"] / out["reported_n"] * 100, 1)
            out["derived_from_counts"] = True
        if rb_u == "%":
            out["rev_beat_pct"] = _f(rb)
        elif out["reported_n"]:
            out["rev_beat_n"] = int(float(rb))
            out["rev_beat_pct"] = round(out["rev_beat_n"] / out["reported_n"] * 100, 1)
            out["derived_from_counts"] = True

    in_season = out["eps_beat_pct"] is not None

    # Commentary: "Overall, X% of the companies in the S&P 500 have reported actual results for Q3 2026
    # to date. Of these companies, Y% have reported actual EPS above estimates, which is ... 5-year ..."
    if in_season:
        m = re.search(rf"Overall, {NUM}% of the companies in the S&P 500 have reported actual results for "
                      rf"Q{qn} {year} to date\. Of these companies, {NUM}% have reported actual EPS above estimates", t)
        if m:
            out["reported_pct"] = out["reported_pct"] if out["reported_pct"] is not None else _f(m.group(1))
            out["avg"]["eps_beat_pct_5y"], out["avg"]["eps_beat_pct_10y"] = _averages(_sentence_after(t, m.end(), 220))
        # Scorecard section: "Of these companies, 88% ... above the mean EPS estimate, 6% ... equal ..., 6% ... below"
        m = re.search(rf"reported earnings to date for {qpat}\. Of these companies, {NUM}% have reported actual EPS "
                      rf"above the mean EPS estimate, {NUM}% have reported actual EPS equal to the mean EPS estimate, "
                      rf"and {NUM}% have reported actual EPS below the mean EPS estimate", t)
        if m:
            out["eps_equal_pct"], out["eps_below_pct"] = _f(m.group(2)), _f(m.group(3))
            chunk = _sentence_after(t, m.end(), 400)
            a5, a10 = _averages(chunk, paren=True)
            out["avg"].setdefault("eps_beat_pct_5y", a5)
            out["avg"].setdefault("eps_beat_pct_10y", a10)
            if out["avg"].get("eps_beat_pct_5y") is None:
                out["avg"]["eps_beat_pct_5y"], out["avg"]["eps_beat_pct_10y"] = a5, a10
        # "In aggregate, companies are reporting earnings that are 16.4% above estimates, which is ..."
        m = re.search(r"In aggregate, companies are reporting earnings that are " + NUM + r"% (above|below) estimates", t)
        if m:
            v = _f(m.group(1))
            out["eps_surprise_pct"] = -v if m.group(2) == "below" else v
            a5, a10 = _averages(_sentence_after(t, m.end(), 220))
            out["avg"]["eps_surprise_pct_5y"], out["avg"]["eps_surprise_pct_10y"] = a5, a10
        # "In terms of revenues, 85% of S&P 500 companies have reported actual revenues above estimates, ..."
        m = re.search(r"In terms of revenues, " + NUM + r"% of S&P 500 companies have reported actual revenues above estimates", t)
        if m:
            if out["rev_beat_pct"] is None:
                out["rev_beat_pct"] = _f(m.group(1))
            out["avg"]["rev_beat_pct_5y"], out["avg"]["rev_beat_pct_10y"] = _averages(_sentence_after(t, m.end(), 220))
        m = re.search(r"In aggregate, companies are reporting revenues that are " + NUM + r"% (above|below) (?:the )?estimates", t)
        if m:
            v = _f(m.group(1))
            out["rev_surprise_pct"] = -v if m.group(2) == "below" else v
            a5, a10 = _averages(_sentence_after(t, m.end(), 220))
            out["avg"]["rev_surprise_pct_5y"], out["avg"]["rev_surprise_pct_10y"] = a5, a10

    # Earnings growth (Key Metrics): "For Q3 2026, the blended|estimated (year-over-year) earnings growth rate
    # for the S&P 500 is 29.5%"
    m = re.search(rf"Earnings Growth: For Q{qn} {year}, the (blended|estimated) \(year-over-year\) earnings growth "
                  rf"rate for the S&P 500 is {NUM}%", t)
    if m:
        out["eps_growth_kind"], out["eps_growth_pct"] = m.group(1), _f(m.group(2))
    m = re.search(rf"The (blended|estimated) \(year-over-year\) earnings growth rate for Q{qn} {year} is {NUM}%", t)
    if m:
        if out["eps_growth_pct"] is None:
            out["eps_growth_kind"], out["eps_growth_pct"] = m.group(1), _f(m.group(2))
        out["avg"]["eps_growth_pct_5y"], out["avg"]["eps_growth_pct_10y"] = _averages(_sentence_after(t, m.end(), 260))

    # Revenue growth
    for pat, kind in (
        (rf"The (blended|estimated) \(year-over-year\) revenue growth rate for Q{qn} {year} is {NUM}%", None),
        (rf"the (blended) revenue growth rate for {qpat} is {NUM}%", None),
        (rf"the S&P 500 is expected to report \(year-over-year\) revenue growth of {NUM}%", "estimated"),
    ):
        m = re.search(pat, t)
        if not m:
            continue
        if kind:
            g_kind, val = kind, m.group(1)
        else:
            g_kind, val = m.group(1), m.group(m.lastindex)
        out["rev_growth_kind"], out["rev_growth_pct"] = g_kind, _f(val)
        if "revenue growth rate for Q" in pat:
            out["avg"]["rev_growth_pct_5y"], out["avg"]["rev_growth_pct_10y"] = _averages(_sentence_after(t, m.end(), 260))
        break

    out["avg"] = {k: v for k, v in out["avg"].items() if v is not None}
    out["in_season"] = in_season
    return out


def latest(season: str, today: date, known_dates: set[str], lookback_days: int = 21, stop_at: str | None = None):
    """Walk back from `today` one day at a time (Fridays are the usual day, but holiday weeks move it)
    and return (date, url, parsed, error) for the newest report found. Dates already parsed
    (`known_dates`) are not downloaded again: that returns (date, url, None, None)."""
    s = requests.Session()
    for i in range(lookback_days + 1):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        if stop_at and d.isoformat() < stop_at:
            break
        if d.isoformat() in known_dates:
            return d, url_for(d), None, None
        u, data = fetch_pdf(d, s)
        if not data:
            continue
        try:
            p = parse(pdf_text(data), season)
            p["url"], p["file_date"] = u, d.isoformat()
            p["fetched_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return d, u, p, None
        except Exception as e:  # parsing problem: report it, caller keeps last good values
            return d, u, None, f"{e.__class__.__name__}: {e}"
    return None, None, None, "no report found in the last %d days" % lookback_days
