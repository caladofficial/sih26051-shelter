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
import os
import secrets
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# project root: src/api_app.py -> parents[2] (api/index.py -> parents[1])
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.climate import (design_weeks, load_config,  # noqa: E402
                              cross_check)
from src.data import nasa_power, openmeteo  # noqa: E402
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
    """Predicted hot-week comfort for a shelter design at a location
    (same sourced RC engine the simulator uses; never fabricated)."""
    try:
        weather, source, _ = get_weather_cached(
            latitude, longitude, int(CFG["climate"]["data_year"]),
            CFG["location"]["timezone"])
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
        cfg = _apply_design(CFG, base)
        weeks = design_weeks(weather, int(CFG["climate"]["data_year"]))
        res = simulate(cfg, weeks["hot_week"], mats)
        st = comfort_stats(res, cfg["climate"]["comfort_range_c"])
        st = {k: (round(float(v), 3) if isinstance(v, float) else v)
              for k, v in st.items()}
        st["period"] = "hot_week"
        st["weather_source"] = source
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
            loc["latitude"], loc["longitude"], loc["year"], loc["timezone"])
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
    }


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
