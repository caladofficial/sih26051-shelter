"""Open-Meteo historical weather — SECOND, cross-check API layer.

Open-Meteo's archive API (ERA5-based reanalysis, free tier, no API key)
goes back to 1940. It is the easy application API; NASA POWER remains the
authoritative project dataset. We use Open-Meteo to CROSS-CHECK the climate
inputs, not to average them blindly with POWER.

Docs: https://open-meteo.com/en/docs/historical-weather-api
"""
import pandas as pd
import requests

OM_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"

# Open-Meteo variable -> standardised column name (same as POWER mapping)
VAR_MAP = {
    "temperature_2m": "t2m",
    "relative_humidity_2m": "rh2m",
    "dew_point_2m": "t2mdew",
    "precipitation": "precip",
    "wind_speed_10m": "ws10m",
    "wind_direction_10m": "wd10m",
    "surface_pressure": "ps",
    "shortwave_radiation": "ghi",       # hourly mean shortwave (W/m2)
}


def fetch_hourly(lat: float, lon: float, start_date: str, end_date: str,
                 variables: list[str] | None = None,
                 timezone: str = "UTC") -> pd.DataFrame:
    """Fetch Open-Meteo historical hourly data (start/end as YYYY-MM-DD).

    Returns a DataFrame with standardised column names on a UTC index.
    """
    vars_ = variables or list(VAR_MAP)
    qs = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(vars_),
        "timezone": timezone,
    }
    r = requests.get(OM_ARCHIVE, params=qs, timeout=180)
    r.raise_for_status()
    j = r.json()
    hourly = j.get("hourly")
    if not hourly:
        raise ValueError(f"Open-Meteo returned no data: {j.get('reason', j)}")
    idx = pd.to_datetime(hourly["time"], utc=(timezone == "UTC"))
    df = pd.DataFrame(index=idx)
    for v in vars_:
        df[VAR_MAP[v]] = hourly[v]
    df.index.name = "timestamp_utc"
    return df
