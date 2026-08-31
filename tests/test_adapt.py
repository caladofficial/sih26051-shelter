"""Site-adaptation tests — location profiling + design recommendation.

The recommendation engine must: classify the site into a documented
NBC 2016-style climate zone, prescribe materials that exist in the sourced
materials table, and validate the prescription with the RC engine
(never assert performance from the rule table).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

ZONES = {"hot_dry", "warm_humid", "composite", "temperate", "cold"}


@pytest.fixture(scope="module", autouse=True)
def _client():
    global client
    from fastapi.testclient import TestClient
    from api.index import app
    client = TestClient(app)
    return client


def test_location_profile():
    r = client.post("/api/location/profile", json={})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["zone"] in ZONES
    assert p["zone_name"] == p["zone"].replace("_", "-").upper()
    assert p["n_hours"] > 8000
    assert p["t_hottest_month_c"] > p["t_coldest_month_c"]
    assert p["diurnal_range_c"] > 0
    assert p["hdd18"] >= 0 and p["cdd18"] >= 0
    assert 0 <= p["rh_mean_pct"] <= 100
    assert p["ghi_mean_w_m2"] > 0
    assert p["wind_mean_ms"] >= 0
    assert 0 <= p["wet_hours_pct"] <= 100
    assert len(p["guidance"]) >= 3
    assert p["weather_source"]


def test_location_profile_custom_coords():
    # a cold site (Leh, Ladakh) must classify as cold
    r = client.post("/api/location/profile",
                    json={"lat": 34.1526, "lon": 77.5771, "year": 2024})
    assert r.status_code in (200, 502), r.text      # 502 only if weather fetch fails
    if r.status_code == 200:
        assert r.json()["zone"] == "cold"


def test_recommend_validates_with_engine():
    design = {"length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
              "wall_material": "brick", "wall_thickness_m": 0.2,
              "roof_material": "rcc_slab", "roof_thickness_m": 0.12,
              "insulation_material": "none", "insulation_thickness_m": 0,
              "window_wall": "south", "window_width_m": 1.2,
              "window_height_m": 1.2}
    r = client.post("/api/location/recommend", json={"design": design})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["profile"]["zone"] in ZONES
    rec = j["recommendation"]
    d = rec["design"]
    # footprint preserved from current design
    assert d["length_m"] == 3.0 and d["width_m"] == 3.0
    # materials exist in the sourced table
    from src.thermal.rc_model import load_materials
    mats = load_materials()
    assert d["wall_material"] in mats.index
    assert d["roof_material"] in mats.index
    assert d["insulation_material"] in mats.index
    assert d["window_wall"] in ("north", "south")
    assert d["insulation_thickness_m"] >= 0
    assert len(rec["rationale"]) >= 6
    # engine validation numbers present
    assert "mean_indoor_c" in j["baseline_metrics"]
    assert "mean_indoor_c" in j["recommended_metrics"]
    assert "mean_indoor_c" in j["delta"]


def test_recommend_hot_dry_site_specifies_more_insulation():
    # dry hot site -> hot_dry rules (if network allows the fetch)
    r = client.post("/api/location/recommend",
                    json={"lat": 26.9, "lon": 70.9, "year": 2024})  # Jaisalmer
    assert r.status_code in (200, 502)
    if r.status_code == 200:
        j = r.json()
        if j["profile"]["zone"] == "hot_dry":
            d = j["recommendation"]["design"]
            assert d["insulation_thickness_m"] >= 0.08
            assert d["window_wall"] == "north"


def test_shelter_metrics_include_zone():
    """Fleet rows carry the climate zone of their location."""
    from src.api_app import _shelter_metrics
    m = _shelter_metrics(25.4358, 81.8463, {
        "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
        "wall_material": "brick", "wall_thickness_m": 0.2,
        "roof_material": "rcc_slab", "roof_thickness_m": 0.12,
        "insulation_material": "eps", "insulation_thickness_m": 0.05,
    })
    assert m is not None
    assert m.get("zone") in ZONES
    assert m.get("zone_name")
