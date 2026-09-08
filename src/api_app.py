"""SIH26051 Shelter API — FastAPI application (module form).

The Vercel entrypoint is the thin shim ``api/index.py``, which re-exports
``app`` from here (``from src.api_app import app``). Keeping this module
out of the entrypoint file is required so the Vercel Python builder's
static analysis detects a clean top-level ASGI app and emits single-app
routing instead of per-file ``api/``-directory routing.

Endpoints (all JSON):
    GET  /api/health
    GET  /api/locations
    GET  /api/materials
    GET  /api/climate?lat=&lon=&year=&timezone=   (Supabase-cached, else live)
    POST /api/simulate                            (RC model on design weeks)
    POST /api/optimize                            (Optuna search)
    GET  /api/simulations?limit=
    GET  /api/optimizations?limit=

Design notes
    * The API runs the FAST RC model (validated against EnergyPlus locally).
      EnergyPlus itself cannot run in serverless functions, so it stays the
      desktop validation engine (scripts/run_first_simulation.py --energyplus).
    * Weather + results persist to Supabase when SUPABASE_URL /
      SUPABASE_SERVICE_ROLE_KEY are set; otherwise the API runs stateless
      (SQLite fallback for local development).
    * CORS is wide open for the demo; restrict before production.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import math
import os
import secrets
import sys
import time
from pathlib import Path
import numpy as np

from src.ai_model import (  # noqa: E402
    build_features, model_meta, model_metrics, predict_batch,
    predict_design, sample_design, FEATURES, TARGETS)

import pandas as pd  # noqa: F401  (used by profile/comparison helpers)

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# project root: src/api_app.py -> parents[1]; keep in sys.path for direct
# module runs (e.g. scripts) that don't go through the package
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.climate import (design_weeks, load_config,  # noqa: E402
                              cross_check)
from src.data import nasa_power, openmeteo  # noqa: E402
from src.data import climate_archive  # noqa: E402
import datetime as _dt  # noqa: E402
from src.db.store import Store, new_id  # noqa: E402
from src.cad import model as cad_model  # noqa: E402
from src.cad import dxf as cad_dxf  # noqa: E402
from src.cad import mesh as cad_mesh  # noqa: E402
from src.cad import ingest as cad_ingest  # noqa: E402
from src.thermal.rc_model import (comfort_stats, load_materials,  # noqa: E402
                                  simulate)

_PROD = os.environ.get("VERCEL") == "1"   # hide API schema on the deployed site
app = FastAPI(title="SIH26051 Shelter API", version="1.0.0",
              docs_url=None if _PROD else "/docs",
              redoc_url=None if _PROD else "/redoc",
              openapi_url=None if _PROD else "/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

CFG = load_config()
STORE = Store()

DEFAULT_LOCATION = {
    "latitude": CFG["location"]["latitude"],
    "longitude": CFG["location"]["longitude"],
    "timezone": CFG["location"]["timezone"],
    "name": CFG["location"]["name"],
    "elevation_m": CFG["location"]["elevation_m"],
}


def _canonical_site_name(lat: float, lon: float, tol_deg: float = 0.35):
    """Name of the nearest engine-verified site, if within tol_deg (~39 km).

    Weather-cache rows are created for every simulated coordinate; without
    this the cache labels every site with the config default's name (the
    bug that filled `locations` with 20 "Prayagraj" rows).
    """
    try:
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    best, best_d = None, tol_deg
    for nm, sd in data.get("sites", {}).items():
        dlat = float(sd["latitude"]) - lat
        dlon = float(sd["longitude"]) - lon
        d = (dlat * dlat + dlon * dlon) ** 0.5
        if d < best_d:
            best, best_d = nm, d
    return best


# --------------------------------------------------------------------------
# request models
# --------------------------------------------------------------------------
class SimulateRequest(BaseModel):
    lat: float | None = None
    lon: float | None = None
    year: int | None = None
    timezone: str | None = None
    # analysis window for the WEATHER: a calendar year or "latest" (rolling
    # 12 months ending at the newest observed hour). Distinct from `period`
    # below, which selects the simulated stress window inside that weather.
    climate_period: str | None = None
    period: str = "hot_week"          # hot_week | cold_week | full_year
    # design overrides (optional; config defaults otherwise)
    orientation_deg: int | None = None
    length_m: float | None = None
    width_m: float | None = None
    height_m: float | None = None
    wall_material: str | None = None
    wall_thickness_m: float | None = None
    roof_material: str | None = None
    roof_thickness_m: float | None = None
    insulation_material: str | None = None
    insulation_thickness_m: float | None = None
    window_wall: str | None = None
    window_width_m: float | None = None
    window_height_m: float | None = None
    window_shgc: float | None = None
    window_u_w_m2k: float | None = None
    ach: float | None = None


class OptimizeRequest(BaseModel):
    lat: float | None = None
    lon: float | None = None
    year: int | None = None
    timezone: str | None = None
    climate_period: str | None = None
    n_trials: int = Field(30, ge=5, le=60)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _loc(req: BaseModel) -> dict:
    period = resolve_period(getattr(req, "year", None),
                            getattr(req, "climate_period", None))
    return {
        "latitude": req.lat if req.lat is not None else DEFAULT_LOCATION["latitude"],
        "longitude": req.lon if req.lon is not None else DEFAULT_LOCATION["longitude"],
        "timezone": req.timezone or DEFAULT_LOCATION["timezone"],
        "year": _period_year(period),
        "period": period,
    }


#: NASA POWER publishes with a multi-day lag; never request newer than this.
POWER_LAG_DAYS = 7
#: Days of overlap used for the POWER-vs-Open-Meteo cross-check. cross_check
#: needs >= 100 shared hours to report anything; 60 days gives ~1,400, which is
#: statistically ample. Pulling a full year here cost ~10 s per new pin for a
#: validation report, not for any data the engine actually uses.
POWER_CHECK_DAYS = 60


def resolve_period(year=None, period: str | None = None):
    """Normalise the analysis-period token used across the API.

    Accepts a calendar year (int or numeric string) or the rolling token
    ``"latest"``. ``"latest"`` is the default: it tracks the most recent
    ROLLING_DAYS of real observations and moves forward on its own every time
    the scheduled refresh job runs, so the platform is never pinned to a stale
    calendar year.
    """
    token = period if period is not None else year
    if token is None or str(token).strip() == "":
        return "latest"
    s = str(token).strip().lower()
    if s in ("latest", "rolling", "recent", "auto", "0"):
        return "latest"
    try:
        y = int(float(s))
    except (TypeError, ValueError):
        return "latest"
    return y if 1990 <= y <= 2100 else "latest"


def _period_year(period) -> int:
    """Calendar year a period token ends in — for legacy callers/labels."""
    if period == "latest":
        end = climate_archive.latest_hour()
        return int(end[:4]) if end else _dt.date.today().year
    return int(period)


def get_weather_cached(lat: float, lon: float, year: int, timezone: str,
                       force_refresh: bool = False, period=None):
    """Hourly weather for a site and analysis period.

    Tiers, in order:
      1. **Supabase** — the live store, kept current by the daily refresh job.
      2. **Local CSV archive** (``data/climate/hourly``) — present in dev and
         in the refresh job; not shipped to the serverless bundle.
      3. **Live fetch** — NASA POWER + Open-Meteo for arbitrary coordinates
         that aren't one of the canonical sites.

    Returns (df, source, validation_report) with a site-local tz index.
    """
    period = resolve_period(year, period)
    location_id = f"loc_{abs(lat):.4f}_{abs(lon):.4f}"
    site = climate_archive.site_for(lat, lon)
    start_utc, end_utc, _label = climate_archive.window_bounds(period, site)
    rolling = period == "latest"

    if not force_refresh:
        # -- tier 1: Supabase -------------------------------------------
        try:
            if rolling:
                q_start, q_end = start_utc, end_utc
            else:
                # fetch the UTC year plus a day of slack either side, then
                # filter on the LOCAL year below — a +05:30 site's local year
                # starts before and ends after the UTC one
                q_start = (pd.Timestamp(start_utc) - pd.Timedelta(days=1)).isoformat()
                q_end = (pd.Timestamp(end_utc) + pd.Timedelta(days=1)).isoformat()
            cached = STORE.load_weather(location_id, start_utc=q_start,
                                        end_utc=q_end)
            if cached is not None:
                cached.index = cached.index.tz_convert(timezone)
                if not rolling:
                    # hourly rows may straddle the UTC year boundary — filter
                    # on the LOCAL year (POWER LST => :30-offset local stamps)
                    cached = cached[cached.index.year == period]
                if len(cached) > 8000:
                    return cached, "supabase-cache", None
        except Exception:
            pass

        # -- tier 2: local multi-year archive ---------------------------
        if site:
            try:
                local = climate_archive.read_local(site)
                if local is not None and not local.empty:
                    sub = climate_archive.slice_period(local, period, site, timezone)
                    if len(sub) > 8000:
                        sub = sub.copy()
                        sub.index = sub.index.tz_convert(timezone)
                        return sub, "archive-open-meteo", None
            except Exception:
                pass

    # -- tier 3: live fetch ---------------------------------------------
    y = _period_year(period)
    if rolling:
        end_d = pd.Timestamp(end_utc).date()
        start_d = pd.Timestamp(start_utc).date()
    else:
        start_d = _dt.date(y, 1, 1)
        end_d = min(_dt.date(y, 12, 31), _dt.date.today())

    # Open-Meteo is the PRIMARY series for arbitrary coordinates. Two reasons:
    # it is the same source as the multi-year archive (so a custom pin is
    # directly comparable with a canonical site instead of carrying a silent
    # source bias), and it publishes right up to the present, which the
    # rolling "latest" window requires.
    om = openmeteo.fetch_hourly(lat, lon, start_d.isoformat(),
                                end_d.isoformat(), timezone="UTC")

    # NASA POWER stays the independent cross-check, but it lags several days.
    # Asking it for the last week of a rolling window returns nothing but fill
    # values, which is exactly what used to 502 every custom coordinate — the
    # "detect my location" button included. Clamp the request to the range
    # POWER can actually serve, and treat its absence as a missing validation
    # report rather than a failed request.
    report = None
    power = None
    power_end = min(end_d, _dt.date.today() - _dt.timedelta(days=POWER_LAG_DAYS))
    power_start = max(start_d, power_end - _dt.timedelta(days=POWER_CHECK_DAYS))
    if power_end > power_start:
        try:
            power = nasa_power.hourly_to_dataframe(nasa_power.fetch_hourly(
                lat, lon, power_start.strftime("%Y%m%d"),
                power_end.strftime("%Y%m%d")))
            report = cross_check(power, om)
        except Exception as exc:                          # noqa: BLE001
            print(f"[api] POWER cross-check unavailable: {exc}")

    df = om.copy()
    df.index = df.index.tz_convert(timezone)
    try:
        STORE.upsert_location({"location_id": location_id,
                               "name": _canonical_site_name(lat, lon)
                                       or "Custom location",
                               "latitude": lat, "longitude": lon,
                               "elevation_m": DEFAULT_LOCATION["elevation_m"],
                               "timezone": timezone})
        STORE.save_weather(om, location_id,             # store in UTC
                           source="open-meteo-archive",
                           data_status="historical_reanalysis")
    except Exception as exc:
        print(f"[api] weather cache write skipped: {exc}")
    source = "live-openmeteo" + ("+power-checked" if report else "")
    return df, source, report


def _design_from_request(req: SimulateRequest) -> dict:
    design = {}
    for field in ("orientation_deg", "length_m", "width_m", "height_m",
                  "wall_material", "wall_thickness_m", "roof_material",
                  "roof_thickness_m", "insulation_material",
                  "insulation_thickness_m", "window_wall", "window_width_m",
                  "window_height_m", "window_shgc", "window_u_w_m2k",
                  "ach"):
        value = getattr(req, field)
        if value is not None:
            design[field] = value
    return design


def _apply_design(cfg: dict, design: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    # map flat request fields onto the nested config schema
    flat_to_nested = {
        "insulation_material": ("insulation", "material"),
        "insulation_thickness_m": ("insulation", "thickness_m"),
        "window_wall": ("window", "wall"),
        "window_width_m": ("window", "width_m"),
        "window_height_m": ("window", "height_m"),
        "window_shgc": ("window", "shgc"),
        "window_u_w_m2k": ("window", "u_w_m2k"),
    }
    for k, v in design.items():
        if k == "ach":
            cfg["simulation"]["ventilation_ach"] = v
        elif k in flat_to_nested:
            section, field = flat_to_nested[k]
            cfg["shelter"][section][field] = v
        elif k in ("window", "door", "insulation"):
            cfg["shelter"][k].update(v)
        else:
            cfg["shelter"][k] = v
    return cfg


_GROUND_OFFSET_K = 2.0   # shallow-ground temp ≈ mean annual air temp + 2 K


def _apply_site_location(cfg: dict, lat: float, lon: float,
                         timezone: str | None = None) -> dict:
    """Set the simulation site's coordinates BEFORE running the engine.

    Solar geometry (SPA sun position) is computed from cfg['location'];
    previously it silently used the config default (Prayagraj) for every
    site — up to ~9 deg of solar-zenith error at Leh. Site-adapted data
    must use the site's own coordinates.
    """
    cfg = copy.deepcopy(cfg)
    cfg["location"]["latitude"] = float(lat)
    cfg["location"]["longitude"] = float(lon)
    if timezone:
        cfg["location"]["timezone"] = timezone
    return cfg


def _apply_ground_temp(cfg: dict, weather: pd.DataFrame) -> dict:
    """Site-adapted shallow-ground temperature for floor coupling.

    The config default (26 C) is a warm-climate placeholder; at
    cold-altitude sites (Leh, Kargil, Dras) a 26 C slab is physically
    wrong. Standard approximation (ASHRAE Handbook-Fundamentals,
    undisturbed-ground temperature ≈ mean annual air temperature + 1-3 K;
    also USDA soil-temperature practice) — we use MAAT + 2 K, rounded to
    0.1 C, computed from the REAL hourly weather of the site.
    """
    if "t2m" not in weather or len(weather) < 1000:
        return cfg
    cfg = copy.deepcopy(cfg)
    maat = float(weather["t2m"].mean())
    cfg["simulation"]["ground_temperature_c"] = round(maat + _GROUND_OFFSET_K, 1)
    return cfg


def _flat_design(cfg: dict) -> dict:
    """Resolved config -> flat design dict (single source for CAD/UI)."""
    s = cfg["shelter"]
    return {
        "length_m": s["length_m"], "width_m": s["width_m"],
        "height_m": s["height_m"], "orientation_deg": s["orientation_deg"],
        "wall_material": s["wall_material"],
        "wall_thickness_m": s["wall_thickness_m"],
        "roof_material": s["roof_material"],
        "roof_thickness_m": s["roof_thickness_m"],
        "floor_material": s.get("floor_material", "concrete"),
        "floor_thickness_m": s.get("floor_thickness_m", 0.1),
        "insulation_material": s["insulation"]["material"],
        "insulation_thickness_m": s["insulation"]["thickness_m"],
        "window_wall": s["window"]["wall"],
        "window_width_m": s["window"]["width_m"],
        "window_height_m": s["window"]["height_m"],
        "window_sill_m": s["window"].get("sill_height_m", 0.9),
        "window_shgc": s["window"].get("shgc", 0.82),
        "window_u_w_m2k": s["window"].get("u_w_m2k", 5.8),
    }


def _materials_map() -> dict:
    return {m["material"]: m for m in _canon_materials(STORE.list_materials())}


def _flat_design_with_overrides(request: Request | None) -> dict:
    """Flat design from config, with optional query-param overrides."""
    flat = _flat_design(CFG)
    if request is not None:
        for k, v in request.query_params.items():
            if k in flat and k not in ("format",):
                try:
                    flat[k] = float(v) if isinstance(flat[k], float) else (
                        int(v) if isinstance(flat[k], int) else v)
                except ValueError:
                    pass
    return flat


def _design_report(flat: dict, mat: dict, comps: list[dict]) -> str:
    asm = cad_model.assembly(flat, mat)
    surf = cad_model.surfaces(flat, comps)
    bb = cad_model.bounding_box(comps)
    ms = cad_model.mass(comps)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = []
    a = L.append
    a(f"# SIH26051 \u2014 SHELTER-STRUCTURE REPORT")
    a(f"")
    a(f"Generated: {now} \u00b7 Coordinate frame: x east, y north, z up, "
      f"origin at footprint centre (orientation rotates clockwise from above)")
    a(f"")
    a(f"## 1. Design parameters")
    a(f"")
    a(f"| Parameter | Value |")
    a(f"|---|---|")
    for k in ("length_m", "width_m", "height_m", "orientation_deg",
              "wall_material", "wall_thickness_m", "roof_material",
              "roof_thickness_m", "floor_material", "floor_thickness_m",
              "insulation_material", "insulation_thickness_m",
              "window_wall", "window_width_m", "window_height_m",
              "window_sill_m", "window_shgc", "window_u_w_m2k"):
        a(f"| {k} | {flat.get(k)} |")
    a(f"")
    a(f"## 2. Envelope & surfaces")
    a(f"")
    a(f"| Item | Value |")
    a(f"|---|---|")
    a(f"| Volume | {surf['volume_m3']} m\u00b3 |")
    a(f"| Floor | {surf['floor_m2']} m\u00b2 |")
    a(f"| Roof | {surf['roof_m2']} m\u00b2 |")
    a(f"| Gross wall | {surf['gross_wall_m2']} m\u00b2 |")
    a(f"| Window | {surf['window_m2']} m\u00b2 (glazing ratio "
      f"{surf['glazing_ratio_pct']} %) |")
    a(f"| Net opaque wall | {surf['net_opaque_wall_m2']} m\u00b2 |")
    a(f"| Total envelope mass (est.) | {ms['total_mass_kg']} kg |")
    a(f"")
    a(f"## 3. Envelope assembly (R = t/k, U = 1/\u03a3R, conductive only)")
    a(f"")
    for part in ("wall", "roof", "floor"):
        a(f"### {part.title()}")
        a(f"")
        a(f"| Layer | t (m) | k (W/mK) | R (m\u00b2K/W) |")
        a(f"|---|---|---|---|")
        for ly in asm[part]["layers"]:
            a(f"| {ly['material']} | {ly['thickness_m']} | "
              f"{ly['k_W_mK'] if ly['k_W_mK'] is not None else '\u2014'} | "
              f"{ly['r_m2K_W'] if ly['r_m2K_W'] is not None else '\u2014'} |")
        a(f"| **Total** | | | **{asm[part]['total_r_m2K_W']}** | "
          f"U = {asm[part]['u_w_m2k']} W/m\u00b2K")
        a(f"")
    a(f"### Window")
    a(f"")
    a(f"- U = {asm['window']['u_w_m2k']} W/m\u00b2K \u00b7 SHGC = {asm['window']['shgc']}")
    a(f"")
    a(f"## 4. Mass breakdown")
    a(f"")
    a(f"| Component | Material | Volume (m\u00b3) | Density (kg/m\u00b3) | Mass (kg) |")
    a(f"|---|---|---|---|---|")
    for r in ms["components"]:
        a(f"| {r['component']} | {r['material']} | {r['volume_m3']} | "
          f"{r['density_kg_m3'] if r['density_kg_m3'] is not None else '\u2014'} | "
          f"{r['mass_kg'] if r['mass_kg'] is not None else '\u2014'} |")
    a(f"")
    a(f"*{ms['note']} \u00b7 Units: metres, SI. Generated by Shelter-Studio "
      f"(SIH26051) \u2014 values derived from the sourced materials table.*")
    return "\n".join(L)


def _design_from_flat(flat: dict) -> dict:
    """Flat design dict -> SimulateRequest-style overrides."""
    return {k: v for k, v in flat.items()
            if k in ("orientation_deg", "length_m", "width_m", "height_m",
                     "wall_material", "wall_thickness_m", "roof_material",
                     "roof_thickness_m", "insulation_material",
                     "insulation_thickness_m", "window_wall",
                     "window_width_m", "window_height_m", "window_shgc")}


def _series_payload(res: pd.DataFrame, period: str) -> dict:
    """Compact time-series payload for the frontend."""
    if period == "full_year":
        m = res.resample("ME").mean()
        return {"mode": "monthly", "ts": [t.strftime("%Y-%m") for t in m.index],
                "indoor_t_c": [round(float(x), 2) for x in m["indoor_t_c"]],
                "outdoor_t_c": [round(float(x), 2) for x in m["outdoor_t_c"]],
                "q_solar_w": [round(float(x), 1) for x in m["q_solar_w"]]}
    return {"mode": "hourly",
            "ts": [t.strftime("%Y-%m-%dT%H:%M") for t in res.index],
            "indoor_t_c": [round(float(x), 2) for x in res["indoor_t_c"]],
            "outdoor_t_c": [round(float(x), 2) for x in res["outdoor_t_c"]],
            "q_solar_w": [round(float(x), 1) for x in res["q_solar_w"]],
            "q_conduct_w": [round(float(x), 1) for x in res["q_conduct_w"]],
            "q_vent_w": [round(float(x), 1) for x in res["q_vent_w"]]}


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------
@app.get("/")
def root():
    return {"service": "SIH26051 Shelter API",
            "docs": "/docs", "endpoints": ["/api/health", "/api/locations",
            "/api/materials", "/api/climate", "/api/simulate",
            "/api/optimize", "/api/simulations", "/api/optimizations"]}


@app.get("/api/health")
def health():
    return {"status": "ok", "backend": STORE.backend,
            "python": sys.version.split()[0]}


def _pick_locations(rows: list[dict]) -> list[dict]:
    """One dropdown entry per city (or per custom coordinate).

    Real cities are deduped by name — the offline-bundle pipeline keeps a
    second Leh row (34.164, 77.585) alongside the engine-canonical one
    (34.1526, 77.5771); showing both would put two "Leh" options in the
    select. The row whose coordinates match the canonical site wins.
    """
    try:
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        canon = {nm: (float(sd["latitude"]), float(sd["longitude"]))
                 for nm, sd in data.get("sites", {}).items()}
    except Exception:
        canon = {}
    by_key: dict = {}
    for r in rows:
        name = r.get("name", "")
        lat, lon = float(r.get("latitude", 0)), float(r.get("longitude", 0))
        if name in canon:
            key = name
        else:
            key = (round(lat, 4), round(lon, 4))
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = r
            continue
        c = canon.get(name)
        if c is None:
            continue
        d_cur = abs(lat - c[0]) + abs(lon - c[1])
        p_lat, p_lon = float(prev.get("latitude", 0)), float(prev.get("longitude", 0))
        d_prev = abs(p_lat - c[0]) + abs(p_lon - c[1])
        if d_cur < d_prev:
            by_key[key] = r
    return list(by_key.values())


@app.get("/api/locations")
def locations():
    """Site dropdown: everything the database knows, plus every site in the
    multi-year climate archive.

    The archive is merged in so the selector is populated from real coverage
    even on a cold database — previously the list depended on which sites
    happened to have been fetched before, which left a fresh deployment (or a
    fresh local SQLite) showing a single hardcoded location.
    """
    try:
        rows = STORE.list_locations()
    except Exception:
        rows = []

    known = {(round(float(r.get("latitude", 0)), 3),
              round(float(r.get("longitude", 0)), 3)) for r in rows}
    for name, meta in climate_archive.load_index().get("sites", {}).items():
        key = (round(float(meta["latitude"]), 3), round(float(meta["longitude"]), 3))
        if key in known:
            continue
        rows.append({
            "location_id": f"loc_{abs(meta['latitude']):.4f}_{abs(meta['longitude']):.4f}",
            "name": name,
            "latitude": meta["latitude"],
            "longitude": meta["longitude"],
            "elevation_m": meta.get("elevation_m", 0),
            "timezone": meta.get("timezone", "Asia/Kolkata"),
        })
        known.add(key)

    # The dropdown lists CURATED sites only. Every custom pin (and every
    # "detect my location" hit) is cached as a locations row so its weather
    # can be reused — but those are one user's ad-hoc coordinates, not part of
    # the project's reference set, and letting them accumulate turned a 15-site
    # selector into a 22-and-growing list of "Custom location" entries for
    # everybody. Curated = the multi-year archive plus the preset library.
    curated = {n.strip().lower()
               for n in climate_archive.load_index().get("sites", {})}
    try:
        curated |= {n.strip().lower() for n in
                    json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
                    .get("sites", {})}
    except Exception:                                       # noqa: BLE001
        pass
    if curated:
        rows = [r for r in rows
                if str(r.get("name", "")).strip().lower() in curated]

    out = _pick_locations(rows)
    out.sort(key=lambda r: r.get("name", ""))
    if out:
        source = STORE.backend if len(out) <= len(known) else f"{STORE.backend}+archive"
        return {"locations": out, "source": source}
    return {"locations": [DEFAULT_LOCATION], "source": "config"}


@app.get("/api/materials")
def materials():
    try:
        rows = STORE.list_materials()
        if rows:
            return {"materials": _canon_materials(rows), "source": STORE.backend}
    except Exception:
        pass
    mats = load_materials().reset_index()
    return {"materials": mats.to_dict(orient="records"), "source": "file"}


_CANON = {"k_w_mk": "k_W_mK", "cp_j_kgk": "cp_J_kgK"}   # postgres folds these


def _canon_materials(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({_CANON.get(k, k): v for k, v in r.items()})
    return out


@app.get("/api/climate")
def climate(lat: float | None = None, lon: float | None = None,
            year: str | None = None, timezone: str | None = None,
            force_refresh: bool = False):
    """Climate recon for a site.

    `year` accepts a calendar year ("2025") or "latest" (default) — the
    rolling 12-month window ending at the newest observed hour, which the
    scheduled refresh job keeps moving forward.
    """
    period = resolve_period(year)
    loc = {"latitude": lat if lat is not None else DEFAULT_LOCATION["latitude"],
           "longitude": lon if lon is not None else DEFAULT_LOCATION["longitude"],
           "timezone": timezone or DEFAULT_LOCATION["timezone"],
           "year": _period_year(period),
           "period": period}
    site = climate_archive.site_for(loc["latitude"], loc["longitude"])
    _s, _e, label = climate_archive.window_bounds(period, site)
    loc["site"] = site
    loc["period_label"] = label
    try:
        df, source, report = get_weather_cached(
            loc["latitude"], loc["longitude"], loc["year"], loc["timezone"],
            force_refresh=force_refresh, period=period)
    except Exception as exc:
        raise HTTPException(502, f"weather fetch failed: {exc}")

    monthly = df.resample("ME").mean()
    sample = df.tail(7 * 24)
    return {
        "location": loc, "source": source, "n_hours": len(df),
        "validation": report,
        "summary": {
            "t2m_min_c": round(float(df["t2m"].min()), 1),
            "t2m_mean_c": round(float(df["t2m"].mean()), 1),
            "t2m_max_c": round(float(df["t2m"].max()), 1),
            "ghi_mean_w_m2": round(float(df["ghi"].mean()), 1),
            "rh2m_mean_pct": round(float(df["rh2m"].mean()), 1),
            "ws10m_mean_m_s": round(float(df["ws10m"].mean()), 2),
        },
        "monthly": {"ts": [t.strftime("%Y-%m") for t in monthly.index],
                    "t2m": [round(float(x), 2) for x in monthly["t2m"]],
                    "ghi": [round(float(x), 1) for x in monthly["ghi"]]},
        "sample": {
            "mode": "hourly",
            "ts": [t.strftime("%Y-%m-%dT%H:%M") for t in sample.index],
            "t2m": [round(float(x), 2) for x in sample["t2m"]],
            "ghi": [round(float(x), 1) for x in sample["ghi"]],
            "rh2m": [round(float(x), 1) for x in sample["rh2m"]],
        },
    }


# --------------------------------------------------------------------------
# Multi-year climate: coverage + year-over-year trends  (SEC/10)
# --------------------------------------------------------------------------
@app.get("/api/climate/coverage")
def climate_coverage():
    """What climate data exists, and how fresh it is.

    Drives the UI's period selector and the "data current through" badge, so
    the frontend never hardcodes a year again.
    """
    cov = climate_archive.coverage()
    cov["backend"] = STORE.backend
    latest = cov.get("latest_hour_utc")
    if latest:
        try:
            age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(latest))
            cov["age_hours"] = round(age.total_seconds() / 3600, 1)
            cov["is_fresh"] = age <= pd.Timedelta(days=14)
        except Exception:
            pass
    years = sorted({int(y) for s in cov["sites"].values() for y in s["years"]})
    cov["options"] = ([{"value": "latest", "label": "LATEST · rolling 12 months",
                        "default": True}]
                      + [{"value": str(y), "label": str(y),
                          "default": False} for y in reversed(years)])
    return cov


@app.get("/api/climate/trends")
def climate_trends(site: str | None = None, lat: float | None = None,
                   lon: float | None = None):
    """Year-over-year climate comparison for one site.

    Every year is truncated to the same day-of-year span before the statistics
    are computed, so an in-progress year is never compared against a full one
    (otherwise "this year is hotter" would just mean "this year stops in
    September, before winter").

    Served from the precomputed bundle — no 350k-row scan per request.
    """
    if site is None and lat is not None and lon is not None:
        site = climate_archive.site_for(lat, lon)
    if not site:
        raise HTTPException(400, "unknown site: pass ?site=Name or ?lat=&lon=")

    bundle = climate_archive.load_bundle()
    entry = bundle.get("sites", {}).get(site)
    if not entry:
        raise HTTPException(404, f"no multi-year archive for site '{site}'")

    trend = entry.get("trend") or {}
    if not trend:
        raise HTTPException(404, f"not enough years archived for '{site}'")

    rows = trend.get("years", [])
    newest = rows[-1] if rows else {}
    return {
        "site": site,
        "latitude": entry.get("latitude"),
        "longitude": entry.get("longitude"),
        "timezone": entry.get("timezone"),
        "source": bundle.get("source"),
        "generated_on": bundle.get("generated_on"),
        "latest_hour_utc": bundle.get("latest_hour_utc"),
        "comparable_through_doy": trend.get("comparable_through_doy"),
        "note": trend.get("comparable_note"),
        "years": rows,
        "delta": trend.get("delta", {}),
        "series": {
            "years": [r["year"] for r in rows],
            "t2m_mean_c": [r["t2m_mean_c"] for r in rows],
            "t2m_max_c": [r["t2m_max_c"] for r in rows],
            "hours_above_35c": [r["hours_above_35c"] for r in rows],
            "hours_below_0c": [r["hours_below_0c"] for r in rows],
            "cdd18": [r["cdd18"] for r in rows],
            "hdd18": [r["hdd18"] for r in rows],
            "precip_total_mm": [r["precip_total_mm"] for r in rows],
        },
        "monthly_by_year": {
            y: entry["years"][y]["monthly"]
            for y in sorted(entry.get("years", {}))
        },
        "latest": {
            "label": entry.get("latest", {}).get("label"),
            "summary": entry.get("latest", {}).get("summary", {}),
        },
        "design_shift": _design_shift(entry),
        "newest_year": newest.get("year"),
    }


def _design_shift(entry: dict) -> dict:
    """How the *design case* itself moved between the oldest and newest year.

    Shelter sizing keys off the hottest/coldest week, not the annual mean — if
    the hot week is 1.5 C warmer than it was, the envelope spec has to follow.
    """
    years = entry.get("years", {})
    keys = sorted(years)
    if len(keys) < 2:
        return {}
    out = {}
    for kind in ("hot", "cold"):
        a = years[keys[0]].get("design_weeks", {}).get(kind, {})
        b = years[keys[-1]].get("design_weeks", {}).get(kind, {})
        if not (a.get("t2m") and b.get("t2m")):
            continue
        pick = max if kind == "hot" else min
        out[kind] = {
            "from_year": int(keys[0]), "to_year": int(keys[-1]),
            "from_week": f"{a.get('start')} → {a.get('end')}",
            "to_week": f"{b.get('start')} → {b.get('end')}",
            "from_peak_c": round(pick(a["t2m"]), 1),
            "to_peak_c": round(pick(b["t2m"]), 1),
            "peak_delta_c": round(pick(b["t2m"]) - pick(a["t2m"]), 1),
            "from_mean_c": round(sum(a["t2m"]) / len(a["t2m"]), 1),
            "to_mean_c": round(sum(b["t2m"]) / len(b["t2m"]), 1),
            "mean_delta_c": round(sum(b["t2m"]) / len(b["t2m"])
                                  - sum(a["t2m"]) / len(a["t2m"]), 1),
        }
    return out


@app.post("/api/simulate")
def simulate_endpoint(req: SimulateRequest):
    loc = _loc(req)
    period = req.period if req.period in ("hot_week", "cold_week", "full_year") \
        else "hot_week"
    try:
        weather, source, _ = get_weather_cached(
            loc["latitude"], loc["longitude"], loc["year"], loc["timezone"],
            period=loc["period"])
    except Exception as exc:
        raise HTTPException(502, f"weather fetch failed: {exc}")

    cfg = _apply_ground_temp(_apply_site_location(
        _apply_design(CFG, _design_from_request(req)),
        loc["latitude"], loc["longitude"], loc["timezone"]), weather)
    mats = load_materials()

    # validate material names
    for key in ("wall_material", "roof_material"):
        mat = cfg["shelter"][key]
        if mat not in mats.index:
            raise HTTPException(400, f"unknown {key}: {mat!r}")
    ins = cfg["shelter"]["insulation"]["material"]
    if ins != "none" and ins not in mats.index:
        raise HTTPException(400, f"unknown insulation material: {ins!r}")

    try:
        if period == "full_year":
            res = simulate(cfg, weather, mats)
        else:
            weeks = design_weeks(weather, loc["year"])
            res = simulate(cfg, weeks[period], mats)
    except Exception as exc:
        raise HTTPException(500, f"simulation failed: {exc}")

    stats = comfort_stats(res, cfg["climate"]["comfort_range_c"])
    if period == "full_year" and "indoor_t_c" in res:
        lo, hi = cfg["climate"]["comfort_range_c"]
        in_band = (res["indoor_t_c"] >= lo) & (res["indoor_t_c"] <= hi)
        by_month = in_band.groupby(res.index.month).mean()
        stats["monthly_comfort"] = [
            {"month": int(m), "comfort_fraction": round(float(by_month.get(m, 0.0)), 3)}
            for m in range(1, 13)]
    design = {k: v for k, v in cfg["shelter"].items() if k != "door"}

    sim_id = new_id("sim")
    try:
        STORE.save_simulation(sim_id, None, design, period, stats,
                              res if len(res) <= 8760 else None)
    except Exception as exc:
        print(f"[api] simulation save skipped: {exc}")

    return {
        "sim_id": sim_id, "location": loc, "period": period,
        "weather_source": source, "design": design, "metrics": stats,
        "series": _series_payload(res, period),
    }


@app.post("/api/optimize")
def optimize_endpoint(req: OptimizeRequest):
    loc = _loc(req)
    try:
        weather, source, _ = get_weather_cached(
            loc["latitude"], loc["longitude"], loc["year"], loc["timezone"],
            period=loc["period"])
    except Exception as exc:
        raise HTTPException(502, f"weather fetch failed: {exc}")

    from src.optimization.optuna_optimizer import run_study
    try:
        study = run_study(_apply_ground_temp(_apply_site_location(
            CFG, loc["latitude"], loc["longitude"], loc["timezone"]), weather),
            weather, load_materials(),
                          n_trials=req.n_trials)
    except Exception as exc:
        raise HTTPException(500, f"optimization failed: {exc}")

    best = study.best_trial
    best_design = dict(best.params)
    best_design["window"] = {
        "wall": best_design.pop("window_wall"),
        "width_m": best_design.pop("window_width_m"),
        "height_m": best_design.pop("window_height_m"),
        "shgc": best_design.pop("window_shgc"),
    }
    trials = [{"trial_no": t.number, "tpi": round(1.0 - t.value, 4),
               "params": t.params} for t in study.trials]
    top10 = sorted(trials, key=lambda t: t["tpi"], reverse=True)[:10]
    history = [round(1.0 - t.value, 4) for t in study.trials]

    run_id = new_id("opt")
    try:
        STORE.save_optimization(run_id, None, req.n_trials,
                                1.0 - best.value, best_design, trials)
    except Exception as exc:
        print(f"[api] optimization save skipped: {exc}")

    return {
        "run_id": run_id, "location": loc, "weather_source": source,
        "n_trials": len(trials),
        "best": {"tpi": round(1.0 - best.value, 4), "design": best_design},
        "top10": top10, "history": history,
    }


# --------------------------------------------------------------------------
# accounts — signup / login / per-user persistence
# --------------------------------------------------------------------------
_AUTH_TTL = 30 * 24 * 3600          # 30-day sessions
_PBKDF2_ITER = 260_000


def _auth_secret() -> str:
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or os.environ.get("SUPABASE_ANON_KEY") or "sih26051-dev")
    return hashlib.sha256(f"sih26051::{key}".encode()).hexdigest()


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(),
                               bytes.fromhex(salt), _PBKDF2_ITER).hex()


def _mint_token(user_id: str) -> str:
    payload = f"{user_id}.{int(time.time()) + _AUTH_TTL}"
    sig = hmac.new(_auth_secret().encode(), payload.encode(),
                   hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}.{sig}".encode()).decode()


def _verify_token(token: str) -> str | None:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        user_id, exp, sig = raw.rsplit(".", 2)
        payload = f"{user_id}.{exp}"
        expect = hmac.new(_auth_secret().encode(), payload.encode(),
                          hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        if int(exp) < time.time():
            return None
        return user_id
    except Exception:
        return None


def _uid(request: Request) -> str | None:
    """User id from the Authorization bearer token, or None (guest)."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return _verify_token(auth[7:].strip())
    return None


def _user_or_401(request: Request) -> str:
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "authentication required")
    return uid


