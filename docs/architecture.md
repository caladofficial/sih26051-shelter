# SIH26051 — Architecture

```
                    ┌──────────────────┐
                    │   USER / DESIGNER │
                    └────────┬─────────┘
                             ↓
                 ┌───────────────────────┐
                 │  STREAMLIT DASHBOARD  │   (Phase 7)
                 └───────────┬───────────┘
                             ↓
       ┌─────────────────────────────────────────┐
       │          INPUT / DATA LAYER             │
       │   NASA POWER │ Open-Meteo │ EPW │ DEM  │
       └────────────────────┬────────────────────┘
                            ↓
       ┌─────────────────────────────────────────┐
       │             MODEL ENGINE                │
       │   fast RC model (sweeps) + EnergyPlus   │
       │   (confirmation)  ·  pvlib solar        │
       └────────────────────┬────────────────────┘
                            ↓
       ┌─────────────────────────────────────────┐
       │          OPTIMIZATION ENGINE            │   (Phase 6)
       │            SciPy / Optuna               │
       └────────────────────┬────────────────────┘
                            ↓
       ┌─────────────────────────────────────────┐
       │              OUTPUTS                    │
       │  Temperature │ Heat Flow │ Solar Gain    │
       │  Thermal Map │ Ranking   │ Recommendation│
       └─────────────────────────────────────────┘
```

## Data flow

```
config/config.yaml  ──►  fetch_climate.py  ──►  data/raw (POWER + Open-Meteo)
                                                     │ cross-check (bias/RMSE/corr)
                                                     ▼
                                             climate_clean.csv (local tz)
                                                     │            └──► .epw (EnergyPlus)
                                                     ▼
                              make_idf.py ──► shelter.idf ──► EnergyPlus 26.1
                                                     ▼
                     run_first_simulation.py / run_parametric.py
                        (RC model sweeps; E+ confirmation runs)
                                                     ▼
                                           results/*.csv|html|png
```

## Design decisions

1. **One config, two engines.** `config/config.yaml` is the single source of truth; the
   fast RC model and the EnergyPlus IDF are both generated from it, so sweeps and
   confirmations always describe the same shelter.
2. **POWER primary, Open-Meteo cross-check.** Never blindly averaged; ERA5 later for
   validation.
3. **RC model for search, EnergyPlus for confirmation.** The optimizer (Phase 6) will call
   the RC model (≈1 s/year); the top designs get an EnergyPlus check.
4. **Materials with provenance.** Every row of `materials.csv` cites a source; no blog
   values.
5. **SQLite for everything small** (simulations, results) — free, file-based, no server.
6. **Free stack only.** EnergyPlus (BSD) replaces ANSYS; pvlib replaces paid solar tools;
   Optuna/SciPy replace paid optimizers; Streamlit replaces paid dashboards.

## Phase status

| Phase | Content | Status |
|---|---|---|
| 0 | Environment, stack, repo | ✅ (this repo; Windows guide in docs/) |
| 1 | NASA POWER + Open-Meteo + materials DB | ✅ |
| 2 | Physics: pvlib solar, heat transfer, geometry | ✅ |
| 3 | EnergyPlus shelter model + first run | ✅ |
| 4 | Validation (POWER vs Open-Meteo vs ERA5) | partial (POWER↔OM done; ERA5 next) |
| 5 | Parametric: material × orientation × insulation | ✅ |
| 6 | Optimisation (SciPy / Optuna) | next |
| 7 | Streamlit dashboard | next |
| 8 | Advanced: OpenFOAM CFD, GIS | later |
| 9 | SIH demo: location → climate → design → simulate → optimize → recommend | after 6–7 |
