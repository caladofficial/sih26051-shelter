#!/usr/bin/env python3
"""Validate src/data/solar.py against pvlib 0.15.2 over a full year.

The RC model's solar geometry used to call pvlib directly; the serverless
API now uses the pure-NumPy ports in src/data/solar.py (NREL SPA + Erbs +
Hay-Davies + Spencer + Kasten-Young). This script proves numerical
equivalence: every quantity (sun position, DNI/DHI split, tilted-surface
irradiance) must match pvlib to < 1e-6 on the real 8760-hour Prayagraj
weather year.

Requires pvlib locally:  pip install -r requirements-dev.txt (or pvlib)

Usage:  python scripts/validate_solar_math.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pvlib                                                        # noqa: E402
from src.data import solar                                         # noqa: E402
from src.geometry.shelter import Shelter                           # noqa: E402
import src.thermal.rc_model as rc_model                            # noqa: E402

TOL = 1e-6   # max absolute difference accepted


def main() -> int:
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text())
    lat = float(cfg["location"]["latitude"])
    lon = float(cfg["location"]["longitude"])

    df = pd.read_csv(ROOT / "data" / "processed" / "climate_clean.csv",
                     parse_dates=["timestamp_local"])
    df = df.set_index("timestamp_local").sort_index()
    df.index = df.index.tz_convert(cfg["location"]["timezone"])
    print(f"weather rows: {len(df)}  ({df.index[0]} .. {df.index[-1]})")

    # ---------------------------------------------------------------- pvlib
    solpos_pv = pvlib.solarposition.get_solarposition(df.index, lat, lon)
    zenith = solpos_pv["apparent_zenith"].clip(upper=89.9)
    azimuth = solpos_pv["azimuth"]
    ghi = df["ghi"].clip(lower=0.0)
    erbs_pv = pvlib.irradiance.erbs(ghi, zenith, df.index)
    dni_extra_pv = pvlib.irradiance.get_extra_radiation(df.index)
    am_pv = pvlib.atmosphere.get_relative_airmass(zenith)
    albedo = float(cfg["simulation"]["albedo"])

    shelter = rc_model._build_shelter(cfg)
    surfaces = shelter.surfaces()
    poa_pv = {}
    for s in surfaces:
        poa_pv[s["name"]] = pvlib.irradiance.get_total_irradiance(
            surface_tilt=s["tilt_deg"], surface_azimuth=s["azimuth_deg"],
            solar_zenith=zenith, solar_azimuth=azimuth,
            dni=erbs_pv["dni"].clip(lower=0.0).fillna(0.0),
            ghi=ghi, dhi=erbs_pv["dhi"].clip(lower=0.0).fillna(0.0),
            dni_extra=dni_extra_pv, airmass=am_pv,
            albedo=albedo)["poa_global"].fillna(0.0).clip(lower=0.0)

    # --------------------------------------------------------------- ours
    solpos = solar.solar_position(df.index, lat, lon)
    zenith_o = solpos["apparent_zenith"].clip(upper=89.9)
    azimuth_o = solpos["azimuth"]
    erbs_o = solar.erbs(ghi, zenith_o, df.index)
    dni_extra_o = solar.get_extra_radiation(df.index)
    am_o = solar.get_relative_airmass(zenith_o)
    poa_o = {}
    for s in surfaces:
        poa_o[s["name"]] = solar.get_total_irradiance(
            surface_tilt=s["tilt_deg"], surface_azimuth=s["azimuth_deg"],
            solar_zenith=zenith_o, solar_azimuth=azimuth_o,
            dni=erbs_o["dni"].clip(lower=0.0).fillna(0.0),
            ghi=ghi, dhi=erbs_o["dhi"].clip(lower=0.0).fillna(0.0),
            dni_extra=dni_extra_o, airmass=am_o,
            albedo=albedo).fillna(0.0).clip(lower=0.0)

    # ------------------------------------------------------------- compare
    checks = [
        ("apparent_zenith [deg]", solpos_pv["apparent_zenith"], solpos["apparent_zenith"]),
        ("azimuth [deg]", solpos_pv["azimuth"], solpos["azimuth"]),
        ("equation_of_time [min]", solpos_pv["equation_of_time"], solpos["equation_of_time"]),
        ("dni (Erbs) [W/m2]", erbs_pv["dni"].clip(lower=0).fillna(0), erbs_o["dni"].clip(lower=0).fillna(0)),
        ("dhi (Erbs) [W/m2]", erbs_pv["dhi"].clip(lower=0).fillna(0), erbs_o["dhi"].clip(lower=0).fillna(0)),
        ("dni_extra (Spencer) [W/m2]", dni_extra_pv, dni_extra_o),
        ("relative airmass [-]", am_pv, am_o),
    ]
    for s in surfaces:
        checks.append((f"poa_global {s['name']} [W/m2]", poa_pv[s["name"]], poa_o[s["name"]]))

    worst = 0.0
    ok = True
    for label, a, b in checks:
        a = a.to_numpy(dtype=float)
        b = b.to_numpy(dtype=float)
        d = float(np.nanmax(np.abs(a - b))) if len(a) else 0.0
        worst = max(worst, d)
        status = "OK " if d < TOL else "FAIL"
        if d >= TOL:
            ok = False
        print(f"  [{status}] max|diff| {label:42s} = {d:.3e}")
        if d >= TOL:
            idx = int(np.nanargmax(np.abs(a - b)))
            print(f"          at {df.index[idx]}: pvlib={a[idx]:.10f} ours={b[idx]:.10f}")

    print(f"\nWORST max|diff| over {len(checks)} checks: {worst:.3e} (tol {TOL})")
    print("RESULT:", "PASS — solar math is pvlib-equivalent" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