def _owns(row: dict | None, uid: str | None) -> bool:
    """row's owner is uid, or both are guests (None)."""
    return bool(row) and (row.get("user_id") or None) == uid


class AuthRequest(BaseModel):
    username: str = Field(min_length=3, max_length=24)
    password: str = Field(min_length=6, max_length=128)


@app.post("/api/auth/signup", status_code=201)
def auth_signup(req: AuthRequest):
    username = req.username.strip().lower()
    if not username.replace("_", "").replace("-", "").replace(".", "").isalnum():
        raise HTTPException(400, "username may contain only letters, digits, _ - .")
    if STORE.get_user_by_username(username):
        raise HTTPException(409, "username already taken")
    user_id = new_id("usr")
    salt = secrets.token_hex(16)
    try:
        STORE.create_user(user_id, username, _hash_password(req.password, salt),
                          salt)
    except Exception as exc:
        raise HTTPException(500, f"account creation failed: {exc}")
    return {"token": _mint_token(user_id), "user": {"user_id": user_id,
                                                    "username": username}}


@app.post("/api/auth/login")
def auth_login(req: AuthRequest):
    username = req.username.strip().lower()
    user = STORE.get_user_by_username(username)
    if not user:
        raise HTTPException(401, "invalid username or password")
    got = _hash_password(req.password, user["salt"])
    if not hmac.compare_digest(got, user["pass_hash"]):
        raise HTTPException(401, "invalid username or password")
    return {"token": _mint_token(user["user_id"]),
            "user": {"user_id": user["user_id"], "username": user["username"]}}


