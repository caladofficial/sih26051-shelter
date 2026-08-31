"""Feature tests — design library CRUD, stats, insulation sweep, report,
envelope mass. Run:  python -m pytest tests/test_features.py -v
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


DESIGN = {
    "length_m": 3.0, "width_m": 3.0, "height_m": 2.6,
    "orientation_deg": 0, "wall_material": "brick", "wall_thickness_m": 0.2,
    "roof_material": "rcc_slab", "roof_thickness_m": 0.12,
    "floor_material": "concrete", "floor_thickness_m": 0.1,
    "insulation_material": "eps", "insulation_thickness_m": 0.05,
    "window_wall": "south", "window_width_m": 1.2, "window_height_m": 1.2,
    "window_sill_m": 0.9, "window_shgc": 0.82, "window_u_w_m2k": 5.8,
}


# ------------------------------------------------------------ design library
def test_designs_crud():
    r = client.post("/api/designs", json={
        "name": "pytest design", "design": DESIGN, "notes": "test row"})
    assert r.status_code == 200
    did = r.json()["design_id"]
    assert did.startswith("dsg_")
    try:
        r = client.get(f"/api/designs/{did}")
        assert r.status_code == 200
        assert r.json()["name"] == "pytest design"
        assert r.json()["design"]["wall_material"] == "brick"
        # list contains it
        r = client.get("/api/designs?limit=50")
        assert any(d["design_id"] == did for d in r.json()["designs"])
        # patch rename + favorite
        r = client.patch(f"/api/designs/{did}",
                         json={"name": "renamed", "favorite": True})
        assert r.status_code == 200
        assert r.json()["name"] == "renamed"
        # favorite-only list
        r = client.get("/api/designs?favorite_only=true&limit=50")
        assert any(d["design_id"] == did for d in r.json()["designs"])
    finally:
        r = client.delete(f"/api/designs/{did}")
        assert r.status_code == 200
        r = client.get(f"/api/designs/{did}")
        assert r.status_code == 404


def test_designs_404():
    assert client.get("/api/designs/dsg_doesnotexist").status_code == 404
    assert client.delete("/api/designs/dsg_doesnotexist").status_code == 404


# -------------------------------------------------------------------- stats
def test_stats():
    r = client.get("/api/stats")
    assert r.status_code == 200
    j = r.json()
    for k in ("simulations", "optimizations", "designs", "cad_imports"):
        assert isinstance(j[k], int) and j[k] >= 0


# -------------------------------------------------------------------- sweep
def test_sweep():
    r = client.post("/api/sweep", json={
        "thicknesses_mm": [0, 50], "period": "hot_week"})
    assert r.status_code == 200
    j = r.json()
    assert j["period"] == "hot_week"
    assert len(j["points"]) == 2
    assert [p["thickness_mm"] for p in j["points"]] == [0, 50]
    for p in j["points"]:
        assert "mean_indoor_c" in p and "comfort_fraction" in p
    # more insulation should not raise indoor temperatures vs 0 mm
    assert j["points"][1]["mean_indoor_c"] <= j["points"][0]["mean_indoor_c"] + 0.5
    assert j["best"]["thickness_mm"] in (0, 50)


def test_sweep_bad_grid():
    r = client.post("/api/sweep", json={"thicknesses_mm": [-5, 5000]})
    assert r.status_code == 400


# ------------------------------------------------------------------ report
def test_report():
    r = client.get("/api/cad/report?length_m=3&width_m=3")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers.get("content-disposition", "")
    body = r.text
    for marker in ("SHELTER-STRUCTURE REPORT", "Envelope & surfaces",
                   "Envelope assembly", "Mass breakdown",
                   "**Total**", "U = ", "brick"):
        assert marker in body


# -------------------------------------------------------------------- mass
def test_structure_mass():
    r = client.post("/api/cad/structure", json=DESIGN)
    assert r.status_code == 200
    m = r.json()["mass"]
    assert m["total_mass_kg"] > 1000        # a 3x3x2.6 shelter is heavy
    assert m["note"].startswith("mass = density")
    by_comp = {c["component"]: c for c in m["components"]}
    assert by_comp["floor"]["mass_kg"] is not None
    assert by_comp["window"]["mass_kg"] is None    # glass has no density row
