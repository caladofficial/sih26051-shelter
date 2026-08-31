"""Storage layer — SQLite (local/dev) or Supabase Postgres (cloud/prod).

The rest of the project talks to `Store` only, never to a database directly.
Backend is chosen automatically:
    * SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY set  -> Supabase Postgres
    * otherwise                                     -> local SQLite file

SQLite schema mirrors supabase/migrations/0001_init.sql so the same code
works in both worlds. On serverless (Vercel) the filesystem is read-only, so
SQLite writes degrade gracefully (writes are skipped, reads return None).
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS locations (
  location_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  latitude REAL NOT NULL,
  longitude REAL NOT NULL,
  elevation_m REAL NOT NULL DEFAULT 0,
  timezone TEXT NOT NULL DEFAULT 'UTC',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS materials (
  material TEXT PRIMARY KEY,
  category TEXT,
  k_W_mK REAL NOT NULL,
  density_kg_m3 REAL NOT NULL,
  cp_J_kgK REAL NOT NULL,
  solar_absorptance REAL NOT NULL,
  emissivity REAL NOT NULL,
  thickness_m REAL NOT NULL,
  source TEXT NOT NULL,
  notes TEXT
);
CREATE TABLE IF NOT EXISTS weather (
  location_id TEXT NOT NULL,
  ts_utc TEXT NOT NULL,
  t2m REAL, rh2m REAL, ws10m REAL, wd10m REAL, ps REAL,
  ghi REAL, ghi_clear REAL, precip REAL, t2mdew REAL,
  PRIMARY KEY (location_id, ts_utc)
);
CREATE INDEX IF NOT EXISTS idx_weather_loc_ts ON weather(location_id, ts_utc);
CREATE TABLE IF NOT EXISTS simulations (
  sim_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  location_id TEXT,
  design TEXT NOT NULL,
  engine TEXT NOT NULL DEFAULT 'rc',
  period TEXT NOT NULL,
  metrics TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS simulation_results (
  sim_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  indoor_t_c REAL, outdoor_t_c REAL,
  q_solar_w REAL, q_conduct_w REAL, q_vent_w REAL, q_net_w REAL,
  PRIMARY KEY (sim_id, ts)
);
CREATE TABLE IF NOT EXISTS optimization_runs (
  run_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  location_id TEXT,
  n_trials INTEGER NOT NULL,
  best_tpi REAL,
  best_design TEXT
);
CREATE TABLE IF NOT EXISTS optimization_trials (
  run_id TEXT NOT NULL,
  trial_no INTEGER NOT NULL,
  tpi REAL,
  params TEXT,
  PRIMARY KEY (run_id, trial_no)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _j(value) -> str:
    return json.dumps(value, default=str)


class Store:
    """Unified storage facade: Supabase when configured, else SQLite."""

    def __init__(self, db_path: str | Path | None = None):
        self.supabase_url = os.environ.get("SUPABASE_URL", "").strip()
        self.supabase_key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or
                             os.environ.get("SUPABASE_ANON_KEY", "")).strip()
        self._client = None
        self._conn = None
        if self.supabase_url and self.supabase_key:
            from supabase import create_client
            self._client = create_client(self.supabase_url, self.supabase_key)
            self.backend = "supabase"
        else:
            path = Path(db_path) if db_path else \
                Path(__file__).resolve().parents[2] / "data" / "processed" / "app.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False)
            self._conn.executescript(SCHEMA_SQLITE)
            self._conn.commit()
            self.backend = "sqlite"

    # ------------------------------------------------------------- locations
    def upsert_location(self, location: dict) -> None:
        row = {
            "location_id": location.get("location_id") or
                           f"{location['name'].lower().replace(' ', '_')}",
            "name": location["name"],
            "latitude": float(location["latitude"]),
            "longitude": float(location["longitude"]),
            "elevation_m": float(location.get("elevation_m", 0)),
            "timezone": location.get("timezone", "UTC"),
            "created_at": _now(),
        }
        if self._client:
            self._client.table("locations").upsert(row,
                on_conflict="location_id").execute()
        else:
            self._conn.execute(
                """INSERT OR REPLACE INTO locations
                   (location_id, name, latitude, longitude, elevation_m,
                    timezone, created_at)
                   VALUES (:location_id, :name, :latitude, :longitude,
                           :elevation_m, :timezone, :created_at)""", row)
            self._conn.commit()

    def list_locations(self) -> list[dict]:
        if self._client:
            res = self._client.table("locations").select("*").order("name").execute()
            return [dict(r) for r in res.data]
        rows = self._conn.execute("SELECT * FROM locations ORDER BY name").fetchall()
        cols = [c[0] for c in self._conn.execute("SELECT * FROM locations").description]
        return [dict(zip(cols, r)) for r in rows]

    # ------------------------------------------------------------- materials
    def upsert_materials(self, materials: pd.DataFrame) -> int:
        rows = []
        for name, r in materials.reset_index().iterrows():
            rows.append({
                "material": r["material"],
                "category": r.get("category", ""),
                "k_W_mK": float(r["k_W_mK"]),
                "density_kg_m3": float(r["density_kg_m3"]),
                "cp_J_kgK": float(r["cp_J_kgK"]),
                "solar_absorptance": float(r["solar_absorptance"]),
                "emissivity": float(r["emissivity"]),
                "thickness_m": float(r["thickness_m"]),
                "source": r["source"],
                "notes": r.get("notes", ""),
            })
        if self._client:
            for i in range(0, len(rows), 100):
                self._client.table("materials").upsert(rows[i:i + 100],
                    on_conflict="material").execute()
        else:
            self._conn.executemany(
                """INSERT OR REPLACE INTO materials
                   (material, category, k_W_mK, density_kg_m3, cp_J_kgK,
                    solar_absorptance, emissivity, thickness_m, source, notes)
                   VALUES (:material, :category, :k_W_mK, :density_kg_m3,
                           :cp_J_kgK, :solar_absorptance, :emissivity,
                           :thickness_m, :source, :notes)""", rows)
            self._conn.commit()
        return len(rows)

    def list_materials(self) -> list[dict]:
        if self._client:
            res = self._client.table("materials").select("*").order("material").execute()
            return [dict(r) for r in res.data]
        rows = self._conn.execute("SELECT * FROM materials ORDER BY material").fetchall()
        cols = [c[0] for c in self._conn.execute("SELECT * FROM materials").description]
        return [dict(zip(cols, r)) for r in rows]

    # --------------------------------------------------------------- weather
    def save_weather(self, df: pd.DataFrame, location_id: str) -> int:
        """df: hourly DataFrame indexed by tz-aware UTC timestamps."""
        df = df.copy()
        df["ts_utc"] = df.index.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S+00:00")
        df = df.reset_index(drop=True)
        rows = []
        for _, r in df.iterrows():
            row = {"location_id": location_id, "ts_utc": r["ts_utc"]}
            for col in ("t2m", "rh2m", "ws10m", "wd10m", "ps", "ghi",
                        "ghi_clear", "precip", "t2mdew"):
                v = r.get(col)
                row[col] = None if pd.isna(v) else float(v)
            rows.append(row)
        if self._client:
            for i in range(0, len(rows), 500):
                try:
                    self._client.table("weather").upsert(rows[i:i + 500],
                        on_conflict="location_id,ts_utc").execute()
                except Exception as exc:          # serverless safety net
                    print(f"[store] weather upsert skipped: {exc}")
                    return 0
        else:
            try:
                self._conn.executemany(
                    """INSERT OR REPLACE INTO weather
                       (location_id, ts_utc, t2m, rh2m, ws10m, wd10m, ps,
                        ghi, ghi_clear, precip, t2mdew)
                       VALUES (:location_id, :ts_utc, :t2m, :rh2m, :ws10m,
                               :wd10m, :ps, :ghi, :ghi_clear, :precip,
                               :t2mdew)""", rows)
                self._conn.commit()
            except sqlite3.OperationalError:
                return 0
        return len(rows)

    def load_weather(self, location_id: str, start_utc: str,
                     end_utc: str) -> pd.DataFrame | None:
        """Return hourly weather for the period as a UTC-indexed DataFrame."""
        if self._client:
            res = self._client.table("weather") \
                .select("*").eq("location_id", location_id) \
                .gte("ts_utc", start_utc).lte("ts_utc", end_utc) \
                .order("ts_utc").execute()
            rows = res.data
        else:
            rows = self._conn.execute(
                """SELECT * FROM weather WHERE location_id = ?
                   AND ts_utc >= ? AND ts_utc <= ? ORDER BY ts_utc""",
                (location_id, start_utc, end_utc)).fetchall()
            cols = [c[0] for c in self._conn.execute("SELECT * FROM weather").description]
            rows = [dict(zip(cols, r)) for r in rows]
        if not rows:
            return None
        df = pd.DataFrame(rows).set_index("ts_utc")
        df.index = pd.to_datetime(df.index, utc=True)
        for col in ("t2m", "rh2m", "ws10m", "wd10m", "ps", "ghi",
                    "ghi_clear", "precip", "t2mdew"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    # ----------------------------------------------------------- simulations
    def save_simulation(self, sim_id: str, location_id: str, design: dict,
                        period: str, metrics: dict, results: pd.DataFrame) -> None:
        if self._client:
            try:
                self._client.table("simulations").upsert({
                    "sim_id": sim_id, "created_at": _now(),
                    "location_id": location_id, "design": _j(design),
                    "engine": "rc", "period": period, "metrics": _j(metrics),
                }, on_conflict="sim_id").execute()
                if results is not None and not results.empty:
                    rows = []
                    r = results.copy()
                    r["ts"] = r.index.strftime("%Y-%m-%dT%H:%M:%S%z")
                    for _, x in r.iterrows():
                        rows.append({
                            "sim_id": sim_id, "ts": x["ts"],
                            "indoor_t_c": _f(x.get("indoor_t_c")),
                            "outdoor_t_c": _f(x.get("outdoor_t_c")),
                            "q_solar_w": _f(x.get("q_solar_w")),
                            "q_conduct_w": _f(x.get("q_conduct_w")),
                            "q_vent_w": _f(x.get("q_vent_w")),
                            "q_net_w": _f(x.get("q_net_w")),
                        })
                    for i in range(0, len(rows), 500):
                        self._client.table("simulation_results").upsert(
                            rows[i:i + 500], on_conflict="sim_id,ts").execute()
            except Exception as exc:
                print(f"[store] simulation save skipped: {exc}")
        else:
            try:
                self._conn.execute(
                    """INSERT OR REPLACE INTO simulations
                       (sim_id, created_at, location_id, design, engine,
                        period, metrics) VALUES (?,?,?,?,?,?,?)""",
                    (sim_id, _now(), location_id, _j(design), "rc",
                     period, _j(metrics)))
                if results is not None and not results.empty:
                    rows = []
                    for ts, x in results.iterrows():
                        rows.append((sim_id, ts.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                     _f(x.get("indoor_t_c")),
                                     _f(x.get("outdoor_t_c")),
                                     _f(x.get("q_solar_w")),
                                     _f(x.get("q_conduct_w")),
                                     _f(x.get("q_vent_w")),
                                     _f(x.get("q_net_w"))))
                    self._conn.executemany(
                        """INSERT OR REPLACE INTO simulation_results
                           (sim_id, ts, indoor_t_c, outdoor_t_c, q_solar_w,
                            q_conduct_w, q_vent_w, q_net_w)
                           VALUES (?,?,?,?,?,?,?,?)""", rows)
                self._conn.commit()
            except sqlite3.OperationalError:
                pass

    def list_simulations(self, limit: int = 10) -> list[dict]:
        if self._client:
            res = self._client.table("simulations").select("*") \
                .order("created_at", desc=True).limit(limit).execute()
            out = []
            for r in res.data:
                r["design"] = json.loads(r["design"]) if r.get("design") else {}
                r["metrics"] = json.loads(r["metrics"]) if r.get("metrics") else {}
                out.append(r)
            return out
        rows = self._conn.execute(
            "SELECT * FROM simulations ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
        cols = [c[0] for c in self._conn.execute("SELECT * FROM simulations").description]
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            d["design"] = json.loads(d["design"])
            d["metrics"] = json.loads(d["metrics"])
            out.append(d)
        return out

    # ----------------------------------------------------------- optimization
    def save_optimization(self, run_id: str, location_id: str, n_trials: int,
                          best_tpi: float, best_design: dict,
                          trials: list[dict]) -> None:
        if self._client:
            try:
                self._client.table("optimization_runs").upsert({
                    "run_id": run_id, "created_at": _now(),
                    "location_id": location_id, "n_trials": n_trials,
                    "best_tpi": float(best_tpi), "best_design": _j(best_design),
                }, on_conflict="run_id").execute()
                rows = [{"run_id": run_id, "trial_no": int(t["trial_no"]),
                         "tpi": float(t["tpi"]), "params": _j(t["params"])}
                        for t in trials]
                for i in range(0, len(rows), 200):
                    self._client.table("optimization_trials").upsert(
                        rows[i:i + 200], on_conflict="run_id,trial_no").execute()
            except Exception as exc:
                print(f"[store] optimization save skipped: {exc}")
        else:
            try:
                self._conn.execute(
                    """INSERT OR REPLACE INTO optimization_runs
                       (run_id, created_at, location_id, n_trials, best_tpi,
                        best_design) VALUES (?,?,?,?,?,?)""",
                    (run_id, _now(), location_id, n_trials, float(best_tpi),
                     _j(best_design)))
                self._conn.executemany(
                    """INSERT OR REPLACE INTO optimization_trials
                       (run_id, trial_no, tpi, params) VALUES (?,?,?,?)""",
                    [(run_id, int(t["trial_no"]), float(t["tpi"]),
                      _j(t["params"])) for t in trials])
                self._conn.commit()
            except sqlite3.OperationalError:
                pass

    def list_optimizations(self, limit: int = 5) -> list[dict]:
        if self._client:
            res = self._client.table("optimization_runs").select("*") \
                .order("created_at", desc=True).limit(limit).execute()
            out = []
            for r in res.data:
                r["best_design"] = json.loads(r["best_design"]) if r.get("best_design") else {}
                out.append(r)
            return out
        rows = self._conn.execute(
            "SELECT * FROM optimization_runs ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
        cols = [c[0] for c in self._conn.execute("SELECT * FROM optimization_runs").description]
        return [dict(zip(cols, r)) for r in rows]


def _f(v) -> float | None:
    if v is None or (isinstance(v, float) and v != v):   # NaN
        return None
    return float(v)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"
