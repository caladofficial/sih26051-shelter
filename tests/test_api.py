"""API tests — FastAPI TestClient (no network needed for health/materials).

Run:  python -m pytest tests/test_api.py -v
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

client = None


@pytest.fixture(scope="module", autouse=True)
def _client():
    global client
    from fastapi.testclient import TestClient
    from api.index import app
    client = TestClient(app)
    return client


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_locations():
    r = client.get("/api/locations")
    assert r.status_code == 200
    locs = r.json()["locations"]
    assert locs and "latitude" in locs[0]


def test_materials():
    r = client.get("/api/materials")
    assert r.status_code == 200
    mats = r.json()["materials"]
    assert len(mats) >= 10
    fields = {"material", "k_W_mK", "density_kg_m3", "cp_J_kgK",
              "solar_absorptance", "emissivity", "source"}
    assert fields <= set(mats[0].keys())


def test_simulate_bad_material():
    r = client.post("/api/simulate", json={"wall_material": "not_a_material"})
    assert r.status_code == 400


def test_simulate_hot_week():
    r = client.post("/api/simulate", json={"period": "hot_week"})
    assert r.status_code == 200
    d = r.json()
    assert d["metrics"]["mean_indoor_c"] > 20
    assert d["metrics"]["max_indoor_c"] > d["metrics"]["mean_indoor_c"]
    assert len(d["series"]["ts"]) == 24 * 7      # one design week


def test_simulate_insulation_override():
    """50 mm EPS must cool the hottest week vs the uninsulated baseline."""
    base = client.post("/api/simulate", json={"period": "hot_week"}).json()
    ins = client.post("/api/simulate", json={
        "period": "hot_week",
        "insulation_material": "eps", "insulation_thickness_m": 0.05,
    }).json()
    assert ins["metrics"]["mean_indoor_c"] < \
        base["metrics"]["mean_indoor_c"] - 3.0


def test_optimize_small():
    r = client.post("/api/optimize", json={"n_trials": 8})
    assert r.status_code == 200
    d = r.json()
    assert d["best"]["tpi"] > 0
    assert len(d["top10"]) <= 8
    assert "wall_material" in d["best"]["design"]
    # the search space covers ventilation + roof pitch (see
    # tests/test_optimizer_space.py) and they must surface in results
    assert "roof_pitch_deg" in d["best"]["design"]
    assert "ach" in d["best"]["design"]
    p0 = d["top10"][0]["params"]
    assert 0.0 <= p0["roof_pitch_deg"] <= 30.0
    assert 0.5 <= p0["ach"] <= 10.0
