"""Optimizer search space — ventilation + roof pitch are explorable.

The surrogate-AI retrain proved both ACH and roof pitch materially change the
thermal result, but the Optuna study could never *discover* them while they
were missing from suggest_design(). These tests pin the space and the config
mapping so a future refactor can't silently drop them again.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import optuna  # noqa: E402

from src.optimization import objective as obj  # noqa: E402
from src.optimization.optuna_optimizer import (  # noqa: E402
    ACH_CHOICES, ROOF_PITCHES, suggest_design)


def _fixed_params():
    return {
        "insulation_material": "eps",
        "insulation_thickness_m": 0.05,
        "orientation_deg": 0,
        "wall_material": "brick",
        "wall_thickness_m": 0.2,
        "roof_material": "rcc_slab",
        "roof_thickness_m": 0.12,
        "window_wall": "south",
        "window_width_m": 1.2,
        "window_height_m": 1.2,
        "window_shgc": 0.6,
        "roof_pitch_deg": 20.0,
        "ach": 6.0,
    }


def test_suggest_design_includes_pitch_and_ach():
    trial = optuna.trial.FixedTrial(_fixed_params())
    d = suggest_design(trial)
    assert d["roof_pitch_deg"] == 20.0
    assert d["ach"] == 6.0


def test_space_choices_match_surrogate_space():
    """Optimizer and src.ai_model must explore the same choices."""
    from src.ai_model import ACH_CHOICES as AI_ACH, ROOF_PITCHES as AI_PITCH
    assert set(ACH_CHOICES) == set(AI_ACH)
    assert set(ROOF_PITCHES) <= set(AI_PITCH)   # AI samples 0 twice (bias)


def test_objective_maps_ach_and_pitch(monkeypatch):
    """ach -> cfg.simulation.ventilation_ach; pitch -> cfg.shelter."""
    captured = {}

    def fake_weeks(weather, year):
        return {"hot_week": None, "cold_week": None}

    def fake_simulate(cfg, weather, materials):
        captured["cfg"] = cfg
        return None

    def fake_stats(res, band):
        return {"comfort_fraction": 0.5, "max_indoor_c": 30.0,
                "night_heat_loss_kwh": 0.0, "mean_indoor_c": 25.0,
                "min_indoor_c": 20.0, "solar_gain_kwh": 1.0}

    monkeypatch.setattr(obj, "design_weeks", fake_weeks)
    monkeypatch.setattr(obj, "simulate", fake_simulate)
    monkeypatch.setattr(obj, "comfort_stats", fake_stats)

    cfg = {"climate": {"data_year": 2024, "comfort_range_c": [18, 32]},
           "shelter": {}, "simulation": {"ventilation_ach": 2.0}}
    out = obj.evaluate_design(cfg, weather=None, materials=None,
                              design={"ach": 8.0, "roof_pitch_deg": 25.0})
    assert captured["cfg"]["simulation"]["ventilation_ach"] == 8.0
    assert captured["cfg"]["shelter"]["roof_pitch_deg"] == 25.0
    assert "thermal_performance_index" in out
