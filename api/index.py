"""SIH26051 Shelter API — Vercel Python serverless (FastAPI/ASGI).

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

import copy
import json
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.climate import (design_weeks, load_config,  # noqa: E402
                              cross_check)
from src.data import nasa_power, openmeteo  # noqa: E402
from src.db.store import Store, new_id  # noqa: E402
from src.thermal.rc_model import (comfort_stats, load_materials,  # noqa: E402
                                  simulate)

app = FastAPI(title="SIH26051 Shelter API", version="1.0.0")
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


# --------------------------------------------------------------------------
# request models
# --------------------------------------------------------------------------
class SimulateRequest(BaseModel):
    lat: float | None = None
    lon: float | None = None
    year: int | None = None
    timezone: str | None = None
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
    ach: float | None = None


class OptimizeRequest(BaseModel):
    lat: float | None = None
    lon: float | None = None
    year: int | None = None
    timezone: str | None = None
    n_trials: int = Field(30, ge=5, le=60)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _loc(req: BaseModel) -> dict:
    return {
        "latitude": req.lat if req.lat is not None else DEFAULT_LOCATION["latitude"],
        "longitude": req.lon if req.lon is not None else DEFAULT_LOCATION["longitude"],
        "timezone": req.timezone or DEFAULT_LOCATION["timezone"],
        "year": req.year or int(CFG["climate"]["data_year"]),
    }


def get_weather_cached(lat: float, lon: float, year: int, timezone: str,
                       force_refresh: bool = False):
    """Weather from Supabase cache if present, else live POWER + Open-Meteo.

    Returns (df, source, validation_report).
    """
    location_id = f"loc_{abs(lat):.4f}_{abs(lon):.4f}"
    if not force_refresh:
        try:
            cached = STORE.load_weather(location_id)
            if cached is not None:
                cached.index = cached.index.tz_convert(timezone)
                # hourly rows may straddle the UTC year boundary — filter on
                # the LOCAL year (POWER LST hours => :30-offset local stamps)
                cached = cached[cached.index.year == year]
                if len(cached) > 8000:
                    return cached, "supabase-cache", None
        except Exception:
            pass

    power = nasa_power.hourly_to_dataframe(nasa_power.fetch_hourly(
        lat, lon, f"{year}0101", f"{year}1231"))
    om = openmeteo.fetch_hourly(lat, lon, f"{year}-01-01", f"{year}-12-31",
                                timezone="UTC")
    report = cross_check(power, om)
    df = power.copy()
    df.index = df.index.tz_convert(timezone)
    try:
        STORE.upsert_location({**DEFAULT_LOCATION, "latitude": lat,
                               "longitude": lon,
                               "location_id": location_id})
        STORE.save_weather(power, location_id)      # store in UTC
    except Exception as exc:
        print(f"[api] weather cache write skipped: {exc}")
    return df, "live-power+openmeteo", report


def _design_from_request(req: SimulateRequest) -> dict:
    design = {}
    for field in ("orientation_deg", "length_m", "width_m", "height_m",
                  "wall_material", "wall_thickness_m", "roof_material",
                  "roof_thickness_m", "insulation_material",
                  "insulation_thickness_m", "window_wall", "window_width_m",
                  "window_height_m", "window_shgc", "ach"):
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


@app.get("/api/locations")
def locations():
    try:
        rows = STORE.list_locations()
        if rows:
            return {"locations": rows, "source": STORE.backend}
    except Exception:
        pass
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
            year: int | None = None, timezone: str | None = None,
            force_refresh: bool = False):
    loc = {"latitude": lat if lat is not None else DEFAULT_LOCATION["latitude"],
           "longitude": lon if lon is not None else DEFAULT_LOCATION["longitude"],
           "timezone": timezone or DEFAULT_LOCATION["timezone"],
           "year": year or int(CFG["climate"]["data_year"])}
    try:
        df, source, report = get_weather_cached(
            loc["latitude"], loc["longitude"], loc["year"], loc["timezone"],
            force_refresh=force_refresh)
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


@app.post("/api/simulate")
def simulate_endpoint(req: SimulateRequest):
    loc = _loc(req)
    period = req.period if req.period in ("hot_week", "cold_week", "full_year") \
        else "hot_week"
    try:
        weather, source, _ = get_weather_cached(
            loc["latitude"], loc["longitude"], loc["year"], loc["timezone"])
    except Exception as exc:
        raise HTTPException(502, f"weather fetch failed: {exc}")

    cfg = _apply_design(CFG, _design_from_request(req))
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
            loc["latitude"], loc["longitude"], loc["year"], loc["timezone"])
    except Exception as exc:
        raise HTTPException(502, f"weather fetch failed: {exc}")

    from src.optimization.optuna_optimizer import run_study
    try:
        study = run_study(CFG, weather, load_materials(),
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
