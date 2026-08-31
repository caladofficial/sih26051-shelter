"""Regression tests for site fidelity in the simulation pipeline.

Two production bugs are pinned here:

1. `_apply_design` silently dropped `window_u_w_m2k` (it landed on a
   top-level config key that `_build_shelter` never reads), so every
   engine run used the default glazing U (5.8 W/m2K) no matter what the
   design asked for — the AI-suggest "verified" metrics and the training
   dataset were all computed with the wrong glazing.

2. Solar geometry (SPA sun position) is computed from cfg['location'],
   which kept the config default (Prayagraj) for every site, so any
   site-adapted simulation (Leh/Dras/Kargil/...) used the wrong sun
   position (up to ~9.4 deg zenith error at Leh).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.api_app import CFG, _apply_design, _apply_site_location  # noqa: E402
from src.thermal.rc_model import _build_shelter  # noqa: E402


def test_window_u_mapping_reaches_engine():
    d = {"window_u_w_m2k": 1.6, "window_shgc": 0.5,
         "window_width_m": 1.2, "window_height_m": 1.2}
    cfg = _apply_design(CFG, d)
    assert cfg["shelter"]["window"]["u_w_m2k"] == 1.6
    assert cfg["shelter"]["window"]["shgc"] == 0.5
    sh = _build_shelter(cfg)
    assert sh.window.u_w_m2k == 1.6
    for s in sh.surfaces():
        if s["type"] == "window":
            assert s["u_w_m2k"] == 1.6


def test_site_location_applied_to_cfg():
    cfg = _apply_site_location(CFG, 34.164, 77.585, "Asia/Kolkata")
    assert cfg["location"]["latitude"] == 34.164
    assert cfg["location"]["longitude"] == 77.585
    assert cfg["location"]["timezone"] == "Asia/Kolkata"
    # original not mutated
    assert CFG["location"]["latitude"] != 34.164


def test_site_location_changes_solar_geometry():
    """At Leh (34.16 N) the sun position must differ from Prayagraj (25.4 N)
    — a canary that SPA actually consumes the site coordinates."""
    import pandas as pd
    from src.data import solar

    idx = pd.date_range("2024-07-15 10:00:00", periods=1, freq="h",
                        tz="Asia/Kolkata")
    zen_py = float(solar.solar_position(idx, 25.4358, 81.8463)
                   ["apparent_zenith"].iloc[0])
    zen_leh = float(solar.solar_position(idx, 34.164, 77.585)
                    ["apparent_zenith"].iloc[0])
    assert abs(zen_py - zen_leh) > 3.0, "SPA must be site-sensitive"
