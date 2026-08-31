"""EnergyPlus runner — discovery, execution, result parsing.

EnergyPlus is the main simulation engine (BSD open-source, NREL). This module:
    * discovers the energyplus executable (PATH, ENERGYPLUS_DIR, common dirs)
    * runs: energyplus -w <weather.epw> -d <workdir> <model.idf>
    * parses eplusout.csv (falls back to eplusout.sql) into a clean DataFrame
"""
import os
import shutil
import subprocess
from pathlib import Path

import pandas as pd


def discover_energyplus(exe_override: str = "") -> str | None:
    """Locate the energyplus executable (Linux or Windows)."""
    if exe_override:
        return exe_override if Path(exe_override).exists() else None
    # environment variables
    for var in ("ENERGYPLUS_EXE", "ENERGYPLUS_DIR"):
        v = os.environ.get(var)
        if v:
            cand = Path(v) / "energyplus" if Path(v).is_dir() else Path(v)
            if cand.exists():
                return str(cand)
    # PATH
    found = shutil.which("energyplus")
    if found:
        return found
    # common install locations
    candidates = []
    home = Path.home()
    candidates += sorted(home.glob("EnergyPlusV*/energyplus"))  # Windows
    candidates += sorted(Path("/usr/local/energyplus").glob("EnergyPlus-*/energyplus"))
    candidates += sorted(Path("/opt/EnergyPlusV*").glob("energyplus"))
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def run_energyplus(idf_path: str | Path, epw_path: str | Path,
                   workdir: str | Path, exe: str | None = None,
                   year: int = 2024) -> dict:
    """Run EnergyPlus on the given IDF + EPW; returns parsed results.

    workdir receives eplusout.csv / eplusout.sql / eplusout.err / logs.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    exe = exe or discover_energyplus()
    if exe is None:
        raise FileNotFoundError(
            "energyplus executable not found. Set ENERGYPLUS_DIR or install "
            "EnergyPlus (see docs/windows_setup.md).")
    print(f"[eplus] executable: {exe}")
    print(f"[eplus] idf: {idf_path}   epw: {epw_path}")
    cmd = [exe, "-w", str(epw_path), "-d", str(workdir), str(idf_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.stdout:
        print(proc.stdout[-2000:])
    if proc.stderr:
        print("stderr:", proc.stderr[-2000:])
    if proc.returncode != 0:
        err = (workdir / "eplusout.err")
        tail = err.read_text(encoding="utf-8", errors="replace")[-3000:] if err.exists() else ""
        raise RuntimeError(f"EnergyPlus failed (rc={proc.returncode})\n{tail}")

    df = _parse_csv(workdir / "eplusout.csv", year=year)
    if df is None:
        df = _parse_sql(workdir / "eplusout.sql")
    if df is None:
        raise RuntimeError("No eplusout.csv/sql results produced; check eplusout.err")
    return {"results": df, "workdir": workdir, "exe": exe}


def _parse_csv(path: Path, year: int = 2024) -> pd.DataFrame | None:
    """Parse eplusout.csv. Index 'MM/DD HH:MM' -> datetime (weather year)."""
    if not path.exists():
        return None
    df = pd.read_csv(path)
    if "Date/Time" not in df.columns:
        return None
    dt = pd.to_datetime(df["Date/Time"], format="%m/%d %H:%M:%S", errors="coerce")
    # EnergyPlus CSVs carry no year — patch with the weather-file year
    dt = pd.to_datetime({"year": year, "month": dt.dt.month, "day": dt.dt.day,
                         "hour": dt.dt.hour, "minute": dt.dt.minute})
    df.index = dt
    df = df.drop(columns=["Date/Time"])
    df = df.apply(pd.to_numeric, errors="coerce")
    return df


def _parse_sql(path: Path) -> pd.DataFrame | None:
    """Read eplusout.sql (SQLite) — E+ 26 schema: ReportData joins to
    ReportDataDictionary (variable names) and Time (timestamps) by index."""
    if not path.exists():
        return None
    import sqlite3
    con = sqlite3.connect(path)
    try:
        dct = pd.read_sql("SELECT * FROM ReportDataDictionary", con)
        times = pd.read_sql("SELECT * FROM Time", con)
        data = pd.read_sql("SELECT * FROM ReportData", con)
    finally:
        con.close()
    if data.empty or dct.empty or times.empty:
        return None
    dct = dct.set_index("ReportDataDictionaryIndex")
    times = times.set_index("TimeIndex")
    dt = pd.to_datetime(dict(year=times["Year"], month=times["Month"],
                             day=times["Day"], hour=times["Hour"],
                             minute=times["Minute"]))
    wide = data.pivot(index="TimeIndex", columns="ReportDataDictionaryIndex",
                      values="Value")
    out = pd.DataFrame(index=dt)
    for col in wide.columns:
        row = dct.loc[col]
        label = f"{row['Name']} [{row['Units']}]"
        out[label] = wide[col].to_numpy()
    return out


def select(df: pd.DataFrame, *substrings: str) -> pd.Series:
    """Pick the first column matching all given substrings (case-insensitive)."""
    for col in df.columns:
        low = col.lower()
        if all(s.lower() in low for s in substrings):
            return df[col]
    raise KeyError(f"No column matching {substrings} in {list(df.columns)[:12]}...")
