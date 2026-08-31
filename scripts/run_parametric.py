"""PHASE 5 — parametric simulation: orientation x material x insulation sweeps.

Usage (from project root):
    python scripts/run_parametric.py

Runs the fast RC model over:
    1. orientation sweep   (0..315 deg, 45 deg steps)
    2. wall-material sweep (brick, concrete, stone, rammed_earth, timber,
                            puf_sandwich_panel, gi_sheet)
    3. insulation sweep    (0 / 25 / 50 / 100 mm EPS on walls+roof)

All numbers come from the simulation — never from thin air.

Outputs under results/parametric/:
    orientation_sweep.csv / .html / .png
    material_sweep.csv    / .html / .png
    insulation_sweep.csv  / .html / .png
    parametric_summary.json
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.climate import design_weeks, load_config, load_clean  # noqa: E402
from src.paths import RESULTS_DIR  # noqa: E402
from src.thermal.rc_model import comfort_stats, load_materials, simulate  # noqa: E402
from src.visualization.plots import bar_compare  # noqa: E402


def _run_case(cfg, weather, materials, **overrides):
    """Run the RC model over a design week with shelter overrides; return stats."""
    import copy
    cfg = copy.deepcopy(cfg)
    for k, v in overrides.items():
        if k in ("window", "door", "insulation"):
            cfg["shelter"][k].update(v)
        else:
            cfg["shelter"][k] = v
    res = simulate(cfg, weather, materials)
    stats = comfort_stats(res, cfg["climate"]["comfort_range_c"])
    stats["solar_gain_kwh"] = stats["solar_gain_kwh"]
    return stats


def main() -> int:
    cfg = load_config()
    materials = load_materials()
    weather = load_clean()
    year = int(cfg["climate"]["data_year"])
    hot_week = design_weeks(weather, year)["hot_week"]

    outdir = RESULTS_DIR / "parametric"
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"[param] design week: hottest week of {year} "
          f"({hot_week.index[0]:%d %b} - {hot_week.index[-1]:%d %b})")

    # ---------- 1) orientation sweep ---------------------------------------
    rows = []
    for deg in range(0, 360, 45):
        s = _run_case(cfg, hot_week, materials, orientation_deg=deg)
        rows.append({"orientation_deg": deg, **s})
    orient = pd.DataFrame(rows)
    orient.to_csv(outdir / "orientation_sweep.csv", index=False)
    bar_compare(orient, "orientation_deg", "max_indoor_c",
                "Peak indoor temperature by orientation (hottest week)",
                outdir / "orientation_sweep.html", outdir / "orientation_sweep.png",
                ylabel="Max indoor temp (degC)")
    bar_compare(orient, "orientation_deg", "comfort_fraction",
                "Comfort hours fraction by orientation (hottest week)",
                outdir / "orientation_comfort.html", outdir / "orientation_comfort.png")
    print("[param] orientation sweep done")

    # ---------- 2) wall-material sweep --------------------------------------
    wall_materials = ["brick", "concrete", "stone", "rammed_earth",
                      "timber", "puf_sandwich_panel", "gi_sheet"]
    rows = []
    for mat in wall_materials:
        s = _run_case(cfg, hot_week, materials, wall_material=mat)
        rows.append({"wall_material": mat, **s})
    mat = pd.DataFrame(rows)
    mat.to_csv(outdir / "material_sweep.csv", index=False)
    bar_compare(mat, "wall_material", "mean_indoor_c",
                "Mean indoor temperature by wall material (hottest week)",
                outdir / "material_sweep.html", outdir / "material_sweep.png",
                ylabel="Mean indoor temp (degC)")
    bar_compare(mat, "wall_material", "total_heat_loss_kwh",
                "Total heat loss by wall material (hottest week)",
                outdir / "material_loss.html", outdir / "material_loss.png",
                ylabel="Heat loss (kWh)")
    print("[param] material sweep done")

    # ---------- 3) insulation sweep -----------------------------------------
    rows = []
    for mm in (0, 25, 50, 100):
        s = _run_case(cfg, hot_week, materials, insulation={
            "material": "eps", "thickness_m": mm / 1000.0})
        rows.append({"insulation_mm": mm, **s})
    ins = pd.DataFrame(rows)
    ins.to_csv(outdir / "insulation_sweep.csv", index=False)
    bar_compare(ins, "insulation_mm", "max_indoor_c",
                "Peak indoor temperature vs EPS insulation thickness",
                outdir / "insulation_sweep.html", outdir / "insulation_sweep.png",
                ylabel="Max indoor temp (degC)")
    bar_compare(ins, "insulation_mm", "total_heat_loss_kwh",
                "Heat loss vs EPS insulation thickness",
                outdir / "insulation_loss.html", outdir / "insulation_loss.png",
                ylabel="Heat loss (kWh)")
    print("[param] insulation sweep done")

    summary = {
        "location": cfg["location"]["name"], "year": year,
        "hot_week": [str(hot_week.index[0]), str(hot_week.index[-1])],
        "orientation_sweep": orient.to_dict(orient="records"),
        "material_sweep": mat.to_dict(orient="records"),
        "insulation_sweep": ins.to_dict(orient="records"),
    }
    with open(outdir / "parametric_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2, default=str)
    print(f"\n[param] all sweeps saved under {outdir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
