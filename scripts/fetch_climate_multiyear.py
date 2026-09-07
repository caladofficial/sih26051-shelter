#!/usr/bin/env python3
"""Multi-year hourly climate ingestion (2024 / 2025 / 2026-YTD).

Based on the Shelter Studio 2025/2026 Climate Data Package (Drive), adapted to
this repo's canonical 14 sites (+ Jaipur) and to the engine's column names.

Source: Open-Meteo Historical Weather API (ERA5-family reanalysis).
  https://archive-api.open-meteo.com/v1/archive

Why Open-Meteo for *every* year (2024 included) even though the existing 2024
cache comes from NASA POWER: a year-over-year comparison must not mix data
sources, or a source bias would be indistinguishable from real climate change.
POWER 2024 stays as-is for engine parity; these rows are a separate, internally
consistent multi-year series.

2026 is year-to-date only. Future dates are NOT fabricated -- rows simply stop
at the last day the archive has, and every row carries `data_status`.

Outputs (per site, per year):
  data/climate/hourly/{site}_{year}.csv    engine columns, local timestamps
  data/climate/index.json                  coverage manifest + per-year summary

Run:
  python scripts/fetch_climate_multiyear.py
  python scripts/fetch_climate_multiyear.py --sites Leh,Delhi --years 2025
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "climate" / "hourly"
INDEX_FILE = ROOT / "data" / "climate" / "index.json"

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"

# Canonical sites: the 14 already wired into the engine/presets/AI model,
# plus Jaipur which the new dataset package adds.
SITES: dict[str, dict] = {
    "Prayagraj": {"latitude": 25.4358, "longitude": 81.8463, "elevation_m": 98,   "timezone": "Asia/Kolkata"},
    "Delhi":     {"latitude": 28.6139, "longitude": 77.2090, "elevation_m": 216,  "timezone": "Asia/Kolkata"},
    "Jaipur":    {"latitude": 26.9124, "longitude": 75.7873, "elevation_m": 431,  "timezone": "Asia/Kolkata"},
    "Jaisalmer": {"latitude": 26.9157, "longitude": 70.9083, "elevation_m": 225,  "timezone": "Asia/Kolkata"},
    "Ahmedabad": {"latitude": 23.0225, "longitude": 72.5714, "elevation_m": 53,   "timezone": "Asia/Kolkata"},
    "Chennai":   {"latitude": 13.0827, "longitude": 80.2707, "elevation_m": 6,    "timezone": "Asia/Kolkata"},
    "Mumbai":    {"latitude": 19.0760, "longitude": 72.8777, "elevation_m": 14,   "timezone": "Asia/Kolkata"},
    "Kolkata":   {"latitude": 22.5726, "longitude": 88.3639, "elevation_m": 9,    "timezone": "Asia/Kolkata"},
    "Bengaluru": {"latitude": 12.9716, "longitude": 77.5946, "elevation_m": 920,  "timezone": "Asia/Kolkata"},
    "Hyderabad": {"latitude": 17.3850, "longitude": 78.4867, "elevation_m": 542,  "timezone": "Asia/Kolkata"},
    "Pune":      {"latitude": 18.5204, "longitude": 73.8567, "elevation_m": 560,  "timezone": "Asia/Kolkata"},
    "Leh":       {"latitude": 34.1526, "longitude": 77.5771, "elevation_m": 3500, "timezone": "Asia/Kolkata"},
    "Srinagar":  {"latitude": 34.0837, "longitude": 74.7973, "elevation_m": 1585, "timezone": "Asia/Kolkata"},
    "Kargil":    {"latitude": 34.5584, "longitude": 76.1334, "elevation_m": 2676, "timezone": "Asia/Kolkata"},
    "Dras":      {"latitude": 34.4306, "longitude": 75.7499, "elevation_m": 3280, "timezone": "Asia/Kolkata"},
}

# Open-Meteo variable -> engine column (same names src/data/openmeteo.py uses)
HOURLY_MAP = {
    "temperature_2m": "t2m",
    "relative_humidity_2m": "rh2m",
    "dew_point_2m": "t2mdew",
    "wind_speed_10m": "ws10m",
    "wind_direction_10m": "wd10m",
    "surface_pressure": "ps",
    "shortwave_radiation": "ghi",
    "direct_radiation": "dni_horiz",
    "diffuse_radiation": "dhi",
    "precipitation": "precip",
    "cloud_cover": "cloud_pct",
}

YEARS = (2024, 2025, 2026)


def fetch(site: str, meta: dict, start: date, end: date,
          retries: int = 4) -> pd.DataFrame:
    params = {
        "latitude": meta["latitude"],
        "longitude": meta["longitude"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY_MAP),
        "timezone": "UTC",           # store UTC; convert to local on read
        "temperature_unit": "celsius",
        "wind_speed_unit": "ms",
        "precipitation_unit": "mm",
    }
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(ARCHIVE, params=params, timeout=(15, 180))
            if r.status_code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            r.raise_for_status()
            h = r.json()["hourly"]
            df = pd.DataFrame(h)
            df["time"] = pd.to_datetime(df["time"], utc=True)
            df = df.set_index("time").rename(columns=HOURLY_MAP)
            df.index.name = "ts_utc"
            return df
        except Exception as exc:                      # noqa: BLE001
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{site} {start}->{end}: {last}")


def summarise(df: pd.DataFrame) -> dict:
    """Comfort-relevant yearly summary (same stats the API /climate exposes)."""
    t = df["t2m"].astype(float)
    daily = t.resample("D")
    out = {
        "n_hours": int(len(df)),
        "t2m_min_c": round(float(t.min()), 2),
        "t2m_mean_c": round(float(t.mean()), 2),
        "t2m_max_c": round(float(t.max()), 2),
        "diurnal_range_c": round(float((daily.max() - daily.min()).mean()), 2),
        "hdd18": round(float(((18 - daily.mean()).clip(lower=0)).sum()), 1),
        "cdd18": round(float(((daily.mean() - 18).clip(lower=0)).sum()), 1),
        "rh2m_mean_pct": round(float(df["rh2m"].astype(float).mean()), 1),
        "ghi_mean_w_m2": round(float(df["ghi"].astype(float).mean()), 1),
        "ws10m_mean_m_s": round(float(df["ws10m"].astype(float).mean()), 2),
        "precip_total_mm": round(float(df["precip"].astype(float).sum()), 1),
        "hours_above_35c": int((t > 35).sum()),
        "hours_below_0c": int((t < 0).sum()),
    }
    hot = daily.mean().idxmax()
    cold = daily.mean().idxmin()
    out["hottest_day"] = str(hot.date())
    out["coldest_day"] = str(cold.date())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default=",".join(SITES))
    ap.add_argument("--years", default=",".join(str(y) for y in YEARS))
    ap.add_argument("--end-partial", default=None,
                    help="last date to request for the in-progress year")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sites = [s.strip() for s in args.sites.split(",") if s.strip()]
    years = [int(y) for y in args.years.split(",") if y.strip()]

    # Archive lags real time by ~5 days; never ask for the future.
    today = date.today()
    cap = date.fromisoformat(args.end_partial) if args.end_partial else today
    cap = min(cap, today)

    index: dict = {}
    if INDEX_FILE.exists():
        index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    index.setdefault("source", "Open-Meteo Historical Weather API (ERA5 family)")
    index.setdefault("sites", {})
    index["generated_on"] = today.isoformat()

    for site in sites:
        if site not in SITES:
            print(f"!! unknown site {site}", file=sys.stderr)
            continue
        meta = SITES[site]
        entry = index["sites"].setdefault(site, {**meta, "years": {}})
        entry.update({k: meta[k] for k in meta})
        for year in years:
            start = date(year, 1, 1)
            end = min(date(year, 12, 31), cap)
            if end < start:
                print(f"-- {site} {year}: not started yet, skipped")
                continue
            path = OUT_DIR / f"{site}_{year}.csv"
            print(f"[{site} {year}] {start} -> {end} ...", flush=True)
            try:
                df = fetch(site, meta, start, end)
            except Exception as exc:                  # noqa: BLE001
                print(f"!! {exc}", file=sys.stderr)
                continue
            if df.empty:
                print(f"!! {site} {year}: empty response", file=sys.stderr)
                continue
            df.to_csv(path)
            complete = len(df) >= 8760
            entry["years"][str(year)] = {
                "file": f"data/climate/hourly/{path.name}",
                "status": "complete" if complete else "year_to_date",
                "data_status": "historical_reanalysis",
                "first_hour": str(df.index[0]),
                "last_hour": str(df.index[-1]),
                **summarise(df),
            }
            print(f"   {len(df):,} h  mean {entry['years'][str(year)]['t2m_mean_c']} C")
            time.sleep(0.4)

        INDEX_FILE.write_text(json.dumps(index, indent=1), encoding="utf-8")

    n = sum(len(s["years"]) for s in index["sites"].values())
    print(f"\nDone. {len(index['sites'])} sites, {n} site-years -> {INDEX_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
