"""Optimisation objective — Thermal Performance Index (TPI).

The SIH26051 statement's objective is: minimise energy utilisation for thermal
comfort maintenance, i.e. design a PASSIVE shelter that stays comfortable on its
own. We therefore score designs on the two stress weeks of the year:

    hot week   (hottest 7 days)  → overheating is the risk
    cold week  (coldest 7 days)  → night-time heat loss is the risk

For each week we run the fast RC model and compute comfort hours inside the
band [T_lo, T_hi] (default 18–32 °C).

    S_week   = fraction of hours in comfort band
    P_hot    = max(0, T_peak - 42) / 10      → penalty for severe overheating
    P_cold   = |night heat loss kWh| / 150   → penalty for night heat loss
                                              (150 kWh ≈ uninsulated brick shelter)

    TPI = 0.5·S_hot + 0.5·S_cold - 0.25·P_hot - 0.15·P_cold

TPI ∈ [-1, 1]; higher is better. Optuna MINIMISES, so the objective passed to
Optuna is 1 - TPI.

Justification: equal weight on both stress weeks matches "area-specific" design
for both summer and winter regions (Ladakh winter / plains summer); the hot
penalty caps runaway solar gains; the cold penalty captures the statement's
"high thermal losses through the material of the shelter and openings".
The exact weighting is a tunable policy constant (see config), not a physical law.
"""
from __future__ import annotations

import copy

from src.data.climate import design_weeks, load_config, load_clean
from src.thermal.rc_model import comfort_stats, simulate


def evaluate_design(cfg: dict, weather, materials, design: dict) -> dict:
    """Run the RC model on hot + cold design weeks for `design` overrides.

    design keys (all optional):
        orientation_deg, wall_material, wall_thickness_m, roof_material,
        roof_thickness_m, roof_pitch_deg, insulation_material,
        insulation_thickness_m, window_wall, window_width_m, window_height_m,
        window_shgc, door_wall, ach
    `ach` maps to cfg["simulation"]["ventilation_ach"] (same convention as
    the API's design override path); everything else is a shelter key.
    Returns a dict of metrics incl. thermal_performance_index (TPI).
    """
    cfg = copy.deepcopy(cfg)
    for k, v in design.items():
        if k in ("window", "door", "insulation"):
            cfg["shelter"][k].update(v)
        elif k == "ach":
            cfg.setdefault("simulation", {})["ventilation_ach"] = v
        else:
            cfg["shelter"][k] = v

    weeks = design_weeks(weather, int(cfg["climate"]["data_year"]))
    hot = simulate(cfg, weeks["hot_week"], materials)
    cold = simulate(cfg, weeks["cold_week"], materials)

    s_hot = comfort_stats(hot, cfg["climate"]["comfort_range_c"])
    s_cold = comfort_stats(cold, cfg["climate"]["comfort_range_c"])

    p_hot = max(0.0, s_hot["max_indoor_c"] - 42.0) / 10.0
    p_cold = abs(s_cold["night_heat_loss_kwh"]) / 150.0
    tpi = (0.5 * s_hot["comfort_fraction"] + 0.5 * s_cold["comfort_fraction"]
           - 0.25 * p_hot - 0.15 * p_cold)

    return {
        "thermal_performance_index": float(tpi),
        "hot_comfort_fraction": s_hot["comfort_fraction"],
        "cold_comfort_fraction": s_cold["comfort_fraction"],
        "hot_mean_indoor_c": s_hot["mean_indoor_c"],
        "hot_max_indoor_c": s_hot["max_indoor_c"],
        "cold_min_indoor_c": s_cold["min_indoor_c"],
        "cold_night_heat_loss_kwh": s_cold["night_heat_loss_kwh"],
        "solar_gain_hot_kwh": s_hot["solar_gain_kwh"],
    }


def objective_factory(cfg, weather, materials):
    """Returns a callable objective(design) -> (1 - TPI) for Optuna."""
    def objective(design: dict) -> float:
        metrics = evaluate_design(cfg, weather, materials, design)
        return 1.0 - metrics["thermal_performance_index"]
    return objective
