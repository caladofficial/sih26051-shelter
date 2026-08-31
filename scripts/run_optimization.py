"""PHASE 6 — Optuna optimisation over shelter designs.

Usage (from project root):
    python scripts/run_optimization.py [--trials 60]

Searches orientation × wall/roof materials × insulation × window placement
to maximise the Thermal Performance Index (see src/optimization/objective.py).
Outputs under results/optimization/: best_design.json, all_trials.csv,
optimization_summary.json.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.climate import load_config, load_clean  # noqa: E402
from src.optimization.optuna_optimizer import run_study, save_study  # noqa: E402
from src.paths import RESULTS_DIR  # noqa: E402
from src.thermal.rc_model import load_materials  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=60)
    args = parser.parse_args()

    cfg = load_config()
    weather = load_clean()
    materials = load_materials()

    print(f"[opt] running Optuna study with {args.trials} trials on design weeks "
          f"of {cfg['location']['name']} {cfg['climate']['data_year']} ...")
    study = run_study(cfg, weather, materials, n_trials=args.trials)
    best = study.best_trial
    outdir = RESULTS_DIR / "optimization"
    save_study(study, outdir)

    print(f"\n[opt] best trial #{best.number}:  TPI = {1.0 - best.value:.3f}")
    for k, v in best.params.items():
        print(f"      {k}: {v}")
    summary = {
        "n_trials": len(study.trials), "best_trial": best.number,
        "best_tpi": 1.0 - best.value, "best_params": best.params,
    }
    with open(outdir / "optimization_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2)
    print(f"\n[opt] results saved under {outdir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
