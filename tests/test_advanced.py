"""Advanced analytics tests — multi-zone comparison, thermal mass physics,
wind rose / diurnal profiles, monthly comfort. Run: python -m pytest tests/test_advanced.py -v
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _client():
    global client
    from fastapi.testclient import TestClient
    from api.index import app
    client = TestClient(app)
    return client


def test_profile_has_wind_rose_and_diurnal():
    p = client.post("/api/location/profile", json={}).json()
    rose = p["wind_rose"]
    assert len(rose) == 16
    labels = {r["label"] for r in rose}
    assert labels == {"N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                      "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"}
    total_freq = sum(r["freq_pct"] for r in rose)
    assert 99.0 < total_freq <= 100.5, total_freq   # sectors cover all hours
    assert all(r["mean_ws_ms"] >= 0 for r in rose)
    d = p["diurnal"]
    assert len(d) == 24
    assert [x["hour"] for x in d] == list(range(24))
    assert all(x["mean_c"] is not None for x in d)
    assert p["diurnal_range_c"] > 0


def test_compare_all_five_zones():
    j = client.post("/api/location/compare", json={}).json()
    assert len(j["sites"]) == 5
    zones = {s["site"]: s["zone"] for s in j["sites"]}
    assert zones["Jaisalmer"] == "hot_dry"
    assert zones["Leh"] == "cold"
    assert zones["Chennai"] == "warm_humid"
    assert zones["Prayagraj"] == "composite"
    for s in j["sites"]:
        assert "mean_indoor_c" in s and "max_indoor_c" in s
        assert "comfort_fraction" in s
        assert s["weather_source"]
    assert j["best"] and j["best"]["site"] in zones
    # physically sensible: cold site far cooler than hot-dry for same design
    by_site = {s["site"]: s["mean_indoor_c"] for s in j["sites"]}
    assert by_site["Leh"] < by_site["Jaisalmer"]
    assert by_site["Leh"] < by_site["Prayagraj"]


def test_compare_with_custom_design():
    design = {"length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
              "wall_material": "brick", "wall_thickness_m": 0.23,
              "roof_material": "rcc_slab", "roof_thickness_m": 0.12,
              "insulation_material": "eps", "insulation_thickness_m": 0.1,
              "window_wall": "north", "window_width_m": 0.8,
              "window_height_m": 0.8, "ach": 6}
    j = client.post("/api/location/compare", json={"design": design,
                                                   "sites": [{"name": "Prayagraj", "lat": 25.4358, "lon": 81.8463}]}).json()
    assert len(j["sites"]) == 1
    s = j["sites"][0]
    assert s["mean_indoor_c"] < 43.0      # insulated design beats the uninsulated baseline


def test_structure_has_thermal_mass():
    st = client.post("/api/cad/structure", json={
        "wall_material": "brick", "wall_thickness_m": 0.23,
        "roof_material": "rcc_slab", "roof_thickness_m": 0.12,
        "insulation_material": "eps", "insulation_thickness_m": 0.1,
    }).json()
    tm = st["thermal_mass"]
    assert "wall_assembly_lag_hours" in tm
    assert "roof_assembly_lag_hours" in tm
    assert tm["wall_assembly_lag_hours"] > 0
    assert 0 < tm["wall_decrement_factor"] < 1
    assert 0 < tm["roof_decrement_factor"] < 1
    assert "note" in tm
    # more insulation -> larger lag
    st2 = client.post("/api/cad/structure", json={
        "wall_material": "brick", "wall_thickness_m": 0.23,
        "roof_material": "rcc_slab", "roof_thickness_m": 0.12,
        "insulation_material": "eps", "insulation_thickness_m": 0.2,
    }).json()
    assert (st2["thermal_mass"]["wall_assembly_lag_hours"] >
            tm["wall_assembly_lag_hours"])


def test_full_year_monthly_comfort():
    j = client.post("/api/simulate", json={"period": "full_year"}).json()
    mc = j["metrics"]["monthly_comfort"]
    assert len(mc) == 12
    assert [m["month"] for m in mc] == list(range(1, 13))
    assert all(0 <= m["comfort_fraction"] <= 1 for m in mc)
    # Prayagraj: winter comfortable, peak summer not
    by_month = {m["month"]: m["comfort_fraction"] for m in mc}
    assert by_month[1] > by_month[5]
    assert by_month[12] > by_month[6]