@app.get("/api/auth/me")
def auth_me(request: Request):
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "not authenticated")
    user = STORE.get_user(uid)
    if not user:
        raise HTTPException(401, "account no longer exists")
    return {"user": {"user_id": user["user_id"], "username": user["username"]}}


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    # stateless sessions — client discards the token
    return {"ok": True}


# --------------------------------------------------------------------------
# shelter fleet management
# --------------------------------------------------------------------------
_SHELTER_STATUSES = {"planned", "deployed", "maintenance", "retired"}


class ShelterRecord(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    location_name: str = ""
    latitude: float | None = None
    longitude: float | None = None
    design: dict | None = None
    status: str = "planned"
    notes: str = ""
    compute: bool = True


def _shelter_metrics(latitude: float, longitude: float,
                     design: dict) -> dict | None:
    """Predicted hot-week comfort + climate zone for a shelter design at a
    location (same sourced RC engine the simulator uses; never fabricated)."""
    try:
        weather, source, _ = get_weather_cached(
            latitude, longitude, _period_year("latest"),
            CFG["location"]["timezone"], period="latest")
        mats = load_materials()
        base = dict(design)
        for k in ("length_m", "width_m", "height_m", "orientation_deg",
                  "wall_thickness_m", "roof_thickness_m",
                  "insulation_thickness_m", "window_width_m",
                  "window_height_m", "window_shgc"):
            v = base.get(k)
            if isinstance(v, str):
                try:
                    base[k] = float(v)
                except ValueError:
                    base.pop(k, None)
        ins = base.get("insulation_material") or "eps"
        if ins == "none":
            base["insulation_material"] = "eps"
        if base["insulation_material"] not in mats.index:
            base.pop("insulation_material", None)
            base.pop("insulation_thickness_m", None)
        for key in ("wall_material", "roof_material"):
            if base.get(key) not in mats.index:
                base[key] = "brick" if key == "wall_material" else "rcc_slab"
        cfg = _apply_ground_temp(_apply_site_location(
            _apply_design(CFG, base), latitude, longitude), weather)
        weeks = design_weeks(weather, int(CFG["climate"]["data_year"]))
        res = simulate(cfg, weeks["hot_week"], mats)
        st = comfort_stats(res, cfg["climate"]["comfort_range_c"])
        st = {k: (round(float(v), 3) if isinstance(v, float) else v)
              for k, v in st.items()}
        st["period"] = "hot_week"
        st["weather_source"] = source
        st["zone"] = _location_profile(weather, latitude, longitude).get("zone")
        st["zone_name"] = _ZONE_NAMES.get(st["zone"], st["zone"])
        return st
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/shelters")
def shelters_list(request: Request, limit: int = 200):
    return {"shelters": STORE.list_shelters(limit=limit, user_id=_uid(request))}


@app.post("/api/shelters")
def shelters_create(req: ShelterRecord, request: Request):
    if req.status not in _SHELTER_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(_SHELTER_STATUSES)}")
    uid = _uid(request)
    metrics = None
    if req.compute and req.latitude is not None and req.longitude is not None \
            and req.design:
        metrics = _shelter_metrics(req.latitude, req.longitude, req.design)
    record = {"shelter_id": new_id("shl"), "name": req.name, "user_id": uid,
              "location_name": req.location_name,
              "latitude": req.latitude, "longitude": req.longitude,
              "design": req.design or {}, "status": req.status,
              "notes": req.notes, "metrics": metrics}
    STORE.save_shelter(record)
    row = STORE.get_shelter(record["shelter_id"])
    return row


