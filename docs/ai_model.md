# AI Design Assistant — model card & reproducibility

**Feature:** `SEC/08 · AI ASSIST` on the dashboard (optional — toggle off for
the classic manual workflow; the engine remains the source of truth).

## What it is

A physics-informed **surrogate model** of the validated RC thermal engine:

| | |
|---|---|
| Model family | 4 gradient-boosted regression trees (scikit-learn, BSD-3) |
| Runtime inference | pure NumPy tree walk (`src/ai_model.py`) — **no scikit-learn on Vercel** |
| Training data | `ml/generate_dataset.py` — the sourced RC engine itself, on real hourly weather (NASA POWER + Open-Meteo cross-check) |
| Sites | 14 Indian cities covering composite, hot-dry, warm-humid and cold zones — incl. Ladakh region: **Leh, Kargil, Dras** (among the coldest inhabited places in India) |
| Samples | 14 × 2000 = 28 000 design×site simulations (hot-week + cold-week runs) |
| Artifact | `src/data/ai_model.json` (~1–3 MB, committed) |
| Exports | `GET /api/ai/info` (model card + accuracy), `POST /api/ai/predict` (instant estimates), `POST /api/ai/suggest` (engine-verified suggestion) |

## Features (23, order matters)

- **Site (8)** — from real weather via `_location_profile`: hottest/coldest
  month mean temp, diurnal range, mean RH, CDD18, HDD18, mean GHI, mean wind.
- **Design (15)** — physics-informed, computed from SOURCED material
  properties with the same helpers the engine uses (`surface_conductance`,
  `surface_mass`): length/width/height, wall R-value, wall mass/m², wall
  solar absorptance, roof R-value, roof mass/m², roof absorptance,
  insulation R-value, window area, window U, window SHGC, orientation sin/cos.

## Targets (all from the engine, nothing invented)

`hot_mean_c`, `hot_max_c`, `hot_comfort_fraction` (hot week), `cold_min_c`
(cold week) — same `design_weeks` + `simulate` + `comfort_stats` code path
`/api/simulate` uses.

## Training & validation

```bash
python3 ml/generate_dataset.py --designs-per-site 2000   # ~45 min, resumable
python3 ml/train_model.py                                # exports ai_model.json
```

Honest validation (exported in the model card):

1. **Random 80/20 holdout** — MAE / R² per target.
2. **Leave-one-site-out** — train on 13 cities, test on the 14th, per-site
   MAE reported, so accuracy reflects unseen locations, not memorisation.
   Cold-region sites (Leh/Kargil/Dras/Srinagar) are explicitly held out so
   the model's honest behaviour on unseen high-altitude cold is disclosed.

## Cold-climate coverage (Ladakh upgrade)

- **Design space** extended for cold climates: stone / mud-brick / timber /
  AAC walls; stone, mud-brick, timber, AAC roofs; EPS up to 200 mm, XPS and
  sheep-wool insulation up to 150 mm; window U-values down to 1.2 W/m²K
  (triple low-e), 1.6 (low-e double), 2.0 (clear double) — ASHRAE HOF 2021
  Ch.15 typical assembled glazing values.
- **Ground temperature is site-adapted**: the flat 26 °C default is replaced
  by mean-annual-air-temperature + 2 K (`_apply_ground_temp`, ASHRAE
  undisturbed-ground approximation) computed from each site's real weather —
  e.g. Leh floor coupling now runs at ≈ −0.7 °C, not 26 °C.
- **Materials added** (sourced): mud_brick (adobe), aerated_concrete (AAC),
  sheep_wool insulation — all with source column in `data/external/materials.csv`.

The UI shows the MAE next to every estimate; `/api/ai/suggest` re-runs the
top candidates on the real engine and returns engine truth, never AI guesses.

## Honesty rules (project constraints)

- Every dataset number is engine output on real weather — no averaged or
  fabricated values.
- The AI never overrides the engine: predictions are labelled estimates,
  `VERIFY WITH ENGINE` re-runs the real simulation, manual mode is intact.
- "Perfectly trained" is not a thing — accuracy is disclosed, not claimed.
