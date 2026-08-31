# SIH26051_Shelter — common commands
.PHONY: install data sim eplus sweep opt api test deploy supabase

install:            ## install all dependencies (runtime + dev)
	python -m pip install -r requirements.txt -r requirements-dev.txt

data:               ## Phase 1: fetch NASA POWER + Open-Meteo + EPW
	python scripts/fetch_climate.py

sim:                ## first simulation (fast RC model + plots)
	python scripts/run_first_simulation.py

eplus:              ## EnergyPlus confirmation run (needs EnergyPlus installed)
	python scripts/run_first_simulation.py --energyplus

sweep:              ## Phase 5: orientation × material × insulation sweeps
	python scripts/run_parametric.py

opt:                ## Phase 6: Optuna optimization
	python scripts/run_optimization.py --trials 40

api:                ## run the FastAPI server locally
	uvicorn api.index:app --reload --port 8000

web:                ## run the Streamlit dashboard locally
	streamlit run app/dashboard.py

test:               ## run the test suite
	python -m pytest tests/ -v

supabase:           ## sync local data into Supabase (set .env first)
	python scripts/sync_supabase.py --weather-year 2024

deploy:             ## deploy to Vercel (requires: vercel CLI + login)
	vercel --prod
