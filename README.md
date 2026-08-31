# SIH26051_Shelter — Area-Specific Shelter Thermal-Comfort Model

Smart India Hackathon 2026 · PS **SIH26051** · DRDO (Department of Defence Production / iDEX)
Software Based Model Development for Design of Area Specific Shelter for Thermal Comfort Maintenance.

A **100% free/open-source** pipeline that, given a location, downloads real climate data,
builds a shelter model, simulates its thermal response (fast RC model **and** EnergyPlus),
and compares designs (orientation × material × insulation) to recommend a thermally
comfortable passive shelter for the region.

Verified problem statement: [`docs/problem_statement.md`](docs/problem_statement.md)

---

## Quick start (this repo was already run for Prayagraj 2024)

```bash
pip install -r requirements.txt

# 1. Climate data: NASA POWER (primary) + Open-Meteo (cross-check) + EPW
python scripts/fetch_climate.py

# 2. First simulation: indoor vs outdoor temperature (RC model + plots)
python scripts/run_first_simulation.py

# 3. Same shelter, confirmed by the EnergyPlus engine
python scripts/run_first_simulation.py --energyplus

# 4. Parametric sweeps: orientation / wall material / insulation
python scripts/run_parametric.py

# 5. Tests
python -m pytest tests/ -v

# 6. Optimisation (Phase 6) — Optuna, ~40 trials in a few seconds
python scripts/run_optimization.py --trials 40

# 7. Dashboard (Phase 7) — 6 pages: Location → Climate → Design → Simulate → Optimize → Recommend
streamlit run app/dashboard.py
```

## What is already done (results in `results/`)

| Milestone | Status | Outputs |
|---|---|---|
| Phase 1 — climate data (POWER + Open-Meteo cross-check, EPW) | ✅ | `data/raw/*.csv`, `data/processed/climate_clean.csv`, `validation_report.json`, `data/external/prayagraj_2024.epw` |
| Phase 2/3 — first simulation | ✅ | `results/first_simulation_*.csv|html|png`, summary JSON |
| Phase 3 — EnergyPlus run | ✅ | `results/energyplus_hourly.csv`, `energyplus_hot_week.*` |
| Phase 5 — parametric sweeps | ✅ | `results/parametric/{orientation,material,insulation}_sweep.*` |
| Phase 6 — optimisation (Optuna, TPI objective) | ✅ | `results/optimization/` (best_design.json, all_trials.csv) |
| Phase 7 — Streamlit dashboard (6 pages) | ✅ | `app/dashboard.py` — run: `streamlit run app/dashboard.py` |

### Headline numbers — Prayagraj (25.44 N, 81.85 E), 2024, brick shelter 3×3×2.6 m

Latest optimisation (40 trials, Thermal Performance Index): best design =
**PUF sandwich-panel envelope + mineral-wool insulation, 255° orientation,
east window, TPI 0.53** — see `results/optimization/`.

- Hottest week (27 May–2 Jun): outdoor max **49.0 °C**; indoor RC **49.4 °C** / E+ **54.0 °C**.
- Cold week: indoor 16.0–27.1 °C → **93 % hours inside 18–32 °C** (no heating needed).
- 100 mm EPS on walls+roof cuts hottest-week mean indoor from **43.4 → 37.0 °C** (−6.4 °C)
  and night heat loss from −115 → −37 kWh.
- South-facing (0°) beats 225°/270° by ≈1 °C in the hottest week.
- Full-year E+ vs RC: 31.7 vs 32.1 °C mean indoor — good agreement for a fast model.

## Deploy — Supabase + Vercel

> ✅ **Status: Supabase is LIVE.** The project
> (`wfqcgxvoqibmljvglqsv` — "caladofficial's Project") has the schema, 12 seeded
> materials, the Prayagraj location, and 8784 hourly weather rows (2024) already
> loaded. The API reads/writes it (`/api/health` → `"backend":"supabase"`).
> Local credentials live in `.env` (gitignored).

The repo is deployment-ready:
- **`api/index.py`** — thin Vercel entrypoint (one-line re-export; the FastAPI
  app itself lives in `src/api_app.py` — see `docs/deployment.md` "Serverless
  entrypoint rules" for why the shim must stay minimal)
- **`public/`** — zero-build web frontend (Location → Climate → Design → Simulate →
  Optimize) — bold matte DRDO-inspired "Command Deck" UI with dark/light
  themes, HUD modules, live clock zero-build web frontend (Location → Climate → Design → Simulate → Optimize) data-link LED
- **`supabase/`** — Postgres schema + seed (materials, locations, weather cache, results)
- **`src/db/store.py`** — storage facade: Supabase (PostgREST over requests) when keys are set, else local SQLite
- **`src/data/solar.py`** — pure-NumPy NREL SPA + Erbs/isotropic-sky solar math,
  bit-equivalent to pvlib (verified in `scripts/validate_solar_math.py`) so the
  serverless bundle stays under Vercel's size limit

```bash
# 1. Supabase: run supabase/migrations/0001_init.sql + supabase/seed.sql in the SQL editor
# 2. Vercel: import the repo (auto-detects vercel.json), set env vars:
#    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
# 3. Preload the weather cache (optional, from your machine):
cp .env.example .env     # fill keys
make supabase
# 4. Deploy
vercel --prod
```

Full guide: [`docs/deployment.md`](docs/deployment.md). Local preview of the API:
`make api` then open http://localhost:8000/docs.

## Project layout

```
SIH26051_Shelter/
├── app/                  # Streamlit dashboard (Phase 7)
├── config/config.yaml    # EVERYTHING is configured here (location, shelter, simulation)
├── data/
│   ├── raw/              # untouched downloads
│   ├── processed/        # clean datasets + validation reports
│   └── external/         # materials.csv (sourced), EPW weather files
├── simulation/energyplus/# IDF generator + runner
├── src/
│   ├── data/             # NASA POWER, Open-Meteo, dataset builder
│   ├── geometry/         # shelter surfaces (areas, azimuths, tilts, openings)
│   ├── thermal/          # fast RC model + comfort metrics
│   ├── visualization/    # plotly HTML + matplotlib PNG
│   └── optimization/     # (Phase 6)
├── scripts/              # CLI entry points (fetch → simulate → sweep)
├── tests/                # pytest suite
├── docs/                 # problem statement, data/model notes, Windows setup
└── results/              # every run lands here
```

## Stack (all free/open source — ₹0)

NASA POWER · Open-Meteo · EnergyPlus 26.1 · pvlib (desktop validation only) ·
pandas/NumPy · FastAPI · Optuna · Plotly · matplotlib · pytest · SQLite ·
Supabase · Vercel · (later: ERA5/CDS, QGIS, OpenFOAM)

Windows setup instructions: [`docs/windows_setup.md`](docs/windows_setup.md)