@app.get("/api/shelters/{shelter_id}")
def shelters_get(shelter_id: str, request: Request):
    row = STORE.get_shelter(shelter_id)
    if not _owns(row, _uid(request)):
        raise HTTPException(404, "shelter not found")
    return row


@app.patch("/api/shelters/{shelter_id}")
def shelters_patch(shelter_id: str, request: Request, body: dict):
    row = STORE.get_shelter(shelter_id)
    if not _owns(row, _uid(request)):
        raise HTTPException(404, "shelter not found")
    patch = {}
    for k in ("name", "location_name", "latitude", "longitude", "design",
              "status", "deployed_at", "notes"):
        if k in body and body[k] is not None:
            patch[k] = body[k]
    if "status" in patch and patch["status"] not in _SHELTER_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(_SHELTER_STATUSES)}")
    if "latitude" in patch:
        patch["latitude"] = float(patch["latitude"])
    if "longitude" in patch:
        patch["longitude"] = float(patch["longitude"])
    recompute = bool(body.get("compute")) and \
        (patch.get("design") or patch.get("latitude") or patch.get("longitude"))
    if recompute:
        lat = patch.get("latitude", row["latitude"])
        lon = patch.get("longitude", row["longitude"])
        design = patch.get("design", row["design"])
        if lat is not None and lon is not None and design:
            patch["metrics"] = _shelter_metrics(lat, lon, design)
    return STORE.update_shelter(shelter_id, patch)


