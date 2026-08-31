"""Sync local data (materials, locations, weather, results) into Supabase.

Usage:
    cp .env.example .env        # fill in SUPABASE_URL + SERVICE_ROLE_KEY
    python scripts/sync_supabase.py [--weather-year 2024] [--with-results]

Reads SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY from the environment or .env.
Without keys it prints instructions and exits.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402
from src.data.climate import load_config, load_clean  # noqa: E402
from src.db.store import Store  # noqa: E402
from src.thermal.rc_model import load_materials  # noqa: E402

load_dotenv()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weather-year", type=int, default=None,
                        help="also upload the clean hourly weather for this year")
    parser.add_argument("--with-results", action="store_true",
                        help="also upload simulation/optimization records from results/")
    args = parser.parse_args()

    store = Store()
    if store.backend != "supabase":
        print("✗ No Supabase credentials found. Copy .env.example to .env and "
              "set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY.")
        return 1

    cfg = load_config()

    n = store.upsert_materials(load_materials())
    print(f"✓ materials: {n} rows")

    loc = cfg["location"]
    loc["location_id"] = f"loc_{abs(loc['latitude']):.4f}_{abs(loc['longitude']):.4f}"
    store.upsert_location(loc)
    print(f"✓ location: {loc['name']} ({loc['latitude']}, {loc['longitude']})")

    if args.weather_year:
        weather = load_clean()
        # same location_id convention as the API cache (api/index.py)
        location_id = f"loc_{abs(cfg['location']['latitude']):.4f}_" \
                      f"{abs(cfg['location']['longitude']):.4f}"
        n = store.save_weather(weather, location_id)
        print(f"✓ weather {args.weather_year}: {n} hourly rows cached")

    if args.with_results:
        import json
        sim_dir = Path("results")
        if (sim_dir / "first_simulation_summary.json").exists():
            summary = json.loads((sim_dir / "first_simulation_summary.json").read_text())
            print("✓ results/ summary present — upload via the API instead "
                  "(POST /api/simulate persists automatically).")

    print("\nDone. Verify in the Supabase dashboard: Table Editor → public.materials")
    return 0


if __name__ == "__main__":
    sys.exit(main())
