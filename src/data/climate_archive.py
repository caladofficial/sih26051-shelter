"""Multi-year climate archive — the self-updating recent-conditions store.

Design goals
------------
* **Always recent.** The primary analysis period is a *rolling* window that
  ends at the most recent hour the archive has, not a fixed calendar year.
  `scripts/refresh_climate.py` (daily GitHub Action) extends it automatically.
* **Three tiers, in order.** Supabase (live, authoritative) -> local CSV
  archive (dev + refresh job) -> static bundle shipped with the frontend
  (`public/data/climate_bundle.json`, works with no DB at all).
* **Honest about coverage.** Every series carries the window it covers, the
  source, and whether the year is complete or year-to-date. Future hours are
  never invented.

Column contract is the engine's: t2m, rh2m, ws10m, wd10m, ps, ghi, ghi_clear,
precip, t2mdew — UTC-indexed, converted to site-local time on read.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_DIR = ROOT / "data" / "climate" / "hourly"
INDEX_FILE = ROOT / "data" / "climate" / "index.json"
#: The bundle must live somewhere the *serverless function* can read. Vercel
#: serves `public/` from the CDN and does NOT include it in the Lambda
#: filesystem, so the canonical copy sits in src/data/ (same place as
#: offline_bundle.json) and public/data/ is a CDN mirror for the browser.
BUNDLE_FILES = (ROOT / "src" / "data" / "climate_bundle.json",
                ROOT / "public" / "data" / "climate_bundle.json")

ENGINE_COLS = ("t2m", "rh2m", "ws10m", "wd10m", "ps",
               "ghi", "ghi_clear", "precip", "t2mdew")

#: rolling analysis window length
ROLLING_DAYS = 365

_index_cache: dict | None = None
_bundle_cache: dict | None = None


# --------------------------------------------------------------- manifest
def load_index(refresh: bool = False) -> dict:
    """Coverage manifest written by the ingestion/refresh scripts."""
    global _index_cache
    if _index_cache is not None and not refresh:
        return _index_cache
    try:
        _index_cache = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        _index_cache = {"sites": {}}
    return _index_cache


def load_bundle(refresh: bool = False) -> dict:
    """Static fallback bundle (summaries + design weeks, no DB needed)."""
    global _bundle_cache
    if _bundle_cache is not None and not refresh:
        return _bundle_cache
    for path in BUNDLE_FILES:
        try:
            _bundle_cache = json.loads(path.read_text(encoding="utf-8"))
            return _bundle_cache
        except Exception:                                   # noqa: BLE001
            continue
    _bundle_cache = {"sites": {}}
    return _bundle_cache


def site_for(lat: float, lon: float, tol: float = 0.05) -> str | None:
    """Canonical site name for coordinates, if one is close enough."""
    best, best_d = None, tol
    for name, meta in load_index().get("sites", {}).items():
        d = abs(float(meta["latitude"]) - lat) + abs(float(meta["longitude"]) - lon)
        if d < best_d:
            best, best_d = name, d
    return best


def available_years(site: str) -> list[int]:
    yrs = load_index().get("sites", {}).get(site, {}).get("years", {})
    return sorted(int(y) for y in yrs)


def latest_hour(site: str | None = None) -> str | None:
    """Most recent hour present in the archive (ISO UTC)."""
    sites = load_index().get("sites", {})
    pool = [sites[site]] if site and site in sites else list(sites.values())
    stamps = [y.get("last_hour") for s in pool for y in s.get("years", {}).values()
              if y.get("last_hour")]
    return max(stamps) if stamps else None


def coverage() -> dict:
    """Machine-readable summary of what data exists — drives the UI selector."""
    idx = load_index()
    sites = {}
    for name, meta in idx.get("sites", {}).items():
        years = meta.get("years", {})
        sites[name] = {
            "latitude": meta.get("latitude"),
            "longitude": meta.get("longitude"),
            "elevation_m": meta.get("elevation_m"),
            "timezone": meta.get("timezone", "Asia/Kolkata"),
            "years": {y: {"status": v.get("status"),
                          "n_hours": v.get("n_hours"),
                          "last_hour": v.get("last_hour")}
                      for y, v in sorted(years.items())},
        }
    return {
        "source": idx.get("source", "Open-Meteo Historical Weather API"),
        "generated_on": idx.get("generated_on"),
        "last_refresh": idx.get("last_refresh"),
        "latest_hour_utc": latest_hour(),
        "rolling_days": ROLLING_DAYS,
        "default_period": "latest",
        "sites": sites,
    }


# ------------------------------------------------------------------ reads
def _read_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "ts_utc"
    for c in ENGINE_COLS:
        if c not in df.columns:
            df[c] = float("nan")
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def read_local(site: str, years: list[int] | None = None) -> pd.DataFrame | None:
    """Concatenated hourly frame from the local CSV archive (UTC index)."""
    years = years or available_years(site)
    frames = []
    for y in years:
        p = ARCHIVE_DIR / f"{site}_{y}.csv"
        if p.exists():
            frames.append(_read_csv(p))
    if not frames:
        return None
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]


def window_bounds(period: str | int, site: str | None = None) -> tuple[str, str, str]:
    """Resolve a period token into (start_utc, end_utc, label).

    `period` is either a calendar year (2025) or "latest" — a rolling
    ROLLING_DAYS window ending at the newest hour the archive holds.
    """
    if str(period).lower() in ("latest", "rolling", "recent"):
        end_s = latest_hour(site)
        end = (pd.Timestamp(end_s) if end_s
               else pd.Timestamp.now(tz="UTC").floor("h"))
        if end.tzinfo is None:
            end = end.tz_localize("UTC")
        start = end - pd.Timedelta(days=ROLLING_DAYS) + pd.Timedelta(hours=1)
        label = f"Rolling 12 months to {end.strftime('%d %b %Y')}"
        return start.isoformat(), end.isoformat(), label
    y = int(period)
    start = pd.Timestamp(f"{y}-01-01T00:00:00Z")
    end = pd.Timestamp(f"{y}-12-31T23:00:00Z")
    return start.isoformat(), end.isoformat(), str(y)


def slice_period(df: pd.DataFrame, period: str | int,
                 site: str | None = None,
                 tz: str = "Asia/Kolkata") -> pd.DataFrame:
    """Rows belonging to a period, returned with the UTC index intact.

    A calendar year means the **local** calendar year, not the UTC one. The
    archive is stored in UTC; at +05:30 the last ~5.5 h of 31 December UTC are
    already 1 January locally, so a naive UTC slice produced a stray 13th month
    and skewed the December statistics.
    """
    if str(period).lower() in ("latest", "rolling", "recent"):
        start, end, _ = window_bounds(period, site)
        return df.loc[(df.index >= pd.Timestamp(start))
                      & (df.index <= pd.Timestamp(end))]
    local_year = df.index.tz_convert(tz).year
    return df[local_year == int(period)]


# -------------------------------------------------------------- summaries
def summarise(df: pd.DataFrame, tz: str = "Asia/Kolkata") -> dict:
    """Comfort/design statistics for one hourly series."""
    if df is None or df.empty:
        return {}
    local = df.copy()
    local.index = local.index.tz_convert(tz)
    t = local["t2m"].astype(float)
    daily_mean = t.resample("D").mean()
    daily_max = t.resample("D").max()
    daily_min = t.resample("D").min()
    out = {
        "n_hours": int(len(local)),
        "first_hour": local.index[0].strftime("%Y-%m-%dT%H:%M"),
        "last_hour": local.index[-1].strftime("%Y-%m-%dT%H:%M"),
        "t2m_min_c": round(float(t.min()), 2),
        "t2m_mean_c": round(float(t.mean()), 2),
        "t2m_max_c": round(float(t.max()), 2),
        "diurnal_range_c": round(float((daily_max - daily_min).mean()), 2),
        "hdd18": round(float((18 - daily_mean).clip(lower=0).sum()), 1),
        "cdd18": round(float((daily_mean - 18).clip(lower=0).sum()), 1),
        "hours_above_35c": int((t > 35).sum()),
        "hours_below_0c": int((t < 0).sum()),
        "hours_in_band_18_32": int(((t >= 18) & (t <= 32)).sum()),
    }
    for col, key, nd in (("rh2m", "rh2m_mean_pct", 1),
                         ("ghi", "ghi_mean_w_m2", 1),
                         ("ws10m", "ws10m_mean_m_s", 2)):
        if col in local:
            out[key] = round(float(local[col].astype(float).mean()), nd)
    if "precip" in local:
        out["precip_total_mm"] = round(float(local["precip"].astype(float).sum()), 1)
        out["wet_hours_pct"] = round(
            float((local["precip"].astype(float) > 0.1).mean() * 100), 1)
    out["comfort_pct_outdoor"] = round(out["hours_in_band_18_32"] /
                                       max(out["n_hours"], 1) * 100, 1)
    if len(daily_mean):
        out["hottest_day"] = str(daily_mean.idxmax().date())
        out["coldest_day"] = str(daily_mean.idxmin().date())
    return out


def monthly(df: pd.DataFrame, tz: str = "Asia/Kolkata") -> dict:
    local = df.copy()
    local.index = local.index.tz_convert(tz)
    m = local.resample("MS").mean(numeric_only=True)
    return {
        "ts": [t.strftime("%Y-%m") for t in m.index],
        "t2m": [None if pd.isna(v) else round(float(v), 2) for v in m["t2m"]],
        "ghi": [None if pd.isna(v) else round(float(v), 1) for v in m.get("ghi", m["t2m"] * 0)],
    }


def diurnal(df: pd.DataFrame, tz: str = "Asia/Kolkata") -> list[float]:
    local = df.copy()
    local.index = local.index.tz_convert(tz)
    g = local.groupby(local.index.hour)["t2m"].mean()
    return [round(float(g.get(h, float("nan"))), 2) for h in range(24)]


def design_week(df: pd.DataFrame, kind: str = "hot",
                tz: str = "Asia/Kolkata", n_days: int = 7) -> pd.DataFrame:
    """Hottest (or coldest) 7-day stress window — the design case."""
    local = df.copy()
    local.index = local.index.tz_convert(tz)
    daily = local["t2m"].resample("D").mean()
    if daily.empty:
        return local.head(0)
    peak = daily.idxmax() if kind == "hot" else daily.idxmin()
    start = peak - pd.Timedelta(days=n_days // 2)
    span = pd.Timedelta(days=n_days) - pd.Timedelta(hours=1)
    return local.loc[start:start + span]