@app.delete("/api/shelters/{shelter_id}")
def shelters_delete(shelter_id: str, request: Request):
    row = STORE.get_shelter(shelter_id)
    if not _owns(row, _uid(request)):
        raise HTTPException(404, "shelter not found")
    STORE.delete_shelter(shelter_id)
    return {"deleted": shelter_id}


# --------------------------------------------------------------------------
# location profiling + site-adapted shelter design
# --------------------------------------------------------------------------
_ZONE_NAMES = {"hot_dry": "HOT-DRY", "warm_humid": "WARM-HUMID",
               "composite": "COMPOSITE", "temperate": "TEMPERATE",
               "cold": "COLD"}

_ZONE_GUIDANCE = {
    "hot_dry": [
        "High thermal-mass walls and roof to damp the large diurnal swing",
        "Insulate the roof and west-facing wall; keep exterior light-coloured",
        "Small windows on north/south with shading — avoid west glazing",
        "Night-time ventilation to flush stored heat (high diurnal range)",
        "Minimise solar aperture during the day (shading devices, overhangs)",
    ],
    "warm_humid": [
        "Shade every glazed opening — direct sun is the main heat source",
        "Maximise cross-ventilation; keep ACH high (moisture + heat removal)",
        "Moderate insulation only — avoid trapping humidity with heavy mass",
        "Ventilated / elevated roof construction to shed solar gain",
        "Light-coloured, reflective exterior surfaces",
    ],
    "composite": [
        "Thermal mass + insulation for the long hot-dry season",
        "Design for strong cross-ventilation during the monsoon humidity",
        "North/south glazing with shading; avoid east/west apertures",
        "Insulated roof is the single most effective measure",
        "Operable openings for seasonal ventilation control",
    ],
    "temperate": [
        "South-facing glazing to harvest winter solar gain",
        "Moderate insulation; lightweight construction is acceptable",
        "Shade summer sun with fixed overhangs sized for the season",
        "Small diurnal range — thermal mass matters less than solar control",
    ],
    "cold": [
        "Insulate the whole envelope heavily (walls + roof + floor edge)",
        "South-facing glazing for passive solar gain; limit north apertures",
        "Low air-change rate — minimise infiltration and heat loss",
        "Use a better glazing if available (lower U-value)",
    ],
}


def _monthly_means(df: pd.DataFrame, min_days: int = 20) -> pd.Series:
    """Monthly mean temperature, ignoring part-months.

    A rolling window starts mid-month, so `resample("ME")` yields a stub bucket
    at each end (e.g. 7 days of September). Those stubs are not months and must
    not be allowed to become the hottest/coldest month of the year.
    """
    if "t2m" not in df or df.empty:
        return pd.Series(dtype=float)
    g = df["t2m"].resample("ME")
    means = g.mean()
    counts = g.count()
    keep = counts >= min_days * 24
    return means[keep] if keep.any() else means


def _monthly_normals(df: pd.DataFrame, min_days: int = 20) -> pd.Series:
    """Climatological monthly normals: mean of each calendar month across years.

    Taking min/max over every individual month of a multi-year archive would
    return the single most extreme month ever recorded, which gets colder the
    more years you add — the opposite of a normal. Averaging January-with-
    January first is what "coldest month" means in NBC/ECBC.
    """
    monthly = _monthly_means(df, min_days)
    if monthly.empty:
        return monthly
    return monthly.groupby(monthly.index.month).mean()


def _zone_from(df: pd.DataFrame) -> tuple[str, float, float, float]:
    """NBC 2016 / ECBC 2017-style zone from a temperature+humidity series."""
    normals = _monthly_normals(df)
    if len(normals):
        t_hot, t_cold = float(normals.max()), float(normals.min())
    else:
        t = df["t2m"].dropna()
        t_hot = float(t.max()) if len(t) else 0.0
        t_cold = float(t.min()) if len(t) else 0.0
    rh = df["rh2m"].dropna() if "rh2m" in df else pd.Series(dtype=float)
    rh_ann = float(rh.mean()) if len(rh) else 0.0

    if t_cold <= 14.0:
        zone = "cold"
    elif t_hot >= 30.0 and rh_ann < 50.0:
        zone = "hot_dry"
    elif t_hot >= 27.0 and rh_ann >= 65.0:
        zone = "warm_humid"
    elif t_hot < 30.0 and rh_ann < 65.0:
        zone = "temperate"
    else:
        zone = "composite"
    return zone, t_hot, t_cold, rh_ann


