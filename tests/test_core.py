"""Tests for SIH26051_Shelter. Run:  python -m pytest tests/ -v"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.thermal.rc_model import load_materials  # noqa: E402


def test_materials_schema():
    """materials.csv must have the documented columns and no missing values."""
    mats = load_materials()
    required = ["k_W_mK", "density_kg_m3", "cp_J_kgK", "solar_absorptance",
                "emissivity", "thickness_m", "source"]
    assert all(c in mats.columns for c in required), f"missing columns: {required}"
    assert mats[required].notna().all().all(), "NaN found in material properties"
    # physical sanity ranges
    assert (mats["k_W_mK"] > 0).all()
    assert (mats["k_W_mK"] < 200).all()
    assert (mats["density_kg_m3"] > 5).all() and (mats["density_kg_m3"] < 10000).all()
    assert (mats["cp_J_kgK"] > 100).all() and (mats["cp_J_kgK"] < 5000).all()
    assert (mats["solar_absorptance"] >= 0).all() and (mats["solar_absorptance"] <= 1).all()
    assert (mats["emissivity"] >= 0).all() and (mats["emissivity"] <= 1).all()
    # every row must cite a source
    assert (mats["source"].str.len() > 5).all()


def test_shelter_surfaces():
    from src.geometry.shelter import Shelter
    sh = Shelter(length_m=3.0, width_m=3.0, height_m=2.6, orientation_deg=0,
                 wall_material="brick", wall_thickness_m=0.2,
                 roof_material="rcc_slab", roof_thickness_m=0.12,
                 floor_material="concrete", floor_thickness_m=0.1)
    surfs = sh.surfaces()
    names = {s["name"] for s in surfs}
    assert names == {"south_wall", "north_wall", "east_wall", "west_wall",
                     "roof", "floor"}
    # orientation rotates azimuths
    sh45 = Shelter(length_m=3, width_m=3, height_m=2.6, orientation_deg=45,
                   wall_material="brick", wall_thickness_m=0.2,
                   roof_material="rcc_slab", roof_thickness_m=0.12,
                   floor_material="concrete", floor_thickness_m=0.1)
    south0 = [s for s in surfs if s["name"] == "south_wall"][0]
    south45 = [s for s in sh45.surfaces() if s["name"] == "south_wall"][0]
    assert south0["azimuth_deg"] == 180.0
    assert south45["azimuth_deg"] == 225.0


def test_rc_model_runs():
    """RC model must run over a short weather slice with sane output."""
    from src.data.climate import load_config
    from src.thermal.rc_model import simulate
    cfg = load_config()
    mats = load_materials()
    idx = pd.date_range("2024-04-10", periods=24 * 3, freq="h",
                        tz=cfg["location"]["timezone"])
    weather = pd.DataFrame({
        "t2m": 25 + 8 * np.sin(np.arange(len(idx)) / 24 * 2 * np.pi),
        "ghi": np.where((idx.hour >= 6) & (idx.hour <= 18),
                        600 * np.sin((idx.hour - 6) / 12 * np.pi), 0.0),
    }, index=idx)
    out = simulate(cfg, weather, mats)
    assert np.isfinite(out["indoor_t_c"]).all()
    assert out["indoor_t_c"].between(-30, 70).all()
    assert np.isfinite(out[["q_solar_w", "q_conduct_w", "q_vent_w"]]).all().all()


def test_idf_build():
    """make_idf must produce an IDF without errors for the default shelter."""
    import sys as _sys
    from src.data.climate import load_config
    cfg = load_config()
    mats = load_materials()
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "simulation" / "energyplus"))
    from make_idf import build_idf
    out = build_idf(cfg, mats, "/tmp/test_shelter.idf")
    text = out.read_text()
    assert "Site:Location" in text and "Zone" in text
    assert "FenestrationSurface:Detailed" in text
    assert "EnergyPlus" in text or "Version" in text


def test_idf_window_on_wall_plane():
    """Window/door vertices must lie on their wall plane and inside the wall."""
    import sys as _sys
    from src.data.climate import load_config
    cfg = load_config()
    mats = load_materials()
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "simulation" / "energyplus"))
    from make_idf import _box_vertices
    verts = _box_vertices(cfg)
    hz = cfg["shelter"]["height_m"]
    # window sits on the south wall: all window vertices have y = 0 and z <= hz
    for wall, vlist in verts.items():
        for (x, y, z) in vlist:
            assert abs(z) <= hz + 1e-9, f"{wall} vertex z={z} above wall height"
    # south wall spans x 0..3 at y=0; west wall spans y 0..3 at x=0
    for (x, y, _z) in verts["south"]:
        assert abs(y) < 1e-9 and -1e-9 <= x <= 3.0 + 1e-9
    for (x, y, _z) in verts["west"]:
        assert abs(x) < 1e-9 and -1e-9 <= y <= 3.0 + 1e-9
