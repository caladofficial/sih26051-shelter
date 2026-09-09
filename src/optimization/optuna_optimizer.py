"""Optuna optimisation — search over geometry × materials × openings.

Search space (kept deliberately small, per the project plan §41):
    orientation            0–315° (15° steps)
    wall material          brick / concrete / stone / rammed_earth / timber /
                           puf_sandwich_panel / gi_sheet
    wall thickness         0.10–0.35 m
    roof material          rcc_slab / timber / gi_sheet / puf_sandwich_panel
    roof thickness         0.08–0.20 m
    insulation material    none / eps / xps / mineral_wool
    insulation thickness   0–100 mm
    window wall            south / east / west / north
    window size            0.5–2.5 m²
    window SHGC            0.3–0.85
    roof pitch             0–30° (0 = flat; >0 = mono-pitch, engine-modelled)
    ventilation            0.5–10 ACH

The last two were added after the surrogate-AI retrain showed both move the
thermal result materially (ACH shifts hot-week peak ~0.4 °C per doubling in
Chennai; a mono-pitch roof cuts the peak by up to 0.6 °C in Mumbai). Keeping
them out of the search space meant the optimiser could never *discover* them.
The categorical choices match src.ai_model.ACH_CHOICES / ROOF_PITCHES so the
optimizer and the surrogate explore the same space.
"""
from __future__ import annotations

import json
from pathlib import Path

import optuna
import pandas as pd

from src.optimization.objective import evaluate_design

WALL_MATERIALS = ["brick", "concrete", "stone", "rammed_earth",
                  "timber", "puf_sandwich_panel", "gi_sheet"]
ROOF_MATERIALS = ["rcc_slab", "timber", "gi_sheet", "puf_sandwich_panel"]
INSULATIONS = ["none", "eps", "xps", "mineral_wool"]
WALLS = ["south", "east", "west", "north"]
# kept in sync with src.ai_model.ACH_CHOICES / ROOF_PITCHES (duplicated on
# purpose: the optimizer must not import the surrogate module to run)
ACH_CHOICES = [0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0]
ROOF_PITCHES = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]


def suggest_design(trial: optuna.Trial) -> dict:
    """Map one Optuna trial to a shelter design dict (see module docstring)."""
    ins_mat = trial.suggest_categorical("insulation_material", INSULATIONS)
    ins_thick = (trial.suggest_float("insulation_thickness_m", 0.0, 0.10)
                 if ins_mat != "none" else 0.0)   # no insulation ⇒ no thickness
    design = {
        "orientation_deg": trial.suggest_int("orientation_deg", 0, 315, step=15),
        "wall_material": trial.suggest_categorical("wall_material", WALL_MATERIALS),
        "wall_thickness_m": trial.suggest_float("wall_thickness_m", 0.10, 0.35),
        "roof_material": trial.suggest_categorical("roof_material", ROOF_MATERIALS),
        "roof_thickness_m": trial.suggest_float("roof_thickness_m", 0.08, 0.20),
        "insulation_material": ins_mat,
        "insulation_thickness_m": ins_thick,
        "window_wall": trial.suggest_categorical("window_wall", WALLS),
        "window_width_m": trial.suggest_float("window_width_m", 0.7, 1.8),
        "window_height_m": trial.suggest_float("window_height_m", 0.7, 1.4),
        "window_shgc": trial.suggest_float("window_shgc", 0.30, 0.85),
        "roof_pitch_deg": trial.suggest_categorical("roof_pitch_deg",
                                                    ROOF_PITCHES),
        "ach": trial.suggest_categorical("ach", ACH_CHOICES),
    }
    return design


def run_study(cfg, weather, materials, n_trials: int = 60,
              seed: int = 42) -> optuna.Study:
    """Run the Optuna study (TPI objective) and return the study object."""
    from src.optimization.objective import objective_factory
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="minimize",
                                sampler=optuna.samplers.TPESampler(seed=seed))
    obj = objective_factory(cfg, weather, materials)

    def _objective(trial):
        design = suggest_design(trial)
        # convert window width/height + wall into the config schema
        design["window"] = {
            "wall": design.pop("window_wall"),
            "width_m": design.pop("window_width_m"),
            "height_m": design.pop("window_height_m"),
            "shgc": design.pop("window_shgc"),
        }
        return obj(design)

    study.optimize(_objective, n_trials=n_trials, show_progress_bar=False)
    return study


def study_to_records(study: optuna.Study) -> pd.DataFrame:
    """Flat table of all trials: params + TPI (= 1 - value) + comfort metrics."""
    records = []
    for t in study.trials:
        rec = {"trial": t.number, "tpi": 1.0 - t.value if t.value is not None else None}
        rec.update(t.params)
        records.append(rec)
    return pd.DataFrame(records).sort_values("tpi", ascending=False)


def save_study(study: optuna.Study, outdir: str | Path):
    """Persist study artifacts: best design JSON + all-trials CSV + summary."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    best = study.best_trial
    design = best.params.copy()
    design["window"] = {
        "wall": design.pop("window_wall"),
        "width_m": design.pop("window_width_m"),
        "height_m": design.pop("window_height_m"),
        "shgc": design.pop("window_shgc"),
    }
    with open(outdir / "best_design.json", "w") as fh:
        json.dump({"tpi": 1.0 - best.value, "params": best.params,
                   "design": design}, fh, indent=2)
    study_to_records(study).to_csv(outdir / "all_trials.csv", index=False)
    return outdir
