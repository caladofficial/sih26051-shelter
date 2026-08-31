"""Phase 1 — build the project climate dataset.

Pipeline:
    1. NASA POWER hourly  -> data/raw/climate_power_hourly.csv       (raw, never edited)
    2. Open-Meteo hourly  -> data/raw/climate_openmeteo_hourly.csv   (raw, never edited)
    3. cross-check        -> bias / RMSE / correlation per variable  (validation report)
    4. clean dataset      -> data/processed/climate_clean.csv        (POWER base, local tz)

The raw files are never modified; all downstream work uses climate_clean.csv.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.data import nasa_power, openmeteo
from src.paths import CONFIG_FILE, RAW_DIR, PROCESSED_DIR


def load_config(path: str | Path = CONFIG_FILE) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def cross_check(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    """Compare two datasets hour-by-hour on shared variables.

    Returns, per variable: bias (b minus a), RMSE, correlation, means.
    """
    shared = [c for c in ("t2m", "rh2m", "ws10m", "ghi", "precip", "ps", "t2mdew")
              if c in a.columns and c in b.columns]
    out = {}
    for col in shared:
        x = a[col].astype(float)
        y = b[col].astype(float)
        mask = x.notna() & y.notna()
        xm, ym = x[mask], y[mask]
        if len(xm) < 100:
            continue
        out[col] = {
            "n_hours": int(mask.sum()),
            "power_mean": float(xm.mean()),
            "openmeteo_mean": float(ym.mean()),
            "bias_om_minus_power": float((ym - xm).mean()),
            "rmse": float(np.sqrt(((ym - xm) ** 2).mean())),
            "correlation": float(np.corrcoef(xm, ym)[0, 1]),
        }
    return out


def build_climate_dataset(cfg: dict) -> dict:
    """Download POWER + Open-Meteo for the configured year and location,
    cross-check them, and write raw + processed datasets. Returns a dict of
    DataFrames and the validation report (also saved as JSON)."""
    loc, cl = cfg["location"], cfg["climate"]
    year = int(cl["data_year"])
    start, end = f"{year}0101", f"{year}1231"

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[climate] Downloading NASA POWER hourly data for {loc['name']} "
          f"({loc['latitude']}, {loc['longitude']}) {year} ...")
    resp = nasa_power.fetch_hourly(loc["latitude"], loc["longitude"],
                                   start, end,
                                   community=cfg["nasa_power"]["community"])
    power = nasa_power.hourly_to_dataframe(resp)
    power.to_csv(RAW_DIR / "climate_power_hourly.csv")
    print(f"[climate] POWER: {len(power)} hours saved to data/raw/")

    print(f"[climate] Downloading Open-Meteo archive data for cross-check ...")
    om = openmeteo.fetch_hourly(loc["latitude"], loc["longitude"],
                                f"{year}-01-01", f"{year}-12-31",
                                variables=cfg["open_meteo"]["variables"])
    om.to_csv(RAW_DIR / "climate_openmeteo_hourly.csv")
    print(f"[climate] Open-Meteo: {len(om)} hours saved to data/raw/")

    report = cross_check(power, om)

    # clean dataset: POWER is the base, converted to the local timezone
    clean = power.copy()
    clean.index = clean.index.tz_convert(loc["timezone"])
    clean.index.name = "timestamp_local"
    clean.to_csv(PROCESSED_DIR / "climate_clean.csv")

    with open(PROCESSED_DIR / "validation_report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    print("[climate] Clean dataset saved to data/processed/climate_clean.csv")
    return {"power": power, "openmeteo": om, "clean": clean, "report": report}


def design_weeks(clean: pd.DataFrame, year: int, n_days: int = 7):
    """Pick the hottest and coldest 7-day design weeks of the year —
    these are the stress cases used for design comparison."""
    daily = clean["t2m"].resample("D").mean()
    hot = daily.idxmax()
    cold = daily.idxmin()
    span = pd.Timedelta(days=n_days) - pd.Timedelta(hours=1)
    def week(peak):
        start = peak - pd.Timedelta(days=n_days // 2)
        return clean.loc[start: start + span]
    return {"hot_week": week(hot), "cold_week": week(cold)}


def load_clean(year: int | None = None) -> pd.DataFrame:
    """Load data/processed/climate_clean.csv (already local timezone)."""
    df = pd.read_csv(PROCESSED_DIR / "climate_clean.csv",
                     index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert("Asia/Kolkata")
    return df
