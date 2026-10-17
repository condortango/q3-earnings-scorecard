#!/usr/bin/env python3
"""One-off: rebuild data/consensus_<season>.json from the versions of data/q3_2026.json in git history
(committed after every scheduled run since Oct 6, 2026). scripts/update.py records later days itself.
Usage: python scripts/backfill_consensus.py   (then commit the data file)"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from update import CONSENSUS_NOTE, ET, consensus_path, load_config, write_json  # noqa: E402


def main() -> int:
    cfg = load_config()
    out_file = "data/q3_2026.json"
    shas = subprocess.run(["git", "log", "--format=%H", "--reverse", "--", out_file],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    by_day: dict[str, dict] = {}
    for sha in shas:  # chronological, so the last commit of a day wins
        try:
            j = json.loads(subprocess.run(["git", "show", f"{sha}:{out_file}"], cwd=ROOT,
                                          capture_output=True, text=True, check=True).stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        if j.get("season") != cfg["season_label"] or not j.get("generated_at_utc"):
            continue
        day = datetime.fromisoformat(j["generated_at_utc"]).astimezone(ET).date().isoformat()
        by_day[day] = {c["cik"]: c["eps_est"] for c in j["companies"] if c.get("eps_est") is not None}
    days, state = {}, {}
    for day in sorted(by_day):
        cur = by_day[day]
        delta = {cik: v for cik, v in cur.items() if state.get(cik) != v}
        delta.update({cik: None for cik in state if cik not in cur})
        days[day] = dict(sorted(delta.items()))
        state = cur
    path = consensus_path(cfg)
    write_json(path, {"season": cfg["season_label"], "note": CONSENSUS_NOTE, "days": days})
    print(f"{path.name}: {len(days)} days, {min(days)} .. {max(days)}, "
          f"{sum(len(v) for v in days.values())} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
