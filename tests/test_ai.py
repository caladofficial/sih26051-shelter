"""AI assistant tests: model card, instant predict (vs engine tolerance),
and engine-verified suggestion. Run: python -m pytest tests/test_ai.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from src.api_app import app

c = TestClient(app)

PY = {"name": "Prayagraj"}
JH = {"name": "Jaisalmer", "lat": 26.9157, "lon": 70.9083}


def _skip_unless_model():
    from src.paths import PROJECT_ROOT
    if not (PROJECT_ROOT / "src" / "data" / "ai_model.json").exists():
        pytest.skip("ai_model.json not trained yet")


def test_ai_info_model_card():
    _skip_unless_model()
    r = c.get("/api/ai/info")
    assert r.status_code == 200
    d = r.json()
    assert d["model"]["n_samples"] > 10_000
    assert d["model"]["n_sites"] >= 10
    for t in ("hot_mean_c", "hot_max_c", "hot_comfort_fraction", "cold_min_c"):
        assert t in d["accuracy"]
        assert d["accuracy"][t]["mae_c"] is not None or \
               d["accuracy"][t]["mae_fraction"] is not None
        assert d["accuracy"][t]["r2"] > 0.8, f"{t} r2 too low"
    assert "disclaimer" in d["note"] or "surrogate" in d["note"]


def test_ai_predict_within_engine_tolerance():
    _skip_unless_model()
    r = c.post("/api/ai/predict", json=PY)
    assert r.status_code == 200
    d = r.json()
    e = d["estimates"]
    for k in ("hot_mean_c", "hot_max_c", "cold_min_c"):
        assert isinstance(e[k], float)
    assert 0.0 <= e["hot_comfort_fraction"] <= 1.0
    # sanity: estimates must be physically plausible
    assert 20 < e["hot_mean_c"] < 55
    assert e["hot_max_c"] >= e["hot_mean_c"]
    assert "error_bars_c" in d and "model" in d

    # compare against the real engine (hot-week metrics) — surrogate must
    # stay within a disclosed tolerance band
    sim = c.post("/api/simulate", json={**PY, "period": "hot_week"}).json()
    m = sim["metrics"]
    assert abs(e["hot_mean_c"] - m["mean_indoor_c"]) < 3.0, \
        f"AI mean {e['hot_mean_c']} vs engine {m['mean_indoor_c']}"
    assert abs(e["hot_max_c"] - m["max_indoor_c"]) < 4.0, \
        f"AI peak {e['hot_max_c']} vs engine {m['max_indoor_c']}"


def test_ai_suggest_engine_verified():
    _skip_unless_model()
    r = c.post("/api/ai/suggest", json={**JH, "objective": "coolest_peak"})
    assert r.status_code == 200
    d = r.json()
    d_ = d["design"]
    for k in ("wall_material", "roof_material", "window_wall",
              "wall_thickness_m", "insulation_material"):
        assert k in d_
    v = d["verified"]
    for k in ("mean_indoor_c", "max_indoor_c", "comfort_fraction"):
        assert k in v
    assert len(d["alternatives"]) == 3
    # the suggestion's verified peak must beat (or tie) the default design's
    base = c.post("/api/location/compare", json={"sites": [JH]})
    assert base.status_code == 200
    base_peak = base.json()["sites"][0]["max_indoor_c"]
    assert v["max_indoor_c"] <= base_peak + 0.01, \
        f"suggested peak {v['max_indoor_c']} > default {base_peak}"


def test_ai_suggest_bad_objective():
    _skip_unless_model()
    r = c.post("/api/ai/suggest", json={**PY, "objective": "nonsense"})
    assert r.status_code == 400
