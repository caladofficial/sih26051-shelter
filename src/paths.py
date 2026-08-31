"""Project path helpers — every module should import paths from here."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIG_FILE = PROJECT_ROOT / "config" / "config.yaml"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
EXTERNAL_DIR = DATA_DIR / "external"
RESULTS_DIR = PROJECT_ROOT / "results"
MATERIALS_FILE = EXTERNAL_DIR / "materials.csv"
