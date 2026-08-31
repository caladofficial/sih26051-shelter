"""Generate parity fixtures: Python engine outputs + embedded inputs for a set
of (site, design) cases, so the JS port can be checked against them."""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api_app import (CFG, get_weather_cached, _location_profile,
                         _apply_ground_temp, _apply_design,
                         _apply_site_location)
from src.data.climate import design_weeks
from src.data import solar
from src.thermal.rc_model import simulate, comfort_stats, load_materials
from src.ai_model import build_features, predict_design

SITES = [
    ("Prayagraj", 25.4358, 81.8463), ("Leh", 34.1526, 77.5771),
    ("Jaisalmer", 26.9157, 70.9083), ("Chennai", 13.0827, 80.2707),
    ("Dras", 34.4306, 75.7499),
]
DESIGNS = [
    {"wall_material": "brick", "wall_thickness_m": 0.23,
     "roof_material": "rcc_slab", "roof_thickness_m": 0.15,
     "insulation_material": "eps", "insulation_thickness_m": 0.05,
     "window_wall": "south", "window_width_m": 1.2, "window_height_m": 1.2,
     "window_shgc": 0.55, "window_u_w_m2k": 2.6, "orientation_deg": 0},
    {"wall_material": "rammed_earth", "wall_thickness_m": 0.45,
     "roof_material": "puf_sandwich_panel", "roof_thickness_m": 0.15,
     "insulation_material": "sheep_wool", "insulation_thickness_m": 0.15,
     "window_wall": "south", "window_width_m": 1.2, "window_height_m": 1.2,
     "window_shgc": 0.85, "window_u_w_m2k": 1.6, "orientation_deg": 45},
]
mats = load_materials()
cases = []
for name, lat, lon in SITES:
    weather, _, _ = get_weather_cached(lat, lon, 2024, "Asia/Kolkata")
    prof = _location_profile(weather)
    ground = _apply_ground_temp(CFG, weather)["simulation"]["ground_temperature_c"]
    weeks = design_weeks(weather, 2024)
    prof["hot_week"] = None  # replaced below
    prof["cold_week"] = None
    for wk_name in ("hot_week", "cold_week"):
        wk = weeks[wk_name]
        zen = solar.solar_position(wk.index, lat, lon)
        zenith = zen["apparent_zenith"].clip(upper=89.9)
        azimuth = zen["azimuth"]
        ghi = wk["ghi"].clip(lower=0.0)
        erbs = solar.erbs(ghi, zenith, wk.index)
        prof[wk_name] = {
            "t2m": [round(float(x), 3) for x in wk["t2m"].interpolate().ffill().bfill()],
            "ghi": [round(float(x), 2) for x in ghi],
            "dni": [round(float(x), 2) for x in erbs["dni"].clip(lower=0).fillna(0)],
            "dhi": [round(float(x), 2) for x in erbs["dhi"].clip(lower=0).fillna(0)],
            "zenith_deg": [round(float(x), 3) for x in zenith],
            "azimuth_deg": [round(float(x), 3) for x in azimuth],
            "hour_of_day": [int(h) for h in wk.index.hour],
            "start_minute": int(wk.index[0].minute),
            "ground_temperature_c": ground,
        }
    prof.pop("wind_rose", None); prof.pop("diurnal", None); prof.pop("guidance", None)
    for i, d in enumerate(DESIGNS):
        cfg = _apply_ground_temp(_apply_site_location(
            _apply_design(CFG, d), lat, lon, "Asia/Kolkata"), weather)
        out = {"site": name, "design": d}
        for wk_name in ("hot_week", "cold_week"):
            res = simulate(cfg, weeks[wk_name], mats)
            out[wk_name + "_stats"] = comfort_stats(res, [18, 32])
        ai = predict_design(d, _location_profile(weather), mats)["estimates"]
        out["ai_estimates"] = ai
        # feature vector for JS parity
        X = build_features(d, _location_profile(weather), mats)
        out["features"] = [round(float(x), 6) for x in X]
        cases.append(out)
    cases.append({"site": name, "profile_features": {
        k: prof[k] for k in ("t_hottest_month_c", "t_coldest_month_c",
                             "diurnal_range_c", "rh_mean_pct", "cdd18",
                             "hdd18", "ghi_mean_w_m2", "wind_mean_ms")},
        "weeks": {"hot_week": prof["hot_week"], "cold_week": prof["cold_week"]},
        "ground_temperature_c": ground,
        "ai_estimates": predict_design(DESIGNS[0], _location_profile(weather), mats)["estimates"],
        "features": [round(float(x), 6) for x in build_features(DESIGNS[0], _location_profile(weather), mats)]})

with open("/tmp/parity_fixtures.json", "w") as fh:
    json.dump(cases, fh)
print(f"fixtures: {len(cases)} case-entries written")
