#!/usr/bin/env python3
"""Fetch S&P 500 earnings results for the configured season and write
data/q3_2026.json, data/history.csv and docs/index.html.

Sources
  Headline (no key): FactSet Earnings Insight weekly PDF (official season scorecard)
  EPS (no key):      Nasdaq earnings calendar, api.nasdaq.com/api/calendar/earnings (daily running estimate)
  Shares (no key):   Nasdaq stock screener (market cap / last sale)
  Revenue (key):     Finnhub earnings calendar, only if FINNHUB_API_KEY is set
  Constituents:      github.com/datasets/s-and-p-500-companies (cached in data/)

If a source fails, cached data is kept and the page shows a stale warning.
"""
from __future__ import annotations

import csv
import io
import json
import os
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")

NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings"
SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
FINNHUB_URL = "https://finnhub.io/api/v1/calendar/earnings"
NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n")


def load_config() -> dict:
    cfg = load_json(ROOT / "config.json", {})
    for key, env in (("window_start", "WINDOW_START"), ("window_end", "WINDOW_END")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    if os.environ.get("MEET_BAND_PCT"):
        cfg["meet_band_pct"] = float(os.environ["MEET_BAND_PCT"])
    return cfg


def norm_sym(s: str) -> str:
    return s.strip().upper().replace("/", ".").replace("-", ".")


def money(v) -> float | None:
    """'$3.29' -> 3.29, '($0.01)' -> -0.01, 'N/A' or '' -> None."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.upper() in ("N/A", "NA", "--", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("$", "").replace(",", "").strip()
    if s.startswith("-"):
        neg, s = True, s[1:]
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


# ---------------------------------------------------------------- constituents

def load_constituents(cfg: dict) -> list[dict]:
    path = DATA / "sp500_constituents.csv"
    try:
        r = requests.get(cfg["constituents_url"], timeout=30)
        r.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(r.text)))
        if len(rows) >= 490 and {"Symbol", "GICS Sector", "CIK"} <= set(rows[0].keys()):
            path.write_text(r.text if r.text.endswith("\n") else r.text + "\n")
            log(f"constituents: refreshed ({len(rows)} tickers)")
        else:
            log("constituents: download looked wrong, using cache")
    except requests.RequestException as e:
        log(f"constituents: download failed ({e}), using cache")
    return list(csv.DictReader(path.open()))


def build_universe(rows: list[dict]) -> tuple[dict, dict]:
    """Returns (companies keyed by CIK, symbol -> CIK). Share classes collapse to one company."""
    companies: dict[str, dict] = {}
    sym2cik: dict[str, str] = {}
    for r in rows:
        sym = norm_sym(r["Symbol"])
        cik = (r.get("CIK") or sym).strip().lstrip("0") or sym
        sym2cik[sym] = cik
        c = companies.setdefault(cik, {"cik": cik, "ticker": sym, "symbols": [],
                                       "name": r["Security"].replace("\u2013", "-").replace("\u2014", "-"), "sector": r["GICS Sector"]})
        c["symbols"].append(sym)
    return companies, sym2cik


# ---------------------------------------------------------------- Nasdaq

class Nasdaq:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(NASDAQ_HEADERS)
        self.ok = 0
        self.fail = 0
        self.consecutive_fail = 0

    def get(self, url: str, params: dict, timeout: int = 25):
        last = None
        for attempt in range(3):
            try:
                r = self.s.get(url, params=params, timeout=timeout)
                if r.status_code == 200:
                    j = r.json()
                    if j.get("data") is not None or j.get("status", {}).get("rCode") == 200:
                        self.ok += 1
                        self.consecutive_fail = 0
                        return j
                last = f"HTTP {r.status_code}"
            except (requests.RequestException, ValueError) as e:
                last = str(e)[:120]
            time.sleep(1.5 * (attempt + 1))
        self.fail += 1
        self.consecutive_fail += 1
        log(f"nasdaq: failed {params} ({last})")
        return None

    def calendar(self, d: date):
        j = self.get(NASDAQ_URL, {"date": d.isoformat()})
        if j is None:
            return None
        return ((j.get("data") or {}).get("rows")) or []


def refresh_calendar(cfg: dict, sp_syms: set[str], today: date) -> dict:
    """Update data/nasdaq_days.json (S&P 500 rows only, keyed by date). Returns stats."""
    cache_path = DATA / "nasdaq_days.json"
    cache = load_json(cache_path, {})
    nq = Nasdaq()
    plan = []
    windows = [
        (date.fromisoformat(cfg["window_start"]), date.fromisoformat(cfg["window_end"])),
        (date.fromisoformat(cfg["yearago_window_start"]), date.fromisoformat(cfg["yearago_window_end"])),
    ]
    recent = today - timedelta(days=int(cfg.get("refetch_days_back", 10)))
    for start, end in windows:
        for d in weekdays(start, end):
            k = d.isoformat()
            if k not in cache or d >= recent:
                plan.append(d)
    log(f"nasdaq: {len(plan)} calendar days to fetch")
    fetched = 0
    for d in plan:
        if nq.consecutive_fail >= 6:
            log("nasdaq: 6 consecutive failures, stopping (source likely blocked); cached days kept")
            break
        rows = nq.calendar(d)
        if rows is None:
            continue
        keep = []
        for r in rows:
            sym = norm_sym(r.get("symbol", ""))
            if sym in sp_syms:
                r = dict(r)
                r["symbol"] = sym
                keep.append(r)
        cache[d.isoformat()] = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "rows": keep}
        fetched += 1
        time.sleep(0.35)
    write_json(cache_path, cache)
    return {"planned": len(plan), "fetched": fetched, "failed": nq.fail, "cache": cache}


def refresh_shares(sp_syms: set[str]) -> dict:
    path = DATA / "shares.json"
    cached = load_json(path, {})
    nq = Nasdaq()
    j = nq.get(SCREENER_URL, {"tableonly": "true", "limit": "10000", "offset": "0", "download": "true"}, timeout=60)
    rows = ((j or {}).get("data") or {}).get("rows") or []
    out = {}
    for r in rows:
        sym = r.get("symbol", "")
        if "^" in sym:
            continue
        sym = norm_sym(sym)
        if sym not in sp_syms:
            continue
        mc, px = money(r.get("marketCap")), money(r.get("lastsale"))
        if mc and px and px > 0:
            out[sym] = round(mc / px)
    if len(out) >= 450:
        write_json(path, {"as_of": date.today().isoformat(), "shares": out})
        log(f"shares: {len(out)} S&P 500 tickers from Nasdaq screener")
        return out
    log(f"shares: screener returned {len(out)} usable rows, using cache")
    return cached.get("shares", {})


# ---------------------------------------------------------------- Finnhub (optional, revenue)

def refresh_finnhub(cfg: dict, sp_syms: set[str], today: date) -> dict:
    """Returns {"status": str, "rows": {SYM|date: row}}; cache persists rows already seen."""
    path = DATA / "finnhub_rows.json"
    cache = load_json(path, {})
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key:
        return {"status": "not_configured", "rows": cache}
    start = max(date.fromisoformat(cfg["window_start"]), today - timedelta(days=28))
    end = min(today + timedelta(days=1), date.fromisoformat(cfg["window_end"]))
    ok = fail = 0
    d = start
    while d <= end:
        e = min(d + timedelta(days=6), end)
        try:
            r = requests.get(FINNHUB_URL, params={"from": d.isoformat(), "to": e.isoformat()},
                             headers={"X-Finnhub-Token": key}, timeout=30)
            if r.status_code == 200:
                for row in r.json().get("earningsCalendar", []) or []:
                    sym = norm_sym(row.get("symbol", ""))
                    if sym in sp_syms and row.get("date"):
                        cache[f"{sym}|{row['date']}"] = {k: row.get(k) for k in (
                            "symbol", "date", "hour", "quarter", "year", "epsActual",
                            "epsEstimate", "revenueActual", "revenueEstimate")}
                ok += 1
            else:
                fail += 1
                log(f"finnhub: HTTP {r.status_code} for {d}..{e}")
        except (requests.RequestException, ValueError) as ex:
            fail += 1
            log(f"finnhub: {ex.__class__.__name__} for {d}..{e}")
        d = e + timedelta(days=1)
        time.sleep(1.1)
    write_json(path, cache)
    status = "ok" if ok and not fail else ("partial" if ok else "failed")
    log(f"finnhub: {ok} ok, {fail} failed, {len(cache)} cached rows")
    return {"status": status, "rows": cache}



# ---------------------------------------------------------------- FactSet (headline standard)

BENCH_KEYS = ("eps_beat_pct", "eps_surprise_pct", "rev_beat_pct", "rev_surprise_pct",
              "eps_growth_pct", "rev_growth_pct")


def _fs_label(d: str) -> str:
    return "FactSet Earnings Insight, " + date.fromisoformat(d).strftime("%B %-d, %Y")


def refresh_factset(cfg: dict, today: date) -> dict:
    """Keeps data/factset.json: {"latest", "history", "status", "stale_reason", "last_check_utc"}.
    Only reports that state numbers for this season are stored. If the newest report cannot be
    downloaded or parsed, the last good values stay and status becomes "stale"."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import factset
    path = DATA / "factset.json"
    st = load_json(path, {"latest": None, "history": [], "status": None, "stale_reason": None})
    season = cfg["season_label"]
    fcfg = cfg.get("factset", {})
    hist = {h["file_date"]: h for h in st.get("history", []) if h.get("season") == season}
    has_numbers = lambda p: p and any(p.get(k) is not None for k in
                                      ("eps_beat_pct", "eps_growth_pct", "rev_growth_pct"))
    st["last_check_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        d, url, parsed, err = factset.latest(season, today, set(hist), int(fcfg.get("lookback_days", 21)))
    except Exception as e:  # network trouble of any kind
        d, url, parsed, err = None, None, None, f"{e.__class__.__name__}: {e}"
    if parsed is not None and has_numbers(parsed):
        hist[parsed["file_date"]] = parsed
        st["status"], st["stale_reason"] = "ok", None
        log(f"factset: parsed report {parsed['file_date']}")
    elif parsed is not None:
        st["status"] = "stale"
        st["stale_reason"] = (f"FactSet's report of {d.isoformat()} was downloaded but no {season} figures "
                              f"could be read from it.")
        log("factset: " + st["stale_reason"])
    elif d is not None and err is None:
        st["status"], st["stale_reason"] = "ok", None
        log(f"factset: latest report {d.isoformat()} already parsed")
    else:
        newest = max(hist) if hist else None
        if newest and (today - date.fromisoformat(newest)).days <= 8 and "no report found" in (err or ""):
            st["status"], st["stale_reason"] = "ok", None
        else:
            st["status"] = "stale"
            st["stale_reason"] = f"Could not get FactSet's latest Earnings Insight ({err})."
        log(f"factset: {err}")
    # first run of a season: collect the earlier weekly reports for the chart
    if hist and not st.get("backfilled_" + season):
        oldest = date.fromisoformat(min(hist))
        s = requests.Session()
        for i in range(1, int(fcfg.get("backfill_days", 42)) + 1):
            dd = oldest - timedelta(days=i)
            if dd.weekday() >= 5 or dd.isoformat() in hist:
                continue
            u, data = factset.fetch_pdf(dd, s)
            if not data:
                continue
            try:
                p = factset.parse(factset.pdf_text(data), season)
            except Exception as e:
                log(f"factset: backfill {dd} parse error {e}")
                continue
            if not p.get("in_season"):
                break  # earlier reports predate the first results of the season
            p["url"], p["file_date"] = u, dd.isoformat()
            p["fetched_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            hist[p["file_date"]] = p
            log(f"factset: backfilled {dd}")
        st["backfilled_" + season] = True
    st["history"] = [hist[k] for k in sorted(hist)]
    st["latest"] = st["history"][-1] if st["history"] else None
    write_json(path, st)
    return st


def benchmarks(cfg: dict, fs: dict) -> dict:
    """5-yr / 10-yr averages: from the latest FactSet report when it states them, else the cited fallback."""
    fb = cfg.get("benchmarks_fallback") or {}
    lat = (fs or {}).get("latest") or {}
    avg = lat.get("avg") or {}
    out = {}
    for k in BENCH_KEYS:
        for span in ("5y", "10y"):
            key = f"{k}_{span}"
            if avg.get(key) is not None:
                out[key] = {"value": avg[key], "source": _fs_label(lat["file_date"]), "url": lat["url"]}
            elif fb.get(key) is not None:
                out[key] = {"value": fb[key], "source": fb["source"], "url": fb["url"]}
    return out

# ---------------------------------------------------------------- metrics

def surprise_pct(actual, est):
    if actual is None or est is None or est == 0:
        return None
    return (actual - est) / abs(est) * 100.0


def classify(actual, est, band):
    if actual is None or est is None:
        return None
    sp = surprise_pct(actual, est)
    if sp is None:  # estimate of exactly zero
        return "beat" if actual > 0 else ("miss" if actual < 0 else "meet")
    if sp > band:
        return "beat"
    if sp < -band:
        return "miss"
    return "meet"


def pct(n, d):
    return round(100.0 * n / d, 1) if d else None


def scorecard(recs, akey, ekey, band, weight=None):
    """Beat/meet/miss and aggregate surprise for records that have both actual and estimate."""
    rows = [r for r in recs if r.get(akey) is not None and r.get(ekey) is not None]
    n = len(rows)
    cls = [classify(r[akey], r[ekey], band) for r in rows]
    above = sum(1 for r in rows if r[akey] > r[ekey])
    sps = [s for s in (surprise_pct(r[akey], r[ekey]) for r in rows) if s is not None]
    out = {
        "n": n,
        "beat": cls.count("beat"), "meet": cls.count("meet"), "miss": cls.count("miss"),
        "beat_pct": pct(cls.count("beat"), n), "meet_pct": pct(cls.count("meet"), n),
        "miss_pct": pct(cls.count("miss"), n),
        "above": above, "above_pct": pct(above, n),
        "median_surprise_pct": round(statistics.median(sps), 2) if sps else None,
        "agg_surprise_pct": None, "agg_method": None,
    }
    if n:
        if weight:
            w = [(r, weight(r)) for r in rows]
            if all(x is not None for _, x in w):
                sa = sum(r[akey] * x for r, x in w)
                se = sum(r[ekey] * x for r, x in w)
                if se:
                    out["agg_surprise_pct"] = round((sa - se) / abs(se) * 100, 2)
                    out["agg_method"] = "share-weighted (EPS x shares outstanding)"
        if out["agg_surprise_pct"] is None:
            sa = sum(r[akey] for r in rows)
            se = sum(r[ekey] for r in rows)
            if se:
                out["agg_surprise_pct"] = round((sa - se) / abs(se) * 100, 2)
                out["agg_method"] = "sum of values" if not weight else "sum of per-share EPS (shares missing)"
    return out


def build_records(cfg, companies, sym2cik, cache, prev, shares, finn_rows, today):
    q3_fq = set(cfg["fiscal_quarter_endings"])
    ya_fq = set(cfg["yearago_fiscal_quarter_endings"])
    ws, we = date.fromisoformat(cfg["window_start"]), date.fromisoformat(cfg["window_end"])
    ys, ye = date.fromisoformat(cfg["yearago_window_start"]), date.fromisoformat(cfg["yearago_window_end"])
    prev_by_cik = {c["cik"]: c for c in prev.get("companies", [])}

    q3_rows: dict[str, list] = {}
    ya_rows: dict[str, list] = {}
    for k, v in cache.items():
        d = date.fromisoformat(k)
        for r in v["rows"]:
            cik = sym2cik.get(r["symbol"])
            if not cik:
                continue
            fq = r.get("fiscalQuarterEnding", "")
            if ws <= d <= we and fq in q3_fq:
                q3_rows.setdefault(cik, []).append((d, r))
            elif ys <= d <= ye and fq in ya_fq:
                ya_rows.setdefault(cik, []).append((d, r))

    finn_by_sym: dict[str, list] = {}
    for row in finn_rows.values():
        finn_by_sym.setdefault(norm_sym(row["symbol"]), []).append(row)

    recs = []
    for cik, c in companies.items():
        rec = {"cik": cik, "ticker": c["ticker"], "name": c["name"], "sector": c["sector"],
               "date": None, "time": None, "fiscal_quarter": None, "eps_actual": None,
               "eps_est": None, "n_ests": None, "eps_yearago": None, "eps_yearago_src": None,
               "rev_actual": None, "rev_est": None, "shares": None}
        rows = sorted(q3_rows.get(cik, []), key=lambda x: x[0])
        reported = [(d, r) for d, r in rows if money(r.get("eps")) is not None and d <= today]
        pick = reported[-1] if reported else (rows[-1] if rows else None)
        last_year_eps = None
        for d, r in rows:
            if money(r.get("lastYearEPS")) is not None:
                last_year_eps = money(r.get("lastYearEPS"))
        if pick:
            d, r = pick
            rec.update({"date": d.isoformat(), "time": (r.get("time") or "").replace("time-", "") or None,
                        "fiscal_quarter": r.get("fiscalQuarterEnding"),
                        "eps_actual": money(r.get("eps")) if reported else None,
                        "eps_est": money(r.get("epsForecast")),
                        "n_ests": int(r["noOfEsts"]) if str(r.get("noOfEsts", "")).isdigit() else None,
                        "ticker": r["symbol"]})
        # year-ago EPS, in order of preference:
        #  1. Nasdaq "lastYearEPS" on a pre-report row (split-adjusted by the source)
        #  2. the same value captured on an earlier run, before the company reported
        #  3. the actual EPS on the year-ago season's calendar row (not split-adjusted; used only
        #     for companies that had already reported before this repo started collecting)
        pv = prev_by_cik.get(cik, {})
        ya = sorted(ya_rows.get(cik, []), key=lambda x: x[0])
        ya = [(d, r) for d, r in ya if money(r.get("eps")) is not None]
        if last_year_eps is not None:
            rec["eps_yearago"], rec["eps_yearago_src"] = last_year_eps, "nasdaq_last_year_eps"
        elif pv.get("eps_yearago") is not None and pv.get("eps_yearago_src") == "nasdaq_last_year_eps":
            rec["eps_yearago"], rec["eps_yearago_src"] = pv["eps_yearago"], "nasdaq_last_year_eps"
        elif ya:
            want = (rec["fiscal_quarter"] or "").replace("2026", "2025")
            match = [x for x in ya if x[1].get("fiscalQuarterEnding") == want] or ya
            rec["eps_yearago"], rec["eps_yearago_src"] = money(match[-1][1].get("eps")), "nasdaq_2025_calendar"
        # keep a previously captured result if the calendar row vanished (source hiccup)
        p = prev_by_cik.get(cik)
        if rec["eps_actual"] is None and p and p.get("eps_actual") is not None:
            for k in ("date", "time", "fiscal_quarter", "eps_actual", "eps_est", "n_ests"):
                rec[k] = p.get(k)
        rec["shares"] = next((shares[s] for s in c["symbols"] if s in shares), None)
        if len(c["symbols"]) > 1:
            # dual share classes: use combined share count so earnings dollars are complete
            tot = sum(shares.get(s, 0) for s in c["symbols"])
            rec["shares"] = tot or rec["shares"]
        # revenue from Finnhub, matched to the Nasdaq report date (+/- 4 days)
        if rec["eps_actual"] is not None and rec["date"]:
            rd = date.fromisoformat(rec["date"])
            cands = []
            for s in c["symbols"]:
                for fr in finn_by_sym.get(s, []):
                    try:
                        fd = date.fromisoformat(fr["date"])
                    except (TypeError, ValueError):
                        continue
                    if abs((fd - rd).days) <= 4 and fr.get("revenueActual") and fr.get("revenueEstimate"):
                        cands.append((abs((fd - rd).days), fr))
            if cands:
                fr = sorted(cands, key=lambda x: x[0])[0][1]
                rec["rev_actual"], rec["rev_est"] = float(fr["revenueActual"]), float(fr["revenueEstimate"])
            elif p and p.get("rev_actual") is not None and p.get("date") == rec["date"]:
                rec["rev_actual"], rec["rev_est"] = p["rev_actual"], p.get("rev_est")
        rec["eps_surprise_pct"] = (round(surprise_pct(rec["eps_actual"], rec["eps_est"]), 2)
                                   if surprise_pct(rec["eps_actual"], rec["eps_est"]) is not None else None)
        rec["rev_surprise_pct"] = (round(surprise_pct(rec["rev_actual"], rec["rev_est"]), 2)
                                   if surprise_pct(rec["rev_actual"], rec["rev_est"]) is not None else None)
        recs.append(rec)
    return recs


def blended_growth(recs, cfg):
    use = [r for r in recs if r["eps_yearago"] is not None and r["shares"]
           and (r["eps_actual"] if r["eps_actual"] is not None else r["eps_est"]) is not None]
    n = len(use)
    out = {"coverage": n, "min_coverage": cfg["min_growth_coverage"], "available": False,
           "value_pct": None, "reporters_pct": None, "reporters_n": 0}
    if n:
        cur = sum((r["eps_actual"] if r["eps_actual"] is not None else r["eps_est"]) * r["shares"] for r in use)
        ya = sum(r["eps_yearago"] * r["shares"] for r in use)
        if ya > 0 and n >= cfg["min_growth_coverage"]:
            out["available"] = True
            out["value_pct"] = round((cur - ya) / ya * 100, 1)
        rep = [r for r in use if r["eps_actual"] is not None]
        out["reporters_n"] = len(rep)
        ya_r = sum(r["eps_yearago"] * r["shares"] for r in rep)
        if rep and ya_r > 0:
            out["reporters_pct"] = round((sum(r["eps_actual"] * r["shares"] for r in rep) - ya_r) / ya_r * 100, 1)
    return out


def compute(cfg, recs, finn_status):
    band = float(cfg["meet_band_pct"])
    w = lambda r: r["shares"]
    reported = [r for r in recs if r["eps_actual"] is not None]
    eps = scorecard(reported, "eps_actual", "eps_est", band, weight=w)
    rev_recs = [r for r in reported if r["rev_actual"] is not None]
    rev = scorecard(rev_recs, "rev_actual", "rev_est", band)
    rev["available"] = bool(rev_recs)
    rev["status"] = finn_status
    sectors = []
    for sec in sorted({r["sector"] for r in recs}):
        sr = [r for r in recs if r["sector"] == sec]
        srep = [r for r in sr if r["eps_actual"] is not None]
        se = scorecard(srep, "eps_actual", "eps_est", band, weight=w)
        sv = scorecard([r for r in srep if r["rev_actual"] is not None], "rev_actual", "rev_est", band)
        sectors.append({"sector": sec, "companies": len(sr), "reported": len(srep),
                        "eps_n": se["n"], "eps_beat_pct": se["beat_pct"], "eps_above_pct": se["above_pct"],
                        "eps_miss_pct": se["miss_pct"], "eps_agg_surprise_pct": se["agg_surprise_pct"],
                        "rev_n": sv["n"], "rev_beat_pct": sv["beat_pct"],
                        "rev_agg_surprise_pct": sv["agg_surprise_pct"]})
    min_est = float(cfg.get("rank_min_abs_estimate", 0.10))
    rankable = [r for r in reported if r["eps_surprise_pct"] is not None and abs(r["eps_est"]) >= min_est]
    top_beats = sorted(rankable, key=lambda r: r["eps_surprise_pct"], reverse=True)[:10]
    top_misses = sorted(rankable, key=lambda r: r["eps_surprise_pct"])[:10]
    top_misses = [r for r in top_misses if r["eps_surprise_pct"] < 0]
    slim = lambda r: {k: r[k] for k in ("ticker", "name", "sector", "date", "eps_actual", "eps_est",
                                         "eps_surprise_pct", "rev_surprise_pct")}
    return {
        "universe": len(recs), "reported": len(reported),
        "scheduled": sum(1 for r in recs if r["date"] and r["eps_actual"] is None),
        "eps": eps, "revenue": rev, "growth": blended_growth(recs, cfg), "sectors": sectors,
        "top_beats": [slim(r) for r in top_beats], "top_misses": [slim(r) for r in top_misses],
    }


# ---------------------------------------------------------------- history

HIST_FIELDS = ["date", "method", "reported", "eps_n", "eps_beat_pct", "eps_meet_pct", "eps_miss_pct",
               "eps_above_pct", "eps_agg_surprise_pct", "eps_median_surprise_pct", "rev_n",
               "rev_beat_pct", "rev_agg_surprise_pct", "blended_growth_pct"]


def hist_row(day: str, method: str, m: dict) -> dict:
    e, v, g = m["eps"], m["revenue"], m["growth"]
    return {"date": day, "method": method, "reported": m["reported"], "eps_n": e["n"],
            "eps_beat_pct": e["beat_pct"], "eps_meet_pct": e["meet_pct"], "eps_miss_pct": e["miss_pct"],
            "eps_above_pct": e["above_pct"], "eps_agg_surprise_pct": e["agg_surprise_pct"],
            "eps_median_surprise_pct": e["median_surprise_pct"], "rev_n": v["n"],
            "rev_beat_pct": v["beat_pct"], "rev_agg_surprise_pct": v["agg_surprise_pct"],
            "blended_growth_pct": g["value_pct"] if g["available"] else None}


def update_history(cfg, recs, m, today: date, finn_status):
    """One row per run day (last run of the day wins). Before the first live run, days are
    back-filled by replaying report dates ("replay" rows) so the chart has a season-to-date line."""
    path = DATA / "history.csv"
    rows = {}
    if path.exists():
        for r in csv.DictReader(path.open()):
            rows[r["date"]] = r
    have_live = any(r["method"] == "live" for r in rows.values())
    if not have_live:
        dates = sorted({r["date"] for r in recs if r["eps_actual"] is not None and r["date"] < today.isoformat()})
        for d in dates:
            sub = [dict(r) if (r["date"] or "9") <= d else {**r, "eps_actual": None, "rev_actual": None}
                   for r in recs]
            mm = compute(cfg, sub, finn_status)
            mm["growth"] = {"available": False, "value_pct": None}
            rows[d] = hist_row(d, "replay", mm)
    rows[today.isoformat()] = hist_row(today.isoformat(), "live", m)
    with path.open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=HIST_FIELDS, lineterminator="\n")
        wr.writeheader()
        for d in sorted(rows):
            wr.writerow({k: ("" if rows[d].get(k) is None else rows[d].get(k)) for k in HIST_FIELDS})
    return [rows[d] for d in sorted(rows)]


# ---------------------------------------------------------------- main

def main() -> int:
    cfg = load_config()
    now_utc = datetime.now(timezone.utc)
    today = now_utc.astimezone(ET).date()
    out_path = DATA / "q3_2026.json"
    prev = load_json(out_path, {})
    stamp = now_utc.astimezone(PT).strftime("%b %-d, %Y %-I:%M %p PT")

    fs = refresh_factset(cfg, today)
    fs_block = {"latest": fs.get("latest"), "history": fs.get("history", []), "status": fs.get("status"),
                "stale_reason": fs.get("stale_reason"), "benchmarks": benchmarks(cfg, fs)}

    constituents = load_constituents(cfg)
    companies, sym2cik = build_universe(constituents)
    sp_syms = set(sym2cik)
    log(f"universe: {len(companies)} companies, {len(sp_syms)} tickers")

    cal = refresh_calendar(cfg, sp_syms, today)
    shares = refresh_shares(sp_syms)
    finn = refresh_finnhub(cfg, sp_syms, today)

    # source health: if nothing could be fetched this run, keep the previous output and flag it stale
    nasdaq_ok = cal["fetched"] > 0 and cal["failed"] <= max(3, cal["planned"] // 4)
    if not nasdaq_ok and prev.get("companies"):
        log("nasdaq: run failed, keeping previous data and marking it stale")
        prev["status"] = "stale"
        prev["stale_reason"] = (f"Nasdaq calendar fetch failed on the last run "
                                f"({cal['fetched']} of {cal['planned']} days fetched).")
        prev["last_attempt_utc"] = now_utc.isoformat(timespec="seconds")
        prev["generated_at_pt"] = stamp
        prev["factset"] = fs_block
        write_json(out_path, prev)
        hist = list(csv.DictReader((DATA / "history.csv").open())) if (DATA / "history.csv").exists() else []
        render(prev, hist, cfg)
        return 0

    recs = build_records(cfg, companies, sym2cik, cal["cache"], prev, shares, finn["rows"], today)
    m = compute(cfg, recs, finn["status"])
    lat = fs_block["latest"] or {}
    since = None
    if lat.get("file_date"):
        since = sum(1 for r in recs if r["eps_actual"] is not None and (r["date"] or "") >= lat["file_date"])
    status = "ok" if nasdaq_ok else "partial"
    out = {
        "season": cfg["season_label"],
        "generated_at_utc": now_utc.isoformat(timespec="seconds"),
        "generated_at_pt": stamp,
        "last_success_pt": stamp,
        "status": status,
        "stale_reason": None if status == "ok" else
            f"Some Nasdaq calendar days failed to download ({cal['failed']} of {cal['planned']}); cached rows used.",
        "config": {k: cfg[k] for k in ("window_start", "window_end", "fiscal_quarter_endings",
                                        "meet_band_pct", "rank_min_abs_estimate")},
        "sources": {
            "headline": "FactSet Earnings Insight (weekly PDF)",
            "eps": "Nasdaq earnings calendar (api.nasdaq.com), consensus as shown by Nasdaq",
            "shares": "Nasdaq stock screener (market cap / last sale)",
            "revenue": "Finnhub earnings calendar" if finn["status"] != "not_configured" else None,
            "constituents": cfg["constituents_url"],
        },
        "factset": fs_block,
        "reported_since_factset": since,
        **m,
        "companies": sorted(recs, key=lambda r: r["ticker"]),
    }
    write_json(out_path, out)
    hist = update_history(cfg, recs, m, today, finn["status"])
    render(out, hist, cfg)
    e = m["eps"]
    log(f"done: {m['reported']}/{m['universe']} reported; EPS beat {e['beat_pct']}% meet {e['meet_pct']}% "
        f"miss {e['miss_pct']}% (above est {e['above_pct']}%); agg surprise {e['agg_surprise_pct']}%; "
        f"median {e['median_surprise_pct']}%; revenue n={m['revenue']['n']} ({finn['status']}); "
        f"growth {m['growth']}")
    if lat:
        log("factset latest: " + json.dumps({k: lat.get(k) for k in (
            "file_date", "reported_pct", "reported_n", "eps_beat_pct", "eps_beat_n", "rev_beat_pct",
            "rev_beat_n", "eps_surprise_pct", "rev_surprise_pct", "eps_growth_kind", "eps_growth_pct",
            "rev_growth_kind", "rev_growth_pct", "avg")}))
    return 0


def render(out, hist, cfg):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from render import render_site
    render_site(out, hist, cfg, DOCS)


if __name__ == "__main__":
    sys.exit(main())
