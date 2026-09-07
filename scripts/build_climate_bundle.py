#!/usr/bin/env python3
"""Build the static climate fallback bundle shipped with the frontend.

Output: public/data/climate_bundle.json  (also copied to public-src/data/)

The bundle is the third data tier: if Supabase is unreachable, rate-limited or
simply not configured, Climate Recon and Climate Trends still render from this
file. It deliberately carries *derived* series only — summaries, monthly means,
diurnal curves and the two 7-day design weeks — not all 350k hourly rows, so it
stays a few hundred KB instead of ~40 MB.

Run:  python scripts/build_climate_bundle.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data import climate_archive as ca      # noqa: E402

OUT = ROOT / "public" / "data" / "climate_bundle.json"
OUT_SRC = ROOT / "public-src" / "data" / "climate_bundle.json"


def week_payload(df, tz: str) -> dict:
    if df is None or df.empty:
        return {}
    return {
        "start": df.index[0].strftime("%Y-%m-%d"),
        "end": df.index[-1].strftime("%Y-%m-%d"),
        "ts": [t.strftime("%Y-%m-%dT%H:%M") for t in df.index],
        "t2m": [round(float(v), 2) for v in df["t2m"]],
        "ghi": [round(float(v), 1) for v in df["ghi"].fillna(0)],
        "rh2m": [round(float(v), 1) for v in df["rh2m"].fillna(0)],
    }


def main() -> int:
    idx = ca.load_index(refresh=True)
    sites_meta = idx.get("sites", {})
    if not sites_meta:
        print("!! no archive index — run scripts/fetch_climate_multiyear.py first")
        return 1

    bundle = {
        "schema_version": 2,
        "generated_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": idx.get("source"),
        "note": ("Derived climate series for offline/fallback rendering. "
                 "Hourly detail lives in Supabase; future hours are never "
                 "fabricated — partial years are marked year_to_date."),
        "rolling_days": ca.ROLLING_DAYS,
        "latest_hour_utc": ca.latest_hour(),
        "sites": {},
    }

    for site, meta in sites_meta.items():
        tz = meta.get("timezone", "Asia/Kolkata")
        df = ca.read_local(site)
        if df is None or df.empty:
            print(f"-- {site}: no local CSVs, skipped")
            continue

        entry = {
            "latitude": meta.get("latitude"),
            "longitude": meta.get("longitude"),
            "elevation_m": meta.get("elevation_m"),
            "timezone": tz,
            "years": {},
        }

        for year_s, ymeta in sorted(meta.get("years", {}).items()):
            sub = ca.slice_period(df, int(year_s), site, tz)
            if sub.empty:
                continue
            entry["years"][year_s] = {
                "status": ymeta.get("status"),
                "data_status": ymeta.get("data_status", "historical_reanalysis"),
                "summary": ca.summarise(sub, tz),
                "monthly": ca.monthly(sub, tz),
                "diurnal": ca.diurnal(sub, tz),
                "design_weeks": {
                    "hot": week_payload(ca.design_week(sub, "hot", tz), tz),
                    "cold": week_payload(ca.design_week(sub, "cold", tz), tz),
                },
            }

        # the rolling "latest" window — what the UI defaults to
        roll = ca.slice_period(df, "latest", site, tz)
        if not roll.empty:
            _, _, label = ca.window_bounds("latest", site)
            entry["latest"] = {
                "label": label,
                "status": "rolling",
                "summary": ca.summarise(roll, tz),
                "monthly": ca.monthly(roll, tz),
                "diurnal": ca.diurnal(roll, tz),
                "design_weeks": {
                    "hot": week_payload(ca.design_week(roll, "hot", tz), tz),
                    "cold": week_payload(ca.design_week(roll, "cold", tz), tz),
                },
            }

        # year-over-year deltas, computed on identical Jan1->last-common-day
        # spans so a partial year is never compared against a full one
        entry["trend"] = build_trend(df, entry, tz)
        bundle["sites"][site] = entry
        print(f"[bundle] {site}: {len(entry['years'])} years")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT_SRC.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(bundle, separators=(",", ":"))
    OUT.write_text(text, encoding="utf-8")
    OUT_SRC.write_text(text, encoding="utf-8")
    kb = len(text.encode()) / 1024
    print(f"\nWrote {OUT.relative_to(ROOT)}  ({kb:,.0f} KB, "
          f"{len(bundle['sites'])} sites)")
    return 0


def build_trend(df, entry: dict, tz: str) -> dict:
    """Like-for-like year comparison.

    The newest year is usually incomplete, so every year is truncated to the
    same day-of-year span before the means are taken. Otherwise "2026 is
    hotter" would just be an artefact of the year stopping in September.
    """
    import pandas as pd

    local = df.copy()
    local.index = local.index.tz_convert(tz)
    years = sorted({int(y) for y in local.index.year.unique()})
    if len(years) < 2:
        return {}
    last_doy = int(local[local.index.year == years[-1]].index.dayofyear.max())

    rows = []
    for y in years:
        sub = local[(local.index.year == y) & (local.index.dayofyear <= last_doy)]
        if sub.empty:
            continue
        t = sub["t2m"].astype(float)
        daily = t.resample("D")
        rows.append({
            "year": y,
            "n_hours": int(len(sub)),
            "t2m_mean_c": round(float(t.mean()), 2),
            "t2m_max_c": round(float(t.max()), 2),
            "t2m_min_c": round(float(t.min()), 2),
            "hours_above_35c": int((t > 35).sum()),
            "hours_below_0c": int((t < 0).sum()),
            "cdd18": round(float((daily.mean() - 18).clip(lower=0).sum()), 1),
            "hdd18": round(float((18 - daily.mean()).clip(lower=0).sum()), 1),
            "precip_total_mm": round(float(sub["precip"].astype(float).sum()), 1),
            "ghi_mean_w_m2": round(float(sub["ghi"].astype(float).mean()), 1),
            "ws10m_mean_m_s": round(float(sub["ws10m"].astype(float).mean()), 2),
        })
    if len(rows) < 2:
        return {}
    base, newest = rows[0], rows[-1]
    return {
        "comparable_through_doy": last_doy,
        "comparable_note": (f"All years truncated to day-of-year 1–{last_doy} "
                            f"so partial {rows[-1]['year']} is compared fairly."),
        "years": rows,
        "delta": {
            "span": f"{base['year']}→{newest['year']}",
            "t2m_mean_c": round(newest["t2m_mean_c"] - base["t2m_mean_c"], 2),
            "t2m_max_c": round(newest["t2m_max_c"] - base["t2m_max_c"], 2),
            "hours_above_35c": newest["hours_above_35c"] - base["hours_above_35c"],
            "cdd18": round(newest["cdd18"] - base["cdd18"], 1),
            "precip_total_mm": round(newest["precip_total_mm"]
                                     - base["precip_total_mm"], 1),
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
