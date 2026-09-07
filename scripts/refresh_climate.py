#!/usr/bin/env python3
"""Keep the climate archive pinned to the most recent conditions.

This is the self-updating half of the pipeline. It is idempotent and safe to
run on a schedule (see .github/workflows/refresh-climate.yml, daily 02:15 UTC).

What it does, per site:
  1. Finds the newest hour already stored locally.
  2. Re-fetches the last REVISION_DAYS days *and* everything newer. ERA5T
     "recent" hours get revised when the final reanalysis lands, so the tail is
     always rewritten rather than blindly appended.
  3. Rolls into a new calendar-year file automatically on 1 January.
  4. Recomputes the coverage manifest (data/climate/index.json).
  5. Rebuilds the static fallback bundle (public/data/climate_bundle.json).
  6. Upserts locations + hourly weather + per-year summaries into Supabase when
     SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are present.

Nothing here fabricates future hours: if the archive stops at day D, the data
stops at day D and the manifest says so.

Run:
  python scripts/refresh_climate.py                # incremental, all sites
  python scripts/refresh_climate.py --full         # re-download every year
  python scripts/refresh_climate.py --no-supabase  # local only
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.fetch_climate_multiyear import (          # noqa: E402
    SITES, fetch, summarise as year_summary, OUT_DIR, INDEX_FILE,
)
from src.data import climate_archive as ca             # noqa: E402

#: recent hours are provisional in ERA5T — always re-pull this tail
REVISION_DAYS = 10
FIRST_YEAR = 2024


def site_last_hour(site: str) -> pd.Timestamp | None:
    df = ca.read_local(site)
    if df is None or df.empty:
        return None
    return df.index[-1]


def refresh_site(site: str, full: bool, cap: date) -> dict:
    # data/climate/hourly is a gitignored cache, so it does not exist on a
    # fresh CI checkout — every site failed with "non-existent directory"
    # until this line existed.
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = SITES[site]
    last = None if full else site_last_hour(site)
    if last is None:
        start = date(FIRST_YEAR, 1, 1)
    else:
        start = (last.date() - timedelta(days=REVISION_DAYS))
        start = max(start, date(FIRST_YEAR, 1, 1))

    if start > cap:
        return {"site": site, "status": "up_to_date", "new_hours": 0}

    # request per calendar year so files stay one-year-per-file
    touched, added = [], 0
    for year in range(start.year, cap.year + 1):
        y_start = max(start, date(year, 1, 1))
        y_end = min(cap, date(year, 12, 31))
        if y_end < y_start:
            continue
        fresh = fetch(site, meta, y_start, y_end)
        if fresh.empty:
            continue
        path = OUT_DIR / f"{site}_{year}.csv"
        if path.exists():
            old = ca._read_csv(path)
            before = len(old)
            merged = pd.concat([old, fresh])
            # fresh wins on overlap -> ERA5T revisions are applied
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            added += max(len(merged) - before, 0)
        else:
            merged = fresh.sort_index()
            added += len(merged)
        merged = merged[merged.index.year == year]
        merged.to_csv(path)
        touched.append(year)
        time.sleep(0.3)

    return {"site": site, "status": "refreshed", "years": touched,
            "new_hours": added,
            "last_hour": str(site_last_hour(site))}


def rebuild_index(cap: date) -> dict:
    index = {}
    if INDEX_FILE.exists():
        index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    index["source"] = "Open-Meteo Historical Weather API (ERA5 family)"
    index["generated_on"] = date.today().isoformat()
    index["last_refresh"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    index.setdefault("sites", {})

    for site, meta in SITES.items():
        years = {}
        for path in sorted(OUT_DIR.glob(f"{site}_*.csv")):
            year = int(path.stem.rsplit("_", 1)[1])
            df = ca._read_csv(path)
            if df.empty:
                continue
            complete = len(df) >= 8760
            years[str(year)] = {
                "file": f"data/climate/hourly/{path.name}",
                "status": "complete" if complete else "year_to_date",
                "data_status": "historical_reanalysis",
                "first_hour": str(df.index[0]),
                "last_hour": str(df.index[-1]),
                **year_summary(df),
            }
        if years:
            index["sites"][site] = {**meta, "years": years}

    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(index, indent=1), encoding="utf-8")
    return index


def push_supabase(index: dict, limit_days: int | None = None) -> dict:
    """Upsert locations, hourly weather and per-year summaries."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        return {"pushed": False, "reason": "no SUPABASE_URL/SERVICE_ROLE_KEY"}

    from src.db.store import Store
    store = Store()
    if store.backend != "supabase":
        return {"pushed": False, "reason": f"store backend is {store.backend}"}

    import requests
    rest = url.rstrip("/") + "/rest/v1"
    hdr = {"apikey": key, "Authorization": f"Bearer {key}",
           "Content-Type": "application/json",
           "Prefer": "resolution=merge-duplicates"}

    n_rows = 0
    summaries = []
    cutoff = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=limit_days)
              if limit_days else None)

    for site, meta in index["sites"].items():
        loc_id = f"loc_{abs(meta['latitude']):.4f}_{abs(meta['longitude']):.4f}"
        store.upsert_location({
            "location_id": loc_id, "name": site,
            "latitude": meta["latitude"], "longitude": meta["longitude"],
            "elevation_m": meta.get("elevation_m", 0),
            "timezone": meta.get("timezone", "Asia/Kolkata"),
        })
        df = ca.read_local(site)
        if df is None or df.empty:
            continue
        if cutoff is not None:
            df = df[df.index >= cutoff]
        if not df.empty:
            n_rows += store.save_weather(
                df, loc_id, source="open-meteo-archive",
                data_status="historical_reanalysis")

        tz = meta.get("timezone", "Asia/Kolkata")
        for year_s, y in meta.get("years", {}).items():
            full = ca.read_local(site, [int(year_s)])
            if full is None or full.empty:
                continue
            summaries.append({
                "location_id": loc_id, "site": site, "year": int(year_s),
                "status": y.get("status"),
                "source": "open-meteo-archive",
                "summary": ca.summarise(full, tz),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })

    # climate_summary is created by migration 0006; ignore if not migrated yet
    ok_sum = False
    try:
        r = requests.post(f"{rest}/climate_summary",
                          params={"on_conflict": "location_id,year"},
                          headers=hdr, json=summaries, timeout=60)
        ok_sum = r.status_code < 300
        if not ok_sum:
            print(f"[supabase] climate_summary skipped: {r.status_code} {r.text[:200]}")
    except Exception as exc:                             # noqa: BLE001
        print(f"[supabase] climate_summary skipped: {exc}")

    try:
        requests.post(f"{rest}/climate_refresh", headers=hdr, timeout=30, json={
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "sites": len(index["sites"]),
            "weather_rows": n_rows,
            "latest_hour_utc": ca.latest_hour(),
            "source": "open-meteo-archive",
        })
    except Exception:                                    # noqa: BLE001
        pass

    return {"pushed": True, "weather_rows": n_rows,
            "summaries": len(summaries) if ok_sum else 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default=",".join(SITES))
    ap.add_argument("--full", action="store_true",
                    help="re-download every year from scratch")
    ap.add_argument("--no-supabase", action="store_true")
    ap.add_argument("--supabase-days", type=int, default=None,
                    help="only push the last N days of hourly rows "
                         "(default: everything)")
    ap.add_argument("--no-bundle", action="store_true")
    args = ap.parse_args()

    cap = date.today()
    sites = [s.strip() for s in args.sites.split(",") if s.strip() in SITES]
    print(f"== climate refresh · {len(sites)} sites · through {cap} ==")

    report = []
    for site in sites:
        try:
            res = refresh_site(site, args.full, cap)
        except Exception as exc:                         # noqa: BLE001
            res = {"site": site, "status": "error", "error": str(exc)}
            print(f"!! {site}: {exc}", file=sys.stderr)
        report.append(res)
        print(f"   {res['site']}: {res['status']} (+{res.get('new_hours', 0)} h)")

    index = rebuild_index(cap)

    if not args.no_bundle:
        subprocess.run([sys.executable, "scripts/build_climate_bundle.py"],
                       cwd=ROOT, check=False)

    push = {"pushed": False, "reason": "--no-supabase"}
    if not args.no_supabase:
        push = push_supabase(index, args.supabase_days)
    print(f"[supabase] {push}")

    log = ROOT / "data" / "climate" / "refresh_log.json"
    history = []
    if log.exists():
        try:
            history = json.loads(log.read_text(encoding="utf-8"))[-29:]
        except Exception:                                # noqa: BLE001
            history = []
    history.append({
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_hour_utc": ca.latest_hour(),
        "sites": len(index.get("sites", {})),
        "supabase": push,
        "results": report,
    })
    log.write_text(json.dumps(history, indent=1), encoding="utf-8")
    print(f"\nLatest hour now: {ca.latest_hour()}")

    # Fail loudly. Previously every site could error and this still returned 0,
    # so CI reported success while writing an EMPTY bundle over a good one.
    errored = [r["site"] for r in report if r["status"] == "error"]
    if errored:
        print(f"\n!! {len(errored)}/{len(report)} sites failed: "
              f"{', '.join(errored)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
