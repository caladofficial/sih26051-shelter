"""Solar geometry + irradiance decomposition for the thermal model.

Pure-NumPy replacement for the three pvlib calls used by the RC model,
chosen so the serverless (Vercel) API does not have to ship pvlib +
scipy + h5py in its bundle. Every formula below is a *verbatim port* of
the pvlib 0.15.2 implementation (BSD-3), not an approximation:

    * sun position ........ NREL SPA  (vendored: src/data/_spa.py)
      (Reda & Andreas 2004; the same algorithm pvlib's
       ``solarposition.get_solarposition(method='nrel_numpy')`` runs)
    * DNI/DHI split ....... Erbs 1982 decomposition (pvlib.irradiance.erbs)
    * extraterrestrial .... Spencer 1971 Fourier series
                            (pvlib.irradiance.get_extra_radiation)
    * relative airmass .... Kasten & Young 1989
                            (pvlib.atmosphere.get_relative_airmass)
    * tilted-surface ...... isotropic sky diffuse + ground reflection
                            (pvlib.irradiance.get_total_irradiance verbatim,
                            which defaults to model='isotropic')

Numerical equivalence with pvlib is verified over a full year in
scripts/validate_solar_math.py (max |diff| < 1e-6).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.data import _spa

SOLAR_CONSTANT = 1366.1       # W/m2 — pvlib default (WMO 1981)
MIN_COS_ZENITH = 0.065        # pvlib erbs default (zenith = 86.273 deg)
MAX_ZENITH_DNI = 87.0         # pvlib erbs default: DNI forced to 0 above
ATMOS_REFRACT = 0.5667        # pvlib spa_python default (deg)
PRESSURE_PA = 101325.0        # pvlib get_solarposition default
TEMPERATURE_C = 12.0          # pvlib get_solarposition default


def _to_unixtime(index: pd.DatetimeIndex) -> np.ndarray:
    """UTC epoch seconds, exactly like pvlib._datetime_to_unixtime.

    ``DatetimeIndex.asi8`` returns the raw integer in the dtype's own unit:
    nanoseconds on pandas 2.x, microseconds on pandas 3.x (tz-aware
    indices default to microsecond resolution there). Rescaling from the
    dtype unit — instead of assuming nanoseconds — keeps the SPA timestamps
    correct on every pandas version (bit-identical unixtime in both).
    """
    if getattr(index, "tz", None) is not None:
        index = index.tz_convert("UTC")
    unit = getattr(index.dtype, "unit", None)
    if unit is None:                       # pandas < 2.1 fallback
        dstr = str(index.dtype)
        unit = dstr.split("[")[1].split(",")[0] if "[" in dstr else "ns"
    scale = {"ns": 1.0, "us": 1e3, "ms": 1e6, "s": 1e9}.get(str(unit).strip(), 1.0)
    return index.asi8.astype(np.float64) * scale / 1e9


def solar_position(index: pd.DatetimeIndex, latitude: float, longitude: float,
                   altitude: float = 0.0, pressure: float = PRESSURE_PA,
                   temperature: float = TEMPERATURE_C) -> pd.DataFrame:
    """Apparent solar position via NREL SPA (pvlib-equivalent output).

    Returns a DataFrame indexed like `index` with the same columns pvlib's
    ``solarposition.get_solarposition(method='nrel_numpy')`` returns:
    apparent_zenith, zenith, apparent_elevation, elevation, azimuth,
    equation_of_time (all in degrees except EoT in minutes).
    """
    unixtime = _to_unixtime(index)
    # pvlib's get_solarposition does NOT pass delta_t, so spa_python's
    # default delta_t = 67.0 s (TT-UT1 for ~2024) is used — replicate that.
    delta_t = np.full(len(unixtime), 67.0)

    theta, theta0, e, e0, phi, eot = _spa.solar_position(
        unixtime, latitude, longitude, altitude, pressure / 100.0,
        temperature, delta_t, ATMOS_REFRACT, numthreads=1)

    return pd.DataFrame({
        "apparent_zenith": theta,
        "zenith": theta0,
        "apparent_elevation": e,
        "elevation": e0,
        "azimuth": phi,
        "equation_of_time": eot,
    }, index=index)


def get_extra_radiation(index: pd.DatetimeIndex) -> pd.Series:
    """Extraterrestrial normal irradiance, Spencer 1971 (pvlib default)."""
    # pvlib's _pandas_to_doy uses UTC day-of-year (pandas dayofyear is local)
    utc = index.tz_convert("UTC") if getattr(index, "tz", None) else index
    doy = utc.dayofyear.to_numpy()
    b = 2.0 * np.pi * (doy - 1) / 365.0
    r2 = (1.00011 + 0.034221 * np.cos(b) + 0.00128 * np.sin(b) +
          0.000719 * np.cos(2 * b) + 7.7e-05 * np.sin(2 * b))
    return pd.Series(SOLAR_CONSTANT * r2, index=index)


def clearness_index(ghi, zenith, extra_radiation) -> np.ndarray:
    """kt — pvlib.irradiance.clearness_index verbatim (defaults)."""
    cos_zenith = np.cos(np.radians(zenith))
    i0h = extra_radiation * np.maximum(cos_zenith, MIN_COS_ZENITH)
    kt = ghi / i0h
    kt = np.maximum(kt, 0)
    kt = np.minimum(kt, 1.0)          # pvlib erbs passes max_clearness_index=1
    return kt


def erbs(ghi: pd.Series, zenith: pd.Series,
         index: pd.DatetimeIndex) -> pd.DataFrame:
    """Erbs 1982 DNI/DHI split — pvlib.irradiance.erbs verbatim (defaults)."""
    dni_extra = get_extra_radiation(index).to_numpy()
    ghi_n = ghi.to_numpy()
    zen_n = zenith.to_numpy()

    kt = clearness_index(ghi_n, zen_n, dni_extra)

    df = 1 - 0.09 * kt
    df = np.where((kt > 0.22) & (kt <= 0.8),
                  0.9511 - 0.1604 * kt + 4.388 * kt ** 2 -
                  16.638 * kt ** 3 + 12.336 * kt ** 4, df)
    df = np.where(kt > 0.8, 0.165, df)

    dhi = df * ghi_n
    dni = (ghi_n - dhi) / np.cos(np.radians(zen_n))
    bad = (zen_n > MAX_ZENITH_DNI) | (ghi_n < 0) | (dni < 0)
    dni = np.where(bad, 0.0, dni)
    dhi = np.where(bad, ghi_n, dhi)   # keep closure GHI = DNI + DHI valid

    return pd.DataFrame({"dni": dni, "dhi": dhi, "kt": kt}, index=index)


def get_relative_airmass(zenith: pd.Series) -> pd.Series:
    """Kasten & Young 1989 — pvlib.atmosphere.get_relative_airmass."""
    z = zenith.to_numpy()
    am = 1.0 / (np.cos(np.radians(z)) +
                0.50572 * (6.07995 + (90.0 - z)) ** -1.6364)
    return pd.Series(am, index=zenith.index)


def _aoi_projection(tilt, surf_az, zen, sun_az) -> np.ndarray:
    proj = (np.cos(np.radians(tilt)) * np.cos(np.radians(zen)) +
            np.sin(np.radians(tilt)) * np.sin(np.radians(zen)) *
            np.cos(np.radians(sun_az - surf_az)))
    return np.clip(proj, -1, 1)


def get_total_irradiance(surface_tilt: float, surface_azimuth: float,
                         solar_zenith: pd.Series, solar_azimuth: pd.Series,
                         dni: pd.Series, ghi: pd.Series, dhi: pd.Series,
                         dni_extra: pd.Series, airmass: pd.Series,
                         albedo: float = 0.25) -> pd.Series:
    """Total tilted-surface irradiance — pvlib ``irradiance.get_total_irradiance``
    verbatim with its default isotropic sky-diffuse model (the exact call the
    RC model has always made; pvlib defaults to model='isotropic')."""
    zen = solar_zenith.to_numpy()
    sun_az = solar_azimuth.to_numpy()
    dni_n = dni.to_numpy()
    ghi_n = ghi.to_numpy()
    dhi_n = dhi.to_numpy()

    # ---- beam (poa_components: aoi -> cos -> direct) -----------------------
    proj = _aoi_projection(surface_tilt, surface_azimuth, zen, sun_az)
    poa_direct = np.maximum(dni_n * proj, 0.0)

    # ---- sky diffuse (pvlib.irradiance.isotropic verbatim) -----------------
    poa_sky_diffuse = dhi_n * (1 + np.cos(np.radians(surface_tilt))) * 0.5

    # ---- ground reflection (get_ground_diffuse verbatim) -------------------
    poa_ground = ghi_n * albedo * (1 - np.cos(np.radians(surface_tilt))) * 0.5

    poa_global = poa_direct + poa_sky_diffuse + poa_ground
    return pd.Series(poa_global, index=solar_zenith.index)