def _zone_reference(lat: float | None, lon: float | None,
                    fallback: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Series to classify the climatic zone from.

    Zones are a property of the *climate*, not of one year: NBC/ECBC zones are
    defined on long-term normals. Classifying off a single rolling window makes
    the label flip whenever a knife-edge year nudges a threshold (Jaisalmer's
    coldest month at 13.8 C vs 15.6 C reads as "cold" vs "hot_dry"). So when the
    site is in the multi-year archive, classify on every year we hold and let
    the selected period drive the *design* numbers only.
    """
    if lat is None or lon is None:
        return fallback, "selected-period"
    site = climate_archive.site_for(lat, lon)
    if not site:
        return fallback, "selected-period"
    try:
        full = climate_archive.read_local(site)
        if full is not None and len(full) > 17000:      # >= ~2 years
            tz = climate_archive.load_index()["sites"][site].get(
                "timezone", "Asia/Kolkata")
            full = full.copy()
            full.index = full.index.tz_convert(tz)
            n_years = int(full.index.year.nunique())
            return full, f"{n_years}-year normal"
    except Exception:                                    # noqa: BLE001
        pass
    return fallback, "selected-period"


def _location_profile(weather: pd.DataFrame, lat: float | None = None,
                      lon: float | None = None) -> dict:
    """Characterize a site from its real hourly weather series.
    Zone classification follows an NBC 2016 / ECBC 2017-style climatic-zones
    approximation using monthly mean temperature + annual mean humidity,
    computed over every archived year (a climatological normal, not one
    knife-edge year); every other indicator (degree-days, diurnal range,
    solar, wind, rain) is computed directly from the selected period's
    sourced hourly data — nothing is fabricated or averaged across sources.
    """
    df = weather
    t = df["t2m"].dropna() if "t2m" in df else pd.Series(dtype=float)
    rh = df["rh2m"].dropna() if "rh2m" in df else pd.Series(dtype=float)
    ghi = df["ghi"].dropna() if "ghi" in df else pd.Series(dtype=float)
    ws = df["ws10m"].dropna() if "ws10m" in df else pd.Series(dtype=float)
    pr = df["precip"].dropna() if "precip" in df else pd.Series(dtype=float)

    zone_df, zone_basis = _zone_reference(lat, lon, df)
    zone, t_hot, t_cold, rh_ann = _zone_from(zone_df)

    daily = df.resample("D").agg(tmax=("t2m", "max"), tmin=("t2m", "min")) \
        if "t2m" in df else pd.DataFrame({"tmax": [], "tmin": []})
    diurnal = float((daily["tmax"] - daily["tmin"]).mean()) if len(daily) else 0.0

    hdd = float(((18.0 - t).clip(lower=0)).sum() / 24.0) if len(t) else 0.0
    cdd = float(((t - 18.0).clip(lower=0)).sum() / 24.0) if len(t) else 0.0

    wet_hours = float((pr > 0.1).mean() * 100.0) if len(pr) else 0.0
    monsoon_share = 0.0
    if len(pr):
        wet_mask = pr > 0.1
        if wet_mask.any():
            monsoon_share = float(
                pr[wet_mask & (pr.index.month.isin([6, 7, 8, 9]))].size
                / wet_mask.sum() * 100.0)

    # wind rose — 16 compass sectors from the hourly wind direction/speed
    rose = []
    if "ws10m" in df and "wd10m" in df and len(df):
        wd = df["wd10m"].dropna()
        ws = df["ws10m"].dropna()
        if len(wd) and len(ws):
            joined = pd.concat([wd, ws], axis=1).dropna()
            if len(joined):
                total = len(joined)
                for i in range(16):
                    lo = i * 22.5 - 11.25
                    hi = i * 22.5 + 11.25
                    if i == 0:
                        mask = (joined["wd10m"] >= 348.75) | (joined["wd10m"] < 11.25)
                    else:
                        mask = (joined["wd10m"] >= lo) & (joined["wd10m"] < hi)
                    sect = joined[mask]
                    rose.append({
                        "sector": i, "center_deg": round(i * 22.5, 1),
                        "label": ["N", "NNE", "NE", "ENE", "E", "ESE", "SE",
                                  "SSE", "S", "SSW", "SW", "WSW", "W", "WNW",
                                  "NW", "NNW"][i],
                        "freq_pct": round(float(sect.shape[0]) / total * 100.0, 2),
                        "mean_ws_ms": round(float(sect["ws10m"].mean()), 2)
                        if len(sect) else 0.0,
                    })

    # diurnal profile — mean temperature by hour of day (local time)
    diurnal_curve = []
    if "t2m" in df and len(df):
        by_hour = df.groupby(df.index.hour)["t2m"].mean()
        for h in range(24):
            diurnal_curve.append({"hour": h,
                                  "mean_c": round(float(by_hour.get(h, float("nan"))), 2)})

    return {
        "zone": zone,
        "zone_name": _ZONE_NAMES[zone],
        "zone_basis": zone_basis,
        "n_hours": int(len(df)),
        "t_mean_c": round(float(t.mean()), 2) if len(t) else None,
        "t_hottest_month_c": round(t_hot, 2),
        "t_coldest_month_c": round(t_cold, 2),
        "diurnal_range_c": round(diurnal, 2),
        "hdd18": round(hdd, 1),
        "cdd18": round(cdd, 1),
        "rh_mean_pct": round(rh_ann, 1),
        "ghi_mean_w_m2": round(float(ghi.mean()), 1) if len(ghi) else None,
        "wind_mean_ms": round(float(ws.mean()), 2) if len(ws) else None,
        "wet_hours_pct": round(wet_hours, 1),
        "monsoon_share_pct": round(monsoon_share, 1),
        "wind_rose": rose,
        "diurnal": diurnal_curve,
        "guidance": _ZONE_GUIDANCE[zone],
    }


def _recommend_design(profile: dict, mats: pd.DataFrame,
                      current: dict | None = None) -> dict:
    """Physics-informed design prescription for the site's climate zone.
    Concrete values come from the sourced materials table and config ranges;
    predicted performance is validated by the RC engine (see /api/location/
    recommend), never asserted by the rule table itself.
    """
    zone = profile["zone"]
    table = {
        # zone: ins mat, ins mm, wall mat, wall m, roof mat, win wall, win size, ach
        "hot_dry":    ("eps", 100, "brick", 0.23, "rcc_slab", "north", (0.8, 0.8), 6.0),
        "warm_humid": ("eps", 50, "brick", 0.20, "rcc_slab", "north", (1.0, 1.0), 10.0),
        "composite":  ("eps", 75, "brick", 0.23, "rcc_slab", "north", (1.0, 1.0), 8.0),
        "temperate":  ("eps", 40, "brick", 0.20, "rcc_slab", "south", (1.2, 1.2), 4.0),
        "cold":       ("xps", 100, "brick", 0.23, "rcc_slab", "south", (1.2, 1.2), 1.5),
    }[zone]
    ins_mat, ins_mm, wall_mat, wall_m, roof_mat, win_wall, win_size, ach = table

    # guard: only recommend materials that exist in the sourced table
    if ins_mat not in mats.index:
        ins_mat = "eps"
    if wall_mat not in mats.index:
        wall_mat = "brick"
    if roof_mat not in mats.index:
        roof_mat = "rcc_slab"

    cur = current or {}
    design = {
        "length_m": cur.get("length_m") or 3.0,
        "width_m": cur.get("width_m") or 3.0,
        "height_m": cur.get("height_m") or 2.6,
        "orientation_deg": 0,
        "wall_material": wall_mat, "wall_thickness_m": wall_m,
        "roof_material": roof_mat, "roof_thickness_m": 0.12,
        "floor_material": cur.get("floor_material") or "concrete",
        "floor_thickness_m": cur.get("floor_thickness_m") or 0.1,
        "insulation_material": ins_mat,
        "insulation_thickness_m": ins_mm / 1000.0,
        "window_wall": win_wall,
        "window_width_m": win_size[0], "window_height_m": win_size[1],
        "window_sill_m": 0.9, "window_shgc": 0.82, "window_u_w_m2k": 5.8,
        "ach": ach,
    }

    why = {
        "orientation_deg": "Long axis east-west: east/west faces get the least low-angle sun exposure",
        "wall_material": "Masonry mass dampens the outdoor temperature swing seen indoors",
        "wall_thickness_m": "Thicker mass wall raises thermal lag and cuts peak heat flux",
        "roof_material": "Concrete slab roof carries insulation and matches sourced construction practice",
        "insulation_material": "Polymer foam board (sourced k about 0.03-0.04 W/mK) with the highest comfort per rupee",
        "insulation_thickness_m": f"Tuned to this zone's cooling degree-days (CDD18 ~ {profile['cdd18']})",
        "window_wall": ("North glazing admits diffuse light with minimal direct solar gain"
                        if win_wall == "north" else
                        "South glazing harvests winter sun when the sun stays low in the sky"),
        "window_width_m": "Glazing kept small in hot seasons; sized for daylight in temperate/cold zones",
        "ach": "Ventilation rate matched to humidity regime: cross-flow for humid zones, sealed for cold",
    }
    rationale = [{"parameter": k, "value": v, "why": why.get(k, "")}
                 for k, v in design.items() if k in why]
    return {"design": design, "rationale": rationale,
            "guidance": _ZONE_GUIDANCE[zone], "zone": zone,
            "zone_name": _ZONE_NAMES[zone]}


class AdaptRequest(SimulateRequest):
    design: dict | None = None


@app.post("/api/location/profile")
def location_profile(req: AdaptRequest):
    """Site characteristics from real hourly weather: climate zone
    (NBC 2016-style), degree-days, diurnal range, humidity, solar, wind."""
    loc = _loc(req)
    try:
        weather, source, _ = get_weather_cached(
            loc["latitude"], loc["longitude"], loc["year"], loc["timezone"],
            period=loc["period"])
    except Exception as exc:
        raise HTTPException(502, f"weather fetch failed: {exc}")
    prof = _location_profile(weather, loc["latitude"], loc["longitude"])
    prof["location"] = loc
    prof["weather_source"] = source
    return prof


@app.post("/api/location/recommend")
def location_recommend(req: AdaptRequest):
    """Site-adapted design: profile the location, prescribe a climate-zone
    design from the sourced materials table, then validate it against the
    current design with the same RC thermal engine (hot week)."""
    loc = _loc(req)
    try:
        weather, source, _ = get_weather_cached(
            loc["latitude"], loc["longitude"], loc["year"], loc["timezone"],
            period=loc["period"])
    except Exception as exc:
        raise HTTPException(502, f"weather fetch failed: {exc}")
    mats = load_materials()
    prof = _location_profile(weather, loc["latitude"], loc["longitude"])
    current = req.design or _flat_design(CFG)
    rec = _recommend_design(prof, mats, current)

    def run_metrics(design: dict) -> dict:
        base = dict(design)
        base.setdefault("orientation_deg", 0)
        for k in ("length_m", "width_m", "height_m", "wall_thickness_m",
                  "roof_thickness_m", "insulation_thickness_m",
                  "window_width_m", "window_height_m", "window_shgc"):
            v = base.get(k)
            if isinstance(v, str):
                try:
                    base[k] = float(v)
                except ValueError:
                    base.pop(k, None)
        ins = base.get("insulation_material") or "none"
        if ins == "none":
            base["insulation_material"] = "eps"
            base["insulation_thickness_m"] = 0.0
        cfg = _apply_ground_temp(_apply_site_location(
            _apply_design(CFG, base), loc["latitude"], loc["longitude"],
            loc["timezone"]), weather)
        try:
            weeks = design_weeks(weather, loc["year"])
            res = simulate(cfg, weeks["hot_week"], mats)
        except Exception as exc:
            raise HTTPException(500, f"validation simulation failed: {exc}")
        st = comfort_stats(res, cfg["climate"]["comfort_range_c"])
        return {k: (round(float(v), 3) if isinstance(v, float) else v)
                for k, v in st.items()}

    base_metrics = run_metrics(current)
    rec_metrics = run_metrics(rec["design"])
    _peak = lambda m: (m.get("max_indoor_c") or m.get("peak_indoor_c") or 0)
    delta = {
        "mean_indoor_c": round(rec_metrics.get("mean_indoor_c", 0)
                               - base_metrics.get("mean_indoor_c", 0), 2),
        "max_indoor_c": round(_peak(rec_metrics) - _peak(base_metrics), 2),
    }
    prof["location"] = loc
    prof["weather_source"] = source
    return {"location": loc, "weather_source": source, "profile": prof,
            "recommendation": rec, "baseline_metrics": base_metrics,
            "recommended_metrics": rec_metrics, "delta": delta}


# --------------------------------------------------------------------------
# multi-zone validation — the same shelter across India's climate zones
# --------------------------------------------------------------------------
REFERENCE_CITIES = [
    {"name": "Prayagraj", "lat": 25.4358, "lon": 81.8463},
    {"name": "Jaisalmer", "lat": 26.9157, "lon": 70.9083},
    {"name": "Chennai", "lat": 13.0827, "lon": 80.2707},
    {"name": "Bengaluru", "lat": 12.9716, "lon": 77.5946},
    {"name": "Leh", "lat": 34.1526, "lon": 77.5771},
]


def _coerce_design(base: dict) -> dict:
    """Normalise a design dict coming from the UI (strings -> floats)."""
    d = dict(base)
    for k in ("length_m", "width_m", "height_m", "orientation_deg",
              "wall_thickness_m", "roof_thickness_m", "insulation_thickness_m",
              "window_width_m", "window_height_m", "window_shgc"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = float(v)
            except ValueError:
                d.pop(k, None)
    return d


def _design_metrics(weather: pd.DataFrame, design: dict,
                    mats: pd.DataFrame, lat: float | None = None,
                    lon: float | None = None,
                    timezone: str | None = None) -> dict:
    """Hot-week comfort metrics for a design at a site (sourced RC engine).

    Solar geometry uses the SITE's coordinates (previously the config
    default — up to 9 deg zenith error at high-latitude sites).
    """
    base = _coerce_design(design)
    base.setdefault("orientation_deg", 0)
    ins = base.get("insulation_material") or "none"
    if ins == "none":
        base["insulation_material"] = "eps"
        base["insulation_thickness_m"] = 0.0
    cfg = _apply_ground_temp(_apply_site_location(
        _apply_design(CFG, base),
        lat if lat is not None else float(CFG["location"]["latitude"]),
        lon if lon is not None else float(CFG["location"]["longitude"]),
        timezone), weather)
    weeks = design_weeks(weather, int(CFG["climate"]["data_year"]))
    res = simulate(cfg, weeks["hot_week"], mats)
    st = comfort_stats(res, cfg["climate"]["comfort_range_c"])
    return {k: (round(float(v), 3) if isinstance(v, float) else v)
            for k, v in st.items()}


class CompareRequest(SimulateRequest):
    design: dict | None = None
    sites: list[dict] | None = None


@app.post("/api/location/compare")
def location_compare(req: CompareRequest):
    """Run the same shelter design against real weather in up to 5
    reference cities spanning India's climate zones. Every number is
    computed by the sourced RC engine on that site's actual hourly
    weather — a true cross-zone validation."""
    mats = load_materials()
    design = req.design or _flat_design(CFG)
    sites = req.sites or REFERENCE_CITIES
    if not sites or len(sites) > 8:
        raise HTTPException(400, "sites must hold 1-8 entries")
    out = []
    for s in sites:
        name = s.get("name") or f"{s['lat']},{s['lon']}"
        try:
            weather, source, _ = get_weather_cached(
                float(s["lat"]), float(s["lon"]),
                _period_year("latest"), CFG["location"]["timezone"],
                period="latest")
        except Exception as exc:
            out.append({"site": name, "error": f"weather fetch failed: {exc}"})
            continue
        prof = _location_profile(weather, float(s["lat"]), float(s["lon"]))
        try:
            metrics = _design_metrics(weather, design, mats,
                                      float(s["lat"]), float(s["lon"]),
                                      CFG["location"]["timezone"])
        except Exception as exc:
            out.append({"site": name, "zone": prof["zone"],
                        "zone_name": prof["zone_name"], "error": str(exc)})
            continue
        out.append({
            "site": name, "latitude": s["lat"], "longitude": s["lon"],
            "zone": prof["zone"], "zone_name": prof["zone_name"],
            "t_hottest_month_c": prof["t_hottest_month_c"],
            "t_coldest_month_c": prof["t_coldest_month_c"],
            "diurnal_range_c": prof["diurnal_range_c"],
            "rh_mean_pct": prof["rh_mean_pct"],
            "cdd18": prof["cdd18"], "hdd18": prof["hdd18"],
            "wind_mean_ms": prof["wind_mean_ms"],
            "weather_source": source,
            **metrics,
        })
    valid = [r for r in out if "error" not in r]
    best = min(valid, key=lambda r: r.get("mean_indoor_c", 1e9)) if valid else None
    return {"design": design, "sites": out,
            "best": {"site": best["site"], "zone_name": best["zone_name"],
                     "mean_indoor_c": best["mean_indoor_c"]} if best else None,
            "note": "Each site uses its own real hourly weather (NASA POWER + "
                    "Open-Meteo cross-check) and the same sourced RC engine."}


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# pre-designed shelters (engine-verified presets)
# --------------------------------------------------------------------------
PRESETS_FILE = ROOT / "src" / "data" / "shelter_presets.json"


@app.get("/api/presets")
def presets_list(site: str = "Prayagraj"):
    """Engine-verified shelter presets for a known site.

    Numbers come from scripts/build_presets.py: every preset was run
    through the same sourced RC engine on real hourly weather of each
    site, so the metrics below are engine truth (not AI estimates).
    """
    try:
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(503, "presets not built yet "
                                 "(run scripts/build_presets.py)")
    site_data = data["sites"].get(site)
    if site_data is None:
        site, site_data = "Prayagraj", data["sites"]["Prayagraj"]
    presets = []
    for p in data["presets"]:
        presets.append({
            "id": p["id"], "name": p["name"], "tagline": p["tagline"],
            "zones": p["zones"], "design": p["design"],
            "rationale": p["rationale"],
            "metrics": site_data.get(p["id"]),
        })
    return {
        "site": site,
        "latitude": site_data.get("latitude"),
        "longitude": site_data.get("longitude"),
        "zone": site_data.get("zone"),
        "zone_name": site_data.get("zone_name"),
        "weather_source": site_data.get("weather_source"),
        "engine": data.get("engine"),
        "generated_on": data.get("generated_on"),
        "presets": presets,
    }


# AI design assistant (optional accelerator — engine stays the truth)
# --------------------------------------------------------------------------
class AiSuggestRequest(SimulateRequest):
    design: dict | None = None
    objective: str = "coolest_peak"   # coolest_peak | max_comfort | coolest_mean
    n_candidates: int = 800


@app.get("/api/ai/info")
def ai_info():
    """Model card: what the AI was trained on and how accurate it is."""
    try:
        mm = model_metrics()
        meta = model_meta()
    except FileNotFoundError:
        raise HTTPException(503, "AI model not trained yet")
    return {
        "model": meta,
        "accuracy": mm.get("targets", {}),
        "features": FEATURES,
        "targets": TARGETS,
        "note": "The AI is a surrogate trained on the sourced RC engine's "
                "own output (real hourly weather, 12 Indian cities). Its "
                "numbers are ESTIMATES with the disclosed MAE above — the "
                "engine remains the source of truth. Manual mode is always "
                "available; verify any AI result with the engine.",
    }


def _ai_weather_profile(req: SimulateRequest):
    loc = _loc(req)
    weather, source, _ = get_weather_cached(
        loc["latitude"], loc["longitude"], loc["year"], loc["timezone"],
        period=loc["period"])
    prof = _location_profile(weather, loc["latitude"], loc["longitude"])
    prof["location"] = loc
    prof["weather_source"] = source
    return weather, prof


@app.post("/api/ai/predict")
def ai_predict(req: AdaptRequest):
    """Instant comfort estimates for a design at a site (surrogate)."""
    try:
        model_metrics()
    except FileNotFoundError:
        raise HTTPException(503, "AI model not trained yet")
    weather, prof = _ai_weather_profile(req)
    mats = load_materials()
    design = req.design or _flat_design(CFG)
    out = predict_design(design, prof, mats)
    out["design"] = design
    out["profile"] = {k: prof[k] for k in (
        "zone_name", "t_hottest_month_c", "t_coldest_month_c",
        "diurnal_range_c", "cdd18", "hdd18", "wind_mean_ms",
        "ghi_mean_w_m2", "weather_source")}
    out["note"] = ("AI estimate (surrogate of the sourced RC engine, MAE "
                   "disclosed in /api/ai/info). Run the engine to verify.")
    return out


@app.post("/api/ai/suggest")
def ai_suggest(req: AiSuggestRequest):
    """AI-suggested design for a site + objective, engine-verified.

    The surrogate ranks ~n_candidates sampled designs instantly; the top
    candidates are then verified with the REAL engine (same hot-week
    simulation /api/location/compare uses) so the returned metrics are
    engine truth, not estimates.
    """
    try:
        model_metrics()
    except FileNotFoundError:
        raise HTTPException(503, "AI model not trained yet")
    if req.objective not in ("coolest_peak", "max_comfort", "coolest_mean"):
        raise HTTPException(400, "objective must be coolest_peak | "
                                 "max_comfort | coolest_mean")
    weather, prof = _ai_weather_profile(req)
    mats = load_materials()
    rng = np.random.default_rng(26051)
    candidates = [sample_design(rng) for _ in range(max(50, req.n_candidates))]

    X = np.asarray([build_features(d, prof, mats) for d in candidates],
                   dtype=float)
    p = predict_batch(X)
    if req.objective == "coolest_peak":
        rank = np.argsort(p["hot_max_c"])
    elif req.objective == "coolest_mean":
        rank = np.argsort(p["hot_mean_c"])
    else:
        rank = np.argsort(-p["hot_comfort_fraction"])

    top = [candidates[i] for i in rank[:12]]
    verified = [_design_metrics(weather, d, mats,
                                 prof["location"]["latitude"],
                                 prof["location"]["longitude"],
                                 prof["location"]["timezone"]) for d in top]
    score = {
        "coolest_peak": lambda m: m["max_indoor_c"],
        "coolest_mean": lambda m: m["mean_indoor_c"],
        "max_comfort": lambda m: -m["comfort_fraction"],
    }[req.objective]
    order = sorted(range(len(top)), key=lambda i: score(verified[i]))
    best_i = order[0]
    best = top[best_i]
    est = predict_design(best, prof, mats)["estimates"]
    return {
        "objective": req.objective,
        "design": best,
        "estimates": est,
        "verified": {k: verified[best_i][k] for k in
                     ("mean_indoor_c", "max_indoor_c", "comfort_fraction")},
        "alternatives": [
            {"design": top[i], "verified": {
                k: verified[i][k] for k in
                ("mean_indoor_c", "max_indoor_c", "comfort_fraction")}}
            for i in order[1:4]],
        "profile": {"zone_name": prof["zone_name"],
                    "weather_source": prof["weather_source"]},
        "note": "Top candidates were re-verified with the real RC engine; "
                "the 'verified' block is engine truth.",
    }


# --------------------------------------------------------------------------
# design library / stats / sweep / report
# --------------------------------------------------------------------------
class DesignRecord(BaseModel):
    design_id: str | None = None
    name: str = "Design"
    design: dict
    notes: str = ""
    favorite: bool = False


@app.post("/api/designs")
def design_save(req: DesignRecord, request: Request):
    did = req.design_id or new_id("dsg")
    uid = _uid(request)
    STORE.save_design({"design_id": did, "name": req.name,
                       "design": req.design, "notes": req.notes,
                       "favorite": req.favorite}, user_id=uid)
    return {"design_id": did, "name": req.name, "saved_at":
            "now", "design": req.design, "user_id": uid}


@app.get("/api/designs")
def design_list(limit: int = 100, favorite_only: bool = False,
                request: Request = None):
    rows = STORE.list_designs(limit=limit, favorite_only=favorite_only,
                              user_id=_uid(request))
    return {"designs": rows}


@app.get("/api/designs/{design_id}")
def design_get(design_id: str, request: Request):
    row = STORE.get_design(design_id)
    if not _owns(row, _uid(request)):
        raise HTTPException(404, "design not found")
    return row


@app.patch("/api/designs/{design_id}")
def design_patch(design_id: str, body: dict, request: Request):
    row = STORE.get_design(design_id)
    if not _owns(row, _uid(request)):
        raise HTTPException(404, "design not found")
    merged = dict(row)
    for k in ("name", "design", "notes", "favorite"):
        if k in body:
            merged[k] = body[k]
    STORE.save_design(merged, user_id=row.get("user_id"))
    return STORE.get_design(design_id)


@app.delete("/api/designs/{design_id}")
def design_delete(design_id: str, request: Request):
    row = STORE.get_design(design_id)
    if not _owns(row, _uid(request)):
        raise HTTPException(404, "design not found")
    STORE.delete_design(design_id)
    return {"deleted": design_id}


@app.get("/api/stats")
def stats():
    return {
        "simulations": STORE.count_rows("simulations"),
        "optimizations": STORE.count_rows("optimization_runs"),
        "designs": STORE.count_rows("designs"),
        "cad_imports": STORE.count_rows("cad_imports"),
        "shelters": STORE.count_rows("shelters"),
        "users": STORE.count_rows("sih_users"),
    }


class SweepRequest(SimulateRequest):
    thicknesses_mm: list[float] | None = None


@app.post("/api/sweep")
def sweep_endpoint(req: SweepRequest):
    """Insulation-thickness sensitivity sweep (hottest week, RC model).
    Scans a thickness grid and returns the thermal metrics per point."""
    loc = _loc(req)
    try:
        weather, source, _ = get_weather_cached(
            loc["latitude"], loc["longitude"], loc["year"], loc["timezone"],
            period=loc["period"])
    except Exception as exc:
        raise HTTPException(502, f"weather fetch failed: {exc}")
    mats = load_materials()
    base = _design_from_request(req)
    ins_mat = base.get("insulation_material") or "eps"
    if ins_mat == "none":
        ins_mat = "eps"     # sweep needs a real insulation material
    grid = sorted({float(t) for t in (req.thicknesses_mm or [0, 25, 50, 75, 100])})
    grid = [t for t in grid if 0.0 <= t <= 300.0]
    if not grid or len(grid) > 10:
        raise HTTPException(400, "thicknesses_mm must hold 1-10 values in [0, 300]")
    if ins_mat not in mats.index:
        raise HTTPException(400, f"unknown insulation material: {ins_mat!r}")
    points = []
    try:
        weeks = design_weeks(weather, loc["year"])
    except Exception as exc:
        raise HTTPException(500, f"design weeks failed: {exc}")
    for t in grid:
        design = dict(base)
        design["insulation_material"] = ins_mat
        design["insulation_thickness_m"] = t / 1000.0
        cfg = _apply_design(CFG, design)
        try:
            res = simulate(cfg, weeks["hot_week"], mats)
        except Exception as exc:
            raise HTTPException(500, f"sweep point {t} mm failed: {exc}")
        st = comfort_stats(res, cfg["climate"]["comfort_range_c"])
        points.append({"thickness_mm": t,
                       **{k: (round(float(v), 3) if isinstance(v, float) else v)
                          for k, v in st.items()}})
    return {"location": loc, "period": "hot_week", "weather_source": source,
            "insulation_material": ins_mat, "points": points,
            "best": min(points, key=lambda p: p["mean_indoor_c"])}


@app.get("/api/cad/report")
def cad_report(request: Request = None):
    """Downloadable markdown report of the current design (structure,
    assembly, surfaces, mass)."""
    flat = _flat_design_with_overrides(request)
    mat = _materials_map()
    comps = cad_model.build_components(flat, mat)
    text = _design_report(flat, mat, comps)
    fname = (f"shelter-report-{flat['length_m']}x{flat['width_m']}"
             f"x{flat['height_m']}m.md")
    return Response(content=text, media_type="text/markdown", headers={
        "Content-Disposition": f'attachment; filename="{fname}"'})


# --------------------------------------------------------------------------
# CAD / digital structure
# --------------------------------------------------------------------------
@app.post("/api/cad/structure")
def cad_structure(req: SimulateRequest):
    """Digital structure of the shelter — generated from the design
    parameters (this is the 'create yourself' path: no CAD file needed)."""
    flat = _flat_design(_apply_design(CFG, _design_from_request(req)))
    mat = _materials_map()
    comps = cad_model.build_components(flat, mat)
    return {
        "design": {k: (round(v, 4) if isinstance(v, float) else v)
                   for k, v in flat.items()},
        "generated": True,
        "coordinate_frame": "x east, y north, z up; origin at footprint centre; "
                            "orientation rotates clockwise viewed from above",
        "components": comps,
        "assembly": cad_model.assembly(flat, mat),
        "bounding": cad_model.bounding_box(comps),
        "surfaces": cad_model.surfaces(flat, comps),
        "mass": cad_model.mass(comps),
        "thermal_mass": _thermal_mass(flat, mat),
    }


def _thermal_mass(flat: dict, mat: dict) -> dict:
    """Assembly thermal lag (time constant tau = R*C) and the decrement
    factor of a 24 h sinusoidal excitation, computed from sourced material
    properties (k, rho, cp) — no invented values. DF = 1/sqrt(1+(omega*tau)^2)
    is the standard lumped-wall approximation (ASHRAE-style treatment)."""
    def layer(mat_name: str | None, thickness: float) -> dict | None:
        m = mat.get(mat_name) if mat_name else None
        if not m or thickness <= 0:
            return None
        k, rho, cp = m.get("k_W_mK"), m.get("density_kg_m3"), m.get("cp_J_kgK")
        if not k or not rho or not cp:
            return None
        r = thickness / k
        c = rho * cp * thickness
        return {"R": r, "C": c}

    def assembly_lag(layers: list[dict | None]) -> dict | None:
        R = sum(l["R"] for l in layers if l)
        C = sum(l["C"] for l in layers if l)
        if not R or not C:
            return None
        tau_h = R * C / 3600.0
        omega = 2.0 * math.pi / 24.0          # one cycle per day
        df = 1.0 / math.sqrt(1.0 + (omega * tau_h) ** 2)
        return {"lag_hours": round(tau_h, 2),
                "decrement_factor": round(df, 4)}

    wall_layers = [layer(flat.get("wall_material"), flat.get("wall_thickness_m")),
                   layer(flat.get("insulation_material")
                         if flat.get("insulation_material") != "none" else None,
                         flat.get("insulation_thickness_m"))]
    roof_layers = [layer(flat.get("roof_material"), flat.get("roof_thickness_m")),
                   layer(flat.get("insulation_material")
                         if flat.get("insulation_material") != "none" else None,
                         flat.get("insulation_thickness_m"))]
    wall = assembly_lag(wall_layers)
    roof = assembly_lag(roof_layers)
    out = {}
    if wall:
        out["wall_assembly_lag_hours"] = wall["lag_hours"]
        out["wall_decrement_factor"] = wall["decrement_factor"]
    if roof:
        out["roof_assembly_lag_hours"] = roof["lag_hours"]
        out["roof_decrement_factor"] = roof["decrement_factor"]
    out["note"] = ("Thermal lag = R·C time constant of the assembly; decrement "
                   "factor = attenuation of a 24 h outdoor swing through the "
                   "assembly (1/sqrt(1+(2·pi·tau/24)^2)). Both computed from "
                   "sourced k, density and specific heat.")
    return out


@app.get("/api/cad/export")
def cad_export(format: str = "dxf", request: Request = None):
    """Export the digital structure as DXF / OBJ / STL (auto-generated
    from the current design parameters; optional query overrides)."""
    fmt = (format or "dxf").lower().lstrip(".")
    if fmt not in ("dxf", "obj", "stl"):
        raise HTTPException(400, "format must be dxf | obj | stl")
    flat = _flat_design_with_overrides(request)
    mat = _materials_map()
    comps = cad_model.build_components(flat, mat)
    L, W, H = flat["length_m"], flat["width_m"], flat["height_m"]
    fname = f"shelter-{L}x{W}x{H}m-o{flat['orientation_deg']}.{fmt}"
    if fmt == "dxf":
        content, media = cad_dxf.write_dxf(flat, comps), "application/dxf"
    elif fmt == "obj":
        content, media = cad_mesh.write_obj(flat, comps), "model/obj"
    else:
        content, media = cad_mesh.write_stl(flat, comps), "model/stl"
    return Response(content=content, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{fname}"'})


@app.post("/api/cad/import")
def cad_import(file: UploadFile = File(...)):
    """Ingest a CAD file (DXF / OBJ / STL) from any source channel and
    extract the shelter dimensions. Recorded in cad_imports."""
    try:
        data = file.file.read()
        summary = cad_ingest.ingest(file.filename or "upload", data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    summary["filename"] = file.filename or "upload"
    summary["suggested_design"] = cad_ingest.suggest_design(summary)
    summary["import_id"] = new_id("cad")
    try:
        STORE.save_cad_import(summary)
    except Exception:
        pass  # ingestion logging must never break the import flow
    return summary


@app.get("/api/cad/imports")
def cad_imports(limit: int = 8):
    """Recent CAD ingestion log (all source channels)."""
    return {"imports": STORE.list_cad_imports(limit=limit)}


@app.get("/api/simulations")
def simulations(limit: int = 10):
    try:
        return {"simulations": STORE.list_simulations(limit)}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/optimizations")
def optimizations(limit: int = 5):
    try:
        return {"optimizations": STORE.list_optimizations(limit)}
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ---------------------------------------------------------------------------
# offline mode — a full-fledged offline edition of the studio, downloadable
# only after a LOGIN or an explicit GUEST CHECK.
# ---------------------------------------------------------------------------
OFFLINE_TEMPLATE = ROOT / "offline" / "offline_app_template.html"
OFFLINE_ENGINE = ROOT / "offline" / "engine.js"
OFFLINE_BUNDLE = ROOT / "src" / "data" / "offline_bundle.json"
_offline_app: bytes | None = None
_offline_app_at: float = 0.0


def _build_offline_app() -> bytes:
    """Assemble the self-contained offline app (template + engine + bundle)
    once, then serve from memory. Rebuilds when any source file changes."""
    global _offline_app, _offline_app_at
    try:
        mtime = max(OFFLINE_TEMPLATE.stat().st_mtime,
                    OFFLINE_ENGINE.stat().st_mtime,
                    OFFLINE_BUNDLE.stat().st_mtime)
    except OSError as exc:
        raise HTTPException(
            503, "offline bundle not built (run scripts/build_offline_bundle.py)") \
            from exc
    if _offline_app is not None and mtime <= _offline_app_at:
        return _offline_app
    bundle = OFFLINE_BUNDLE.read_text(encoding="utf-8").replace("</", "<\\/")
    engine = OFFLINE_ENGINE.read_text(encoding="utf-8")
    html = (OFFLINE_TEMPLATE.read_text(encoding="utf-8")
            .replace("/*__BUNDLE__*/", bundle)
            .replace("/*__ENGINE__*/", engine))
    _offline_app = html.encode("utf-8")
    _offline_app_at = mtime
    return _offline_app


class OfflineDownloadRequest(BaseModel):
    mode: str = "guest"   # "login" (requires bearer token) or "guest"


@app.get("/api/offline/info")
def offline_info():
    """What the offline edition contains (for the download UI)."""
    bundle = {}
    try:
        bundle = json.loads(OFFLINE_BUNDLE.read_text(encoding="utf-8"))
    except Exception:
        pass
    size = 0
    try:
        size = len(_build_offline_app())
    except Exception:
        size = 0
    return {
        "available": bool(bundle),
        "size_bytes": size,
        "sites": len(bundle.get("sites", [])),
        "materials": len(bundle.get("materials", [])),
        "presets": len(bundle.get("presets", {}).get("designs", [])),
        "model": bundle.get("model", {}).get("metadata", {}).get("name"),
        "n_samples": bundle.get("model", {}).get("metadata", {}).get("n_samples"),
        "generated_on": bundle.get("generated_on"),
    }


@app.post("/api/offline/download")
def offline_download(req: OfflineDownloadRequest, request: Request):
    """Download the offline edition of the studio.

    Gated: mode='login' requires a valid session token; mode='guest' is
    the explicit guest check (anyone can use it — guests are anonymous by
    design). The returned file is a single self-contained HTML application:
    design, materials, simulate, AI suggest, presets, geometry views and
    local persistence all run with zero network afterwards.
    """
    mode = (req.mode or "").strip().lower()
    if mode not in ("login", "guest"):
        raise HTTPException(400, "mode must be 'login' or 'guest'")
    if mode == "login" and not _uid(request):
        raise HTTPException(401, "authentication required — log in first")
    body = _build_offline_app()
    return Response(
        content=body,
        media_type="text/html",
        headers={
            "Content-Disposition":
                'attachment; filename="shelter-studio-offline.html"',
            "Content-Length": str(len(body)),
            "X-Offline-Mode": mode,
            "Cache-Control": "no-store",
        })
