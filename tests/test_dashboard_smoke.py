"""Dashboard smoke test — boots the Streamlit app and visits every page.

Uses streamlit.testing.v1.AppTest (no browser needed).
Run:  python -m pytest tests/test_dashboard_smoke.py -v
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _boot(page: str):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=180)
    at.run()
    assert not at.exception, f"boot failed: {at.exception}"
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, f"page {page!r} raised: {at.exception}"
    return at


def test_pages_boot():
    for page in ["1 · Location", "2 · Climate", "3 · Shelter design",
                 "4 · Simulation", "5 · Optimization", "6 · Recommendation"]:
        _boot(page)
        print(f"  OK: {page}")


if __name__ == "__main__":
    test_pages_boot()
    print("dashboard smoke test passed")
