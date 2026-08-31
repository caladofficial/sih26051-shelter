"""Build the offline bundle: everything the standalone offline app needs.

Produces src/data/offline_bundle.json — a single self-contained data file
with:

  * materials        — the full sourced materials table (materials.csv)
  * model            — the AI surrogate (ai_model.json, trees as embedded)
  * sites            — 14 reference sites, each with its climate profile
                       AND its real hourly hot/cold design weeks, with
                       site-correct solar geometry (zenith/azimuth) and
                       ERBS dni/dhi — so the offline engine can simulate
                       without any network or SPA reimplementation
  * presets          — curated preset designs (src/presets.py) plus the
                       engine-verified per-site numbers (shelter_presets.json)

The offline app (offline/offline_app_template.html + offline/engine.js)
is assembled from this bundle at API startup (src/api_app.py) and served
only after a login or guest check (POST /api/offline/download).

Usage:  python scripts/build_offline_bundle.py   (from repo root)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.api_app import (CFG, _apply_ground_temp, _location_profile,  # noqa: E402
                         get_weather_cached)
from src.data import solar  # noqa: E402
from src.data.climate import design_weeks  # noqa: E402
from src.presets import PRESETS  # noqa: E402

MATERIALS_CSV = ROOT / "data" / "external" / "materials.csv"
MODEL_JSON = ROOT / "src" / "data" / "ai_model.json"
PRESETS_JSON = ROOT / "src" / "data" / "shelter_presets.json"
OUT = ROOT / "src" / "data" / "offline_bundle.json"

# 14 reference sites (same list as the training set / API)
SITES = [
    ("Prayagraj", 25.4358, 81.8463), ("Leh", 34.164, 77.585),
    ("Jaisalmer", 26.9137, 70.9127), ("Chennai", 13.0827, 80.2707),
    ("Dras", 34.4296, 75.7497), ("Kargil", 34.5591, 76.1278),
    ("Delhi", 28.6139, 77.2090), ("Ahmedabad", 23.0225, 72.5714),
    ("Mumbai", 19.0760, 72.8777), ("Kolkata", 22.5726, 88.3639),
    ("Bengaluru", 12.9716, 77.5946), ("Hyderabad", 17.3850, 78.4867),
    ("Pune", 18.5204, 73.8567), ("Srinagar", 34.0837, 74.7973),
]

TIMEZONE = "Asia/Kolkata"
YEAR = int(CFG["climate"]["data_year"])


def load_materials() -> list[dict]:
    out = []
    with open(MATERIALS_CSV, encoding="utf-8") as fh:
        header = [h.strip() for h in fh.readline().strip().split(",")]
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # quoted fields contain commas — parse conservatively
            parts, cur, in_q = [], "", False
            for ch in line:
                if ch == '"':
                    in_q = not in_q
                elif ch == "," and not in_q:
                    parts.append(cur.strip())
                    cur = ""
                else:
                    cur += ch
            parts.append(cur.strip())
            if len(parts) < len(header):
                continue
            row = dict(zip(header, parts))
            out.append({
                "name": row["material"],
                "category": row["category"],
                "k_W_mK": float(row["k_W_mK"]),
                "density_kg_m3": float(row["density_kg_m3"]),
                "cp_J_kgK": float(row["cp_J_kgK"]),
                "solar_absorptance": float(row["solar_absorptance"]),
                "emissivity": float(row["emissivity"]),
                "thickness_m": float(row["thickness_m"]),
                "source": row["source"],
                "notes": row.get("notes", ""),
            })
    return out


def embed_week(wk, lat: float, lon: float, ground_c: float) -> dict:
    """Hourly design week with site-correct solar geometry (like the parity
    fixtures): the offline engine consumes these arrays directly."""
    zen = solar.solar_position(wk.index, lat, lon)
    zenith = zen["apparent_zenith"].clip(upper=89.9)
    azimuth = zen["azimuth"]
    ghi = wk["ghi"].clip(lower=0.0)
    erbs = solar.erbs(ghi, zenith, wk.index)
    return {
        "t2m": [round(float(x), 3) for x in wk["t2m"].interpolate().ffill().bfill()],
        "ghi": [round(float(x), 2) for x in ghi],
        "dni": [round(float(x), 2) for x in erbs["dni"].clip(lower=0).fillna(0)],
        "dhi": [round(float(x), 2) for x in erbs["dhi"].clip(lower=0).fillna(0)],
        "zenith_deg": [round(float(x), 3) for x in zenith],
        "azimuth_deg": [round(float(x), 3) for x in azimuth],
        "hour_of_day": [int(h) for h in wk.index.hour],
        "start_minute": int(wk.index[0].minute),
        "ground_temperature_c": ground_c,
        "albedo": float(CFG["simulation"]["albedo"]),
    }


def main() -> None:
    materials = load_materials()
    model = json.loads(MODEL_JSON.read_text(encoding="utf-8"))
    presets_file = json.loads(PRESETS_JSON.read_text(encoding="utf-8"))

    sites = []
    for name, lat, lon in SITES:
        weather, source, _ = get_weather_cached(lat, lon, YEAR, TIMEZONE)
        prof = _location_profile(weather)
        ground = _apply_ground_temp(CFG, weather)["simulation"]["ground_temperature_c"]
        weeks = design_weeks(weather, YEAR)
        sites.append({
            "name": name,
            "latitude": lat, "longitude": lon,
            "timezone": TIMEZONE, "year": YEAR,
            "weather_source": source,
            "profile": {
                "t_hottest_month_c": prof["t_hottest_month_c"],
                "t_coldest_month_c": prof["t_coldest_month_c"],
                "diurnal_range_c": prof["diurnal_range_c"],
                "rh_mean_pct": prof["rh_mean_pct"],
                "cdd18": prof["cdd18"], "hdd18": prof["hdd18"],
                "ghi_mean_w_m2": prof["ghi_mean_w_m2"],
                "wind_mean_ms": prof["wind_mean_ms"],
                "zone": prof["zone"], "zone_name": prof["zone_name"],
            },
            "ground_temperature_c": ground,
            "weeks": {
                "hot_week": embed_week(weeks["hot_week"], lat, lon, ground),
                "cold_week": embed_week(weeks["cold_week"], lat, lon, ground),
            },
        })
        print(f"[bundle] {name}: profile + hot/cold weeks embedded "
              f"({source})", flush=True)

    bundle = {
        "schema_version": 1,
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "engine": "offline/engine.js — RC port of src/thermal/rc_model.py "
                  "(parity-verified), AI surrogate from src/ai_model.py",
        "materials": materials,
        "model": model,
        "sites": sites,
        "presets": {
            "designs": PRESETS,
            "results": presets_file,
        },
    }
    OUT.write_text(json.dumps(bundle, separators=(",", ":")), encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"[bundle] -> {OUT} ({kb:,.0f} KB, {len(materials)} materials, "
          f"{len(sites)} sites, {len(PRESETS)} presets)")


if __name__ == "__main__":
    main()
