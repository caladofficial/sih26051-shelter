"""NASA POWER hourly data ingestion — PRIMARY climate source for SIH26051.

NASA POWER (Prediction Of Worldwide Energy Resources) provides free, globally
available solar + meteorological data for energy/building applications.
The hourly temporal API returns CSV / JSON / NetCDF / EPW.

Endpoint: https://power.larc.nasa.gov/api/temporal/hourly/point
Docs:     https://power.larc.nasa.gov/docs/
"""
from pathlib import Path

import pandas as pd
import requests

POWER_BASE = "https://power.larc.nasa.gov/api/temporal/hourly/point"

# POWER parameter code -> standardised column name used across the whole pipeline
PARAM_MAP = {
    "T2M": "t2m",                       # air temperature @2m (degC)
    "RH2M": "rh2m",                     # relative humidity @2m (%)
    "WS10M": "ws10m",                   # wind speed @10m (m/s)
    "WD10M": "wd10m",                   # wind direction @10m (deg from N)
    "PS": "ps",                         # surface pressure (Pa)
    "ALLSKY_SFC_SW_DWN": "ghi",         # all-sky GHI (W/m2)
    "CLRSKY_SFC_SW_DWN": "ghi_clear",   # clear-sky GHI (W/m2)
    "PRECTOTCORR": "precip",            # precipitation (mm/h)
    "T2MDEW": "t2mdew",                 # dew-point temperature (degC)
}

# parameters used when requesting a full EPW weather file
EPW_PARAMETERS = [
    "ALLSKY_SFC_SW_DWN", "CLRSKY_SFC_SW_DWN", "T2M", "T2MDEW",
    "RH2M", "PS", "WS10M", "WD10M", "PRECTOTCORR",
]


def fetch_hourly(lat: float, lon: float, start: str, end: str,
                 parameters: list[str] | None = None,
                 community: str = "RE", fmt: str = "JSON") -> requests.Response:
    """Request hourly POWER data.

    start/end are inclusive 'YYYYMMDD' strings (POWER API convention).
    Returns the raw HTTP response; use hourly_to_dataframe() to parse JSON
    or .text for CSV/EPW formats.
    """
    params = parameters or list(PARAM_MAP)
    qs = {
        "parameters": ",".join(params),
        "community": community,
        "longitude": lon,
        "latitude": lat,
        "start": start,
        "end": end,
        "format": fmt,
    }
    r = requests.get(POWER_BASE, params=qs, timeout=180)
    r.raise_for_status()
    return r


def hourly_to_dataframe(resp: requests.Response) -> pd.DataFrame:
    """Parse a POWER hourly JSON response into a clean UTC hourly DataFrame.

    POWER API v2.9.9+ returns each parameter as a dict keyed by 'YYYYMMDDHH'
    (e.g. {"2024010100": 14.19, ...}) in LOCAL STANDARD TIME
    (LST = UTC + longitude/15). Missing hours are the fill value -999.0.
    This parser converts everything to UTC and drops fill values.
    """
    data = resp.json()
    geometry = data.get("geometry", {}).get("coordinates", [None, None])
    lon = geometry[0] if geometry else None
    # v2.9.9: hourly data lives under properties.parameter; "parameters" only
    # carries units/longname metadata
    params = (data.get("properties") or {}).get("parameter") or data.get("parameters")
    if not params:
        raise ValueError("No parameter data in POWER response "
                         f"(status {resp.status_code})")

    series = {}
    for code, meta in params.items():
        if not isinstance(meta, dict):
            continue
        col = PARAM_MAP.get(code, code.lower())
        if "index" in meta:                      # legacy array format
            idx = pd.to_datetime(meta["index"], format="%Y%m%d%H", utc=True)
            s = pd.Series(meta["data"], index=idx, dtype=float)
        else:                                    # v2.9.9 dict format (LST)
            stamps = list(meta.keys())
            if not stamps or not str(stamps[0])[:8].isdigit():
                continue
            idx = pd.to_datetime(stamps, format="%Y%m%d%H")
            s = pd.Series(list(meta.values()), index=idx, dtype=float)
        if code == "PS":
            s = s * 10.0    # POWER reports PS in kPa -> hPa to match Open-Meteo/EPW
        series[col] = s

    if not series:
        raise ValueError("POWER response contained no parseable hourly series")
    df = pd.concat(series, axis=1).sort_index()
    df = df.replace(-999.0, pd.NA)               # POWER fill value
    if lon is not None:
        # timestamps are Local Standard Time: LST = UTC + longitude/15
        df.index = df.index.tz_localize("UTC") - pd.Timedelta(hours=lon / 15.0)
    df.index = df.index.tz_convert("UTC")
    # POWER's LST convention gives fractional-hour offsets (e.g. +5h27m at
    # 81.85E); snap to the civil hour grid so POWER aligns with other sources
    df.index = df.index.round("h")
    df = df[~df.index.duplicated(keep="first")]
    df.index.name = "timestamp_utc"
    return df


def clean_epw(text: str) -> str:
    """Fix known NASA POWER EPW export issues so EnergyPlus 26 can run it.

    1. The '?9?9?9?9E0?9?9...' token POWER puts in every data row is the
       STANDARD EPW missing-data flag (field 6 of a 35-field row, same as in
       NREL TMY files) — it must be KEPT.
    2. Leap-day records (Feb 29) must be dropped: EnergyPlus 26 cannot
       process them ("Feb29 data encountered but will not be processed" then
       aborts). Each EPW row carries its own date, so dropping the 24 Feb-29
       records keeps every other date aligned (365-day design year).
    3. POWER omits the final '12/31 24:00' record (8759 rows); EnergyPlus 26
       requires exactly 8760 rows for a 365-day year and aborts otherwise.
       Append a clone of the last record with hour=24.
    """
    out = []
    for line in text.splitlines():
        if line.startswith("HOLIDAYS"):
            out.append("HOLIDAYS/DAYLIGHT SAVINGS,Yes,0,0,0")
            continue
        if line[:4].isdigit() and "," in line:      # weather data rows
            parts = line.split(",")
            if len(parts) >= 3 and parts[1] == "2" and parts[2] == "29":
                continue                             # drop leap-day record
        out.append(line)
    data = [l for l in out if l[:4].isdigit() and "," in l]
    if len(data) == 8759:                            # missing 12/31 24:00
        last = data[-1].split(",")
        if len(last) >= 4 and last[2] == "31" and last[3] == "23":
            clone = list(last)
            clone[3] = "24"
            out.append(",".join(clone))
    return "\n".join(out) + "\n"


def fetch_epw(lat: float, lon: float, year: int,
              out_path: str | Path | None = None) -> str:
    """Fetch an EnergyPlus EPW weather file directly from POWER.

    The POWER API can produce EPW output for its Sustainable Buildings (SB)
    user community — this is the cleanest path into EnergyPlus. The response
    is passed through clean_epw() to fix POWER's field-shift export bug.
    """
    qs = {
        "parameters": ",".join(EPW_PARAMETERS),
        "community": "SB",               # Sustainable Buildings community
        "longitude": lon,
        "latitude": lat,
        "start": f"{year}0101",
        "end": f"{year}1231",
        "format": "EPW",
    }
    r = requests.get(POWER_BASE, params=qs, timeout=300)
    r.raise_for_status()
    text = clean_epw(r.text)
    if not text.strip().startswith("LOCATION"):
        raise ValueError("POWER did not return a valid EPW file "
                         f"(first line: {text[:80]!r})")
    if out_path is not None:
        Path(out_path).write_text(text, encoding="utf-8")
    return text
