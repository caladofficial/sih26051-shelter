"""PHASE 1 — download climate data (NASA POWER primary + Open-Meteo cross-check).

Usage (from project root):
    python scripts/fetch_climate.py

Outputs:
    data/raw/climate_power_hourly.csv        raw POWER hourly (never edited)
    data/raw/climate_openmeteo_hourly.csv    raw Open-Meteo hourly
    data/processed/climate_clean.csv         clean dataset (POWER, local tz)
    data/processed/validation_report.json    cross-check stats
    data/external/<location>_<year>.epw      EPW weather file for EnergyPlus
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.climate import build_climate_dataset, load_config  # noqa: E402
from src.data.nasa_power import fetch_epw  # noqa: E402
from src.paths import EXTERNAL_DIR  # noqa: E402


def main() -> int:
    cfg = load_config()
    out = build_climate_dataset(cfg)

    print("\n========== VALIDATION REPORT (Open-Meteo vs NASA POWER) ==========")
    print(f"{'variable':<8}{'power_mean':>12}{'om_mean':>12}{'bias':>10}{'rmse':>10}{'corr':>8}")
    for var, stats in out["report"].items():
        print(f"{var:<8}{stats['power_mean']:>12.2f}{stats['openmeteo_mean']:>12.2f}"
              f"{stats['bias_om_minus_power']:>10.2f}{stats['rmse']:>10.2f}"
              f"{stats['correlation']:>8.3f}")

    # EPW for EnergyPlus (POWER Sustainable Buildings community)
    loc = cfg["location"]
    year = int(cfg["climate"]["data_year"])
    epw_path = EXTERNAL_DIR / f"{loc['name'].lower()}_{year}.epw"
    try:
        fetch_epw(loc["latitude"], loc["longitude"], year, out_path=epw_path)
        head = epw_path.read_text(encoding="utf-8").splitlines()[0]
        print(f"\n[climate] EPW saved: {epw_path}\n          {head}")
    except Exception as exc:  # POWER EPW can be picky — non-fatal
        print(f"\n[climate] WARNING: EPW fetch failed ({exc}) — "
              "EnergyPlus run will use Open-Meteo-converted weather instead.")

    with open(EXTERNAL_DIR / f"{loc['name'].lower()}_{year}_meta.json", "w") as fh:
        json.dump({"name": loc["name"], "latitude": loc["latitude"],
                   "longitude": loc["longitude"], "elevation_m": loc["elevation_m"],
                   "year": year, "timezone": loc["timezone"]}, fh, indent=2)
    print("\nPHASE 1 complete. Next: python scripts/run_first_simulation.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
