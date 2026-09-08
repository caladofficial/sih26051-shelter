"""Generate the AI training dataset from the sourced RC engine.

Every row = one flat-schema design simulated by the validated engine on REAL
hourly weather (NASA POWER + Open-Meteo cross-check) of an Indian city.
15 cities cover all NBC-style climate zones. Deterministic (seeded) so the
dataset is reproducible; per-site CSVs are written independently so the run
can be resumed. The engine code path is the same one /api/simulate uses
(design_weeks + simulate + comfort_stats) — no averages, no invented data.

Run:  python3 ml/generate_dataset.py [--designs-per-site 2000]
Outputs: ml/data/raw_{site}.csv  +  ml/data/site_profiles.json
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from src.ai_model import sample_design  # noqa: E402
from src.api_app import (_apply_design, _apply_ground_temp,  # noqa: E402
                         _apply_site_location, CFG, get_weather_cached,
                         _location_profile)
from src.data.climate import design_weeks  # noqa: E402
from src.thermal.rc_model import (comfort_stats, load_materials,  # noqa: E402
                                  simulate)

# (name, lat, lon) — one city per climate zone family
SITES = [
    ("Prayagraj", 25.4358, 81.8463),   # composite
    ("Delhi", 28.6139, 77.2090),       # composite
    ("Jaipur", 26.9124, 75.7873),      # composite/hot-dry (dataset package)
    ("Jaisalmer", 26.9157, 70.9083),   # hot-dry
    ("Ahmedabad", 23.0225, 72.5714),   # hot-dry
    ("Chennai", 13.0827, 80.2707),     # warm-humid
    ("Mumbai", 19.0760, 72.8777),      # warm-humid
    ("Kolkata", 22.5726, 88.3639),     # warm-humid
    ("Bengaluru", 12.9716, 77.5946),   # warm-humid / temperate
    ("Hyderabad", 17.3850, 78.4867),   # composite / hot-dry
    ("Pune", 18.5204, 73.8567),        # composite / temperate
    ("Leh", 34.1526, 77.5771),         # cold (high-altitude Ladakh)
    ("Srinagar", 34.0837, 74.7973),    # cold (Kashmir valley)
    ("Kargil", 34.5584, 76.1334),      # cold (Ladakh, ~2,676 m)
    ("Dras", 34.4306, 75.7499),        # cold (Ladakh, ~3,280 m — among the
                                       # coldest inhabited places in India)
]
# Train on the SAME window the deployed engine serves. v1 learned from the
# 2024 NASA POWER snapshot while the API now answers from the rolling
# Open-Meteo archive, so the surrogate and the engine disagreed about the
# climate before either of them saw a design.
from src.api_app import _period_year            # noqa: E402
PERIOD = "latest"
YEAR = _period_year(PERIOD)
BASE_SEED = 26051
OUT_DIR = os.path.join(REPO, "ml", "data")
ROW_COLS = [
    "site", "latitude", "longitude", "zone",
] + [
    "length_m", "width_m", "height_m", "orientation_deg",
    "wall_material", "wall_thickness_m", "roof_material", "roof_thickness_m",
    "insulation_material", "insulation_thickness_m",
    "window_wall", "window_width_m", "window_height_m",
    "window_shgc", "window_u_w_m2k",
] + [
    "hot_mean_c", "hot_max_c", "hot_comfort_fraction",
    "cold_min_c", "cold_mean_c", "cold_comfort_fraction",
]


def _sim_metrics(weather, weeks, design, mats, cfg_base=None):
    cfg = _apply_design(cfg_base if cfg_base is not None else CFG, design)
    hot = comfort_stats(simulate(cfg, weeks["hot_week"], mats),
                        CFG["climate"]["comfort_range_c"])
    cold = comfort_stats(simulate(cfg, weeks["cold_week"], mats),
                         CFG["climate"]["comfort_range_c"])
    return (round(float(hot["mean_indoor_c"]), 3),
            round(float(hot["max_indoor_c"]), 3),
            round(float(hot["comfort_fraction"]), 4),
            round(float(cold["min_indoor_c"]), 3),
            round(float(cold["mean_indoor_c"]), 3),
            round(float(cold["comfort_fraction"]), 4))


def _site_seed(name: str) -> int:
    import zlib
    return BASE_SEED + zlib.crc32(name.encode("utf-8")) % 10_000


def generate_site(site, n_designs, profile, mats):
    name, lat, lon = site
    weather, source, _ = get_weather_cached(lat, lon, YEAR,
                                            CFG["location"]["timezone"],
                                            period=PERIOD)
    rng = np.random.default_rng(_site_seed(name))
    weeks = design_weeks(weather, YEAR)
    # site-adapted physics: ground temperature (MAAT + 2 K) AND solar
    # geometry from the SITE's own coordinates (the engine reads
    # cfg['location'] for the SPA sun position)
    cfg_site = _apply_ground_temp(_apply_site_location(
        CFG, lat, lon, CFG["location"]["timezone"]), weather)
    rows = []
    t0 = time.time()
    for i in range(n_designs):
        d = sample_design(rng)
        try:
            hm, hx, hcf, cm, cmean, ccf = _sim_metrics(weather, weeks, d,
                                                       mats, cfg_site)
        except Exception as exc:          # skip pathological samples
            print(f"[gen] {name} sample {i} skipped: {exc}")
            continue
        rows.append([
            name, lat, lon, profile["zone"],
            d["length_m"], d["width_m"], d["height_m"], d["orientation_deg"],
            d["wall_material"], d["wall_thickness_m"],
            d["roof_material"], d["roof_thickness_m"],
            d["insulation_material"], d["insulation_thickness_m"],
            d["window_wall"], d["window_width_m"], d["window_height_m"],
            d["window_shgc"], d["window_u_w_m2k"],
            hm, hx, hcf, cm, cmean, ccf,
        ])
        if (i + 1) % 200 == 0:
            el = time.time() - t0
            print(f"[gen] {name}: {i + 1}/{n_designs} "
                  f"({el / (i + 1):.2f}s/sample, src={source})", flush=True)
    df = pd.DataFrame(rows, columns=ROW_COLS)
    df.to_csv(os.path.join(OUT_DIR, f"raw_{name}.csv"), index=False)
    print(f"[gen] {name} DONE: {len(df)} rows -> raw_{name}.csv", flush=True)
    return len(df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--designs-per-site", type=int, default=2000)
    ap.add_argument("--sites", default="all",
                    help="comma-separated site names or 'all'")
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    mats = load_materials()
    names = [s[0] for s in SITES] if args.sites == "all" \
        else [x.strip() for x in args.sites.split(",")]
    todo = [s for s in SITES if s[0] in names]

    # site climate profiles (real weather) — saved once for the trainer
    profiles = {}
    for name, lat, lon in todo:
        weather, _, _ = get_weather_cached(lat, lon, YEAR,
                                           CFG["location"]["timezone"])
        profiles[name] = _location_profile(weather)
        profiles[name]["latitude"] = lat
        profiles[name]["longitude"] = lon
    with open(os.path.join(OUT_DIR, "site_profiles.json"), "w") as fh:
        json.dump(profiles, fh, indent=1, default=str)
    print(f"[gen] profiles for {len(profiles)} sites saved", flush=True)

    total = 0
    if args.workers > 1 and len(todo) > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(generate_site, s, args.designs_per_site,
                              profiles[s[0]], mats): s[0] for s in todo}
            for f in futs:
                total += f.result()
    else:
        for s in todo:
            total += generate_site(s, args.designs_per_site,
                                   profiles[s[0]], mats)
    print(f"[gen] ALL DONE: {total} samples", flush=True)


if __name__ == "__main__":
    main()
