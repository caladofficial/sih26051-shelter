"""PHASE 2/3 — first simulation: shelter + weather -> indoor temperature.

Usage (from project root):
    python scripts/run_first_simulation.py [--energyplus]

Runs the fast RC model for the full year and for the hottest/coldest design
weeks, produces the milestone plots, and (if EnergyPlus is available or
--energyplus is passed) confirms the design with the EnergyPlus engine.

Outputs under results/:
    first_simulation_*.csv        hourly indoor/outdoor + heat balance
    first_simulation_*.html/png   plots (interactive + static)
    first_simulation_summary.json
    energyplus/...                EnergyPlus model + results (if run)
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.climate import design_weeks, load_config, load_clean  # noqa: E402
from src.paths import RESULTS_DIR, EXTERNAL_DIR, PROCESSED_DIR  # noqa: E402
from src.thermal.rc_model import (comfort_stats, load_materials,  # noqa: E402
                                  simulate, surface_conductance)
from src.visualization.plots import time_series  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--energyplus", action="store_true",
                        help="also run the EnergyPlus confirmation simulation")
    args = parser.parse_args()

    cfg = load_config()
    materials = load_materials()
    weather = load_clean()
    year = int(cfg["climate"]["data_year"])

    print(f"[sim] Shelter: {cfg['shelter']['wall_material']} walls, "
          f"{cfg['shelter']['roof_material']} roof, orientation "
          f"{cfg['shelter']['orientation_deg']} deg")
    print(f"[sim] Weather: {cfg['location']['name']} {year}, "
          f"{len(weather)} hours")

    weeks = design_weeks(weather, year)
    periods = {"full_year": weather,
               "hot_week": weeks["hot_week"],
               "cold_week": weeks["cold_week"]}

    summary = {}
    for name, w in periods.items():
        print(f"[sim] running RC model: {name} ...")
        res = simulate(cfg, w, materials)
        stats = comfort_stats(res, cfg["climate"]["comfort_range_c"])
        summary[name] = stats
        res.to_csv(RESULTS_DIR / f"first_simulation_{name}.csv")
        if name == "full_year":
            # monthly mean profile (presentation-friendly)
            monthly = res.resample("ME").mean()
            time_series(
                monthly, ["indoor_t_c", "outdoor_t_c"],
                f"Monthly mean temperature — {cfg['location']['name']} {year}",
                "Temperature (degC)",
                RESULTS_DIR / "first_simulation_monthly.html",
                RESULTS_DIR / "first_simulation_monthly.png",
                labels={"indoor_t_c": "Indoor (RC model)",
                        "outdoor_t_c": "Outdoor"})
        else:
            tag = "hottest" if "hot" in name else "coldest"
            time_series(
                res, ["indoor_t_c", "outdoor_t_c"],
                f"{tag.capitalize()} design week — indoor vs outdoor "
                f"({cfg['location']['name']} {year})",
                "Temperature (degC)",
                RESULTS_DIR / f"first_simulation_{name}.html",
                RESULTS_DIR / f"first_simulation_{name}.png",
                labels={"indoor_t_c": "Indoor (RC model)",
                        "outdoor_t_c": "Outdoor"})
        print(f"    mean indoor {stats['mean_indoor_c']:.1f} C | "
              f"min {stats['min_indoor_c']:.1f} | max {stats['max_indoor_c']:.1f} | "
              f"comfort {stats['comfort_fraction'] * 100:.0f}%")

    # ---- EnergyPlus confirmation ------------------------------------------
    eplus_summary = None
    if args.energyplus:
        eplus_summary = _run_energyplus(cfg, materials, weeks)

    summary["location"] = cfg["location"]
    summary["shelter"] = cfg["shelter"]
    summary["energyplus"] = eplus_summary
    with open(RESULTS_DIR / "first_simulation_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2, default=str)

    print("\nResults saved under results/. Next: python scripts/run_parametric.py")
    return 0


def _run_energyplus(cfg, materials, weeks):
    """Build IDF, fetch EPW (POWER), run EnergyPlus, plot the hot week."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "simulation" / "energyplus"))
    from make_idf import build_idf
    from run_energyplus import discover_energyplus, run_energyplus, select
    from src.data.nasa_power import fetch_epw

    exe = discover_energyplus(cfg.get("energyplus", {}).get("executable", ""))
    if exe is None:
        print("[eplus] SKIPPED — energyplus executable not found. "
              "Set ENERGYPLUS_DIR or install EnergyPlus (docs/windows_setup.md).")
        return None

    loc = cfg["location"]
    year = int(cfg["climate"]["data_year"])
    epw = EXTERNAL_DIR / f"{loc['name'].lower()}_{year}.epw"
    if not epw.exists():
        try:
            fetch_epw(loc["latitude"], loc["longitude"], year, out_path=epw)
        except Exception as exc:
            print(f"[eplus] SKIPPED — EPW unavailable ({exc})")
            return None

    idf = Path("simulation/energyplus/shelter.idf")
    build_idf(cfg, materials, idf)
    out = run_energyplus(idf, epw, RESULTS_DIR / "energyplus", exe=exe, year=year)
    df = out["results"]
    print(f"[eplus] parsed {len(df)} rows")

    res = pd.DataFrame({
        "indoor_t_c": select(df, "Zone Mean Air Temperature"),
        "outdoor_t_c": select(df, "Site Outdoor Air Drybulb Temperature"),
        "q_solar_w": select(df, "Incident Solar Radiation"),
    })
    res.index = df.index.tz_localize(cfg["location"]["timezone"])
    res.to_csv(RESULTS_DIR / "energyplus_hourly.csv")

    # plot the hottest design week (same period as the RC model hot week).
    # E+ timestamps use its "no year" anchor (e.g. 2017) — map dates over.
    hot = weeks["hot_week"]
    year0 = res.index[0].year
    start = hot.index[0].replace(year=year0)
    end = hot.index[-1].replace(year=year0)
    hot_res = res.loc[start: end]
    time_series(
        hot_res, ["indoor_t_c", "outdoor_t_c"],
        "EnergyPlus — hottest design week, indoor vs outdoor",
        "Temperature (degC)",
        RESULTS_DIR / "energyplus_hot_week.html",
        RESULTS_DIR / "energyplus_hot_week.png",
        labels={"indoor_t_c": "Indoor (EnergyPlus)", "outdoor_t_c": "Outdoor"})
    print("[eplus] plots saved under results/energyplus_hot_week.*")
    return {"executable": exe, "rows": int(len(df)),
            "mean_indoor_c": float(res["indoor_t_c"].mean())}


if __name__ == "__main__":
    sys.exit(main())
