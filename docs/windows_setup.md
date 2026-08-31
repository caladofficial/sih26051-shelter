# Windows setup (for the SIH team laptop)

Everything below is the exact sequence from the project plan (Phase 0), verified on Linux;
the pip/venv/git steps are identical on Windows (paths differ slightly).

## 0. Prerequisites

1. Install Python 3.11+ from python.org — **tick "Add Python to PATH"** during install.
2. Install Git (git-scm.com) and VS Code.
3. Install EnergyPlus (NREL, BSD open source):
   https://github.com/NREL/EnergyPlus/releases — pick the latest Windows installer
   (e.g. `EnergyPlus-26.1.0-...-Windows-x86_64.exe`). Note the install path, e.g.
   `C:\EnergyPlusV26-1-0\` (it is auto-discovered by `run_energyplus.py`).

## 1. Project + environment

```bat
cd C:\SIH26051_Shelter
python -m venv .venv
.venv\Scripts\activate            :: PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Ladybug (optional, weather-file tooling):
```bat
python -m pip install -U ladybug-core honeybee-energy
:: check:  ladybug viz
```

## 2. Verify the stack

```bat
python -c "import numpy,pandas,scipy,requests,pvlib,plotly,matplotlib,pytest; print('core OK')"
python -m pytest tests\ -v
```

EnergyPlus check: build the model and let the runner find the engine:

```bat
python scripts\run_first_simulation.py --energyplus
```

If the executable is not found, either add EnergyPlus to PATH or set:
```bat
set ENERGYPLUS_DIR=C:\EnergyPlusV26-1-0
```

## 3. First data + first simulation

```bat
python scripts\fetch_climate.py              :: NASA POWER + Open-Meteo + EPW
python scripts\run_first_simulation.py       :: RC model, indoor vs outdoor plot
python scripts\run_parametric.py             :: orientation/material/insulation sweeps
```

Change the target region in `config\config.yaml` (name, latitude, longitude, elevation,
timezone, data year) — nothing else needs editing.

## 4. Notes

- The dashboard (`app\`) comes in Phase 7: `streamlit run app\dashboard.py`.
- Keep `data\raw\` untouched; all derived files live in `data\processed\` and `results\`.
- Commit early, commit often — one repo holds code, data, results, docs and deck assets.
