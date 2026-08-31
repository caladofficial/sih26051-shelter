"""Engine-verify every shelter preset at every site and cache the results.

Each preset from src/presets.py is simulated with the same sourced RC
engine (design_weeks + simulate + comfort_stats) on the REAL hourly
weather of each of the 14 dataset sites. The output
src/data/shelter_presets.json therefore contains ENGINE TRUTH numbers
per (preset, site) — the UI shows these, never estimates.

Run:  python3 scripts/build_presets.py
Output: src/data/shelter_presets.json
"""

import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from src.api_app import (_apply_design, _apply_ground_temp, CFG,  # noqa: E402
                         _location_profile, get_weather_cached)
from src.data.climate import design_weeks  # noqa: E402
from src.presets import PRESETS  # noqa: E402
from src.thermal.rc_model import comfort_stats, load_materials, simulate  # noqa: E402

SITES = [
    ("Prayagraj", 25.4358, 81.8463), ("Delhi", 28.6139, 77.2090),
    ("Jaisalmer", 26.9157, 70.9083), ("Ahmedabad", 23.0225, 72.5714),
    ("Chennai", 13.0827, 80.2707), ("Mumbai", 19.0760, 72.8777),
    ("Kolkata", 22.5726, 88.3639), ("Bengaluru", 12.9716, 77.5946),
    ("Hyderabad", 17.3850, 78.4867), ("Pune", 18.5204, 73.8567),
    ("Leh", 34.1526, 77.5771), ("Srinagar", 34.0837, 74.7973),
    ("Kargil", 34.5584, 76.1334), ("Dras", 34.4306, 75.7499),
]
YEAR = int(CFG["climate"]["data_year"])
OUT = os.path.join(REPO, "src", "data", "shelter_presets.json")

WEEK_KEYS = {
    "hot": ("mean_indoor_c", "max_indoor_c", "comfort_fraction",
            "total_heat_loss_kwh", "solar_gain_kwh"),
    "cold": ("mean_indoor_c", "min_indoor_c", "comfort_fraction",
             "night_heat_loss_kwh", "total_heat_loss_kwh", "solar_gain_kwh"),
}


def _sim(preset, weather, mats, cfg_base):
    cfg = _apply_ground_temp(_apply_design(cfg_base, preset["design"]), weather)
    weeks = design_weeks(weather, YEAR)
    out = {}
    for wk_name, keys in WEEK_KEYS.items():
        res = simulate(cfg, weeks[f"{wk_name}_week"], mats)
        st = comfort_stats(res, CFG["climate"]["comfort_range_c"])
        out[f"{wk_name}_week"] = {
            k: round(float(st[k]), 3) for k in keys if k in st}
    return out


def main():
    mats = load_materials()
    sites_out = {}
    t0 = time.time()
    for name, lat, lon in SITES:
        weather, source, _ = get_weather_cached(lat, lon, YEAR,
                                                CFG["location"]["timezone"])
        cfg_base = CFG
        prof = _location_profile(weather)
        site = {"latitude": lat, "longitude": lon, "weather_source": source,
                "zone": prof.get("zone"), "zone_name": prof.get("zone_name")}
        for p in PRESETS:
            site[p["id"]] = _sim(p, weather, mats, cfg_base)
        sites_out[name] = site
        print(f"[presets] {name}: {len(PRESETS)} presets simulated "
              f"({time.time()-t0:.0f}s)", flush=True)

    payload = {
        "schema_version": 1,
        "generated_on": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
        "engine": "src/thermal/rc_model.simulate (design weeks on real "
                  "hourly weather)",
        "sites": sites_out,
        "presets": [
            {k: p[k] for k in ("id", "name", "tagline", "zones",
                               "design", "rationale")}
            for p in PRESETS
        ],
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    print(f"[presets] -> {OUT} "
          f"({os.path.getsize(OUT)/1024:.0f} KB, {len(PRESETS)} presets "
          f"x {len(SITES)} sites)")


if __name__ == "__main__":
    main()
