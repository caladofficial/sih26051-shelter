"""Storage layer — SQLite (local/dev) or Supabase Postgres (cloud/prod).

The rest of the project talks to `Store` only, never to a database directly.
Backend is chosen automatically:
    * SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY set  -> Supabase Postgres
    * otherwise                                     -> local SQLite file

SQLite schema mirrors supabase/migrations/0001_init.sql so the same code
works in both worlds. On serverless (Vercel) the filesystem is read-only, so
SQLite writes degrade gracefully (writes are skipped, reads return None).

Supabase is accessed through its PostgREST REST API with plain `requests`
(no supabase-py SDK) so the serverless bundle stays small.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

try:                                  # auto-load .env when present (local runs)
    from dotenv import load_dotenv
    # tests/conftest.py sets DOTENV_DISABLED so a developer's .env can't
    # silently point the test suite at the production Supabase project
    if os.environ.get("DOTENV_DISABLED") != "1":
        load_dotenv()
except ImportError:
    pass

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
CREATE TABLE IF NOT EXISTS designs (
  design_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  design TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  favorite INTEGER NOT NULL DEFAULT 0,
  user_id TEXT
);
CREATE TABLE IF NOT EXISTS sih_users (
  user_id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  pass_hash TEXT NOT NULL,
  salt TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shelters (
  shelter_id TEXT PRIMARY KEY,
  user_id TEXT,
  name TEXT NOT NULL,
  location_name TEXT NOT NULL DEFAULT '',
  latitude REAL,
  longitude REAL,
  design TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'planned',
  deployed_at TEXT,
  notes TEXT NOT NULL DEFAULT '',
  metrics TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cad_imports (
  import_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  filename TEXT NOT NULL,
  format TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'upload',
  bbox TEXT,
  dimensions TEXT,
  entity_counts TEXT
);

CREATE TABLE IF NOT EXISTS optimization_trials (
  run_id TEXT NOT NULL,
  trial_no INTEGER NOT NULL,
  tpi REAL,
  params TEXT,
  PRIMARY KEY (run_id, trial_no)
);

CREATE TABLE IF NOT EXISTS nlp_feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  text TEXT NOT NULL,
  intent TEXT,
  confidence REAL,
  slots TEXT,
  design TEXT,
  correct INTEGER NOT NULL,
  correction TEXT
);
"""


def _maybe_json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _j(value) -> str:
    return json.dumps(value, default=str)


class Store:
    """Unified storage facade: Supabase when configured, else SQLite."""

    def __init__(self, db_path: str | Path | None = None):
        self.supabase_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        self.supabase_key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or
                             os.environ.get("SUPABASE_ANON_KEY", "")).strip()
        self._rest = None
        self._conn = None
        if self.supabase_url and self.supabase_key:
            self._rest = f"{self.supabase_url}/rest/v1"
            self._headers = {
                "apikey": self.supabase_key,
                "Authorization": f"Bearer {self.supabase_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            self.backend = "supabase"
        else:
            path = Path(db_path) if db_path else \
                Path(__file__).resolve().parents[2] / "data" / "processed" / "app.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False)
            self._conn.executescript(SCHEMA_SQLITE)
            self._conn.commit()
            self.backend = "sqlite"
            self._seed_materials()

    def _seed_materials(self) -> None:
        """Populate an empty local materials table from the canonical CSV.

        Supabase ships seeded via migration 0002; a fresh SQLite file did not,
        so anything reading materials through the Store (the CAD assembly
        tests, /api/materials) silently saw an empty table and computed zero
        mass. The CSV is the same source the migration was generated from.
        """
        try:
            n = self._conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
            if n:
                return
            csv_path = (Path(__file__).resolve().parents[2]
                        / "data" / "external" / "materials.csv")
            if not csv_path.exists():
                return
            df = pd.read_csv(csv_path, comment="#")
            df.columns = [c.strip() for c in df.columns]
            rows = []
            for r in df.to_dict("records"):
                rows.append({
                    "material": r.get("material"),
                    "category": r.get("category"),
                    "k_W_mK": r.get("k_W_mK"),
                    "density_kg_m3": r.get("density_kg_m3"),
                    "cp_J_kgK": r.get("cp_J_kgK"),
                    "solar_absorptance": r.get("solar_absorptance"),
                    "emissivity": r.get("emissivity"),
                    "thickness_m": r.get("thickness_m", 0) or 0,
                    "source": r.get("source", "materials.csv"),
                    "notes": r.get("notes", ""),
                })
            self._conn.executemany(
                """INSERT OR REPLACE INTO materials
                   (material, category, k_W_mK, density_kg_m3, cp_J_kgK,
                    solar_absorptance, emissivity, thickness_m, source, notes)
                   VALUES (:material, :category, :k_W_mK, :density_kg_m3,
                           :cp_J_kgK, :solar_absorptance, :emissivity,
                           :thickness_m, :source, :notes)""", rows)
            self._conn.commit()
        except Exception:                                    # noqa: BLE001
            pass                                             # never block startup

    # ---------------------------------------------------- PostgREST helpers
    def _pg(self, method: str, table: str, params: dict | None = None,
            body=None, prefer: str | None = None,
            range_: tuple[int, int] | None = None) -> list[dict] | None:
        """Call PostgREST; returns the JSON row list, [] on empty success,
        or None when the request failed."""
        headers = dict(self._headers)
        if prefer:
            headers["Prefer"] = prefer
        if range_:
            headers["Range"] = f"{range_[0]}-{range_[1]}"
        try:
            r = requests.request(method, f"{self._rest}/{table}",
                                 headers=headers, params=params,
                                 json=body, timeout=60)
            r.raise_for_status()
        except requests.RequestException as exc:
            print(f"[store] PostgREST {method} {table} failed: {exc}")
            return None
        if not r.content:
            return []
        return r.json()

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
        if self._rest:
            self._pg("POST", "locations", params={"on_conflict": "location_id"},
                     body=row, prefer="resolution=merge-duplicates")
        else:
            self._conn.execute(
                """INSERT OR REPLACE INTO locations
                   (location_id, name, latitude, longitude, elevation_m,
                    timezone, created_at)
                   VALUES (:location_id, :name, :latitude, :longitude,
                           :elevation_m, :timezone, :created_at)""", row)
            self._conn.commit()

    def list_locations(self) -> list[dict]:
        if self._rest:
            return self._pg("GET", "locations",
                            params={"select": "*", "order": "name.asc"})
        rows = self._conn.execute("SELECT * FROM locations ORDER BY name").fetchall()
        cols = [c[0] for c in self._conn.execute("SELECT * FROM locations").description]
        return [dict(zip(cols, r)) for r in rows]

    # ------------------------------------------------------------- materials
    def upsert_materials(self, materials: pd.DataFrame) -> int:
        # NOTE: Postgres folds unquoted DDL identifiers to lowercase
        # (cp_J_kgK -> cp_j_kgk, k_W_mK -> k_w_mk); use those keys with
        # PostgREST. SQLite is case-insensitive, so the same rows work there.
        rows = []
        for _, r in materials.reset_index().iterrows():
            rows.append({
                "material": r["material"],
                "category": r.get("category", ""),
                "k_w_mk": float(r["k_W_mK"]),
                "density_kg_m3": float(r["density_kg_m3"]),
                "cp_j_kgk": float(r["cp_J_kgK"]),
                "solar_absorptance": float(r["solar_absorptance"]),
                "emissivity": float(r["emissivity"]),
                "thickness_m": float(r["thickness_m"]),
                "source": r["source"],
                "notes": r.get("notes", ""),
            })
        if self._rest:
            for i in range(0, len(rows), 100):
                self._pg("POST", "materials", params={"on_conflict": "material"},
                         body=rows[i:i + 100],
                         prefer="resolution=merge-duplicates")
        else:
            self._conn.executemany(
                """INSERT OR REPLACE INTO materials
                   (material, category, k_W_mK, density_kg_m3, cp_J_kgK,
                    solar_absorptance, emissivity, thickness_m, source, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [tuple(r[k] for k in
                       ("material", "category", "k_w_mk", "density_kg_m3",
                        "cp_j_kgk", "solar_absorptance", "emissivity",
                        "thickness_m", "source", "notes")) for r in rows])
            self._conn.commit()
        return len(rows)

    def list_materials(self) -> list[dict]:
        if self._rest:
            return self._pg("GET", "materials",
                            params={"select": "*", "order": "material.asc"})
        rows = self._conn.execute("SELECT * FROM materials ORDER BY material").fetchall()
        # Postgres folds unquoted identifiers to lowercase, so PostgREST
        # returns k_w_mk / cp_j_kgk. SQLite preserves the declared casing —
        # fold it here so callers see ONE shape whichever backend is active
        # (src.api_app._canon_materials maps them back to k_W_mK / cp_J_kgK).
        cols = [c[0].lower()
                for c in self._conn.execute("SELECT * FROM materials").description]
        return [dict(zip(cols, r)) for r in rows]

    # --------------------------------------------------------------- weather
    def save_weather(self, df: pd.DataFrame, location_id: str,
                     source: str | None = None,
                     data_status: str | None = None) -> int:
        """df: hourly DataFrame indexed by tz-aware UTC timestamps.

        `source` records which service produced the rows ('nasa_power' or
        'open-meteo-archive'). It matters: a year-over-year comparison that
        silently mixes sources would show a source bias as climate change.
        Columns exist only after migration 0006, so they are sent only when
        explicitly requested and dropped if the server rejects them.
        """
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
            if source:
                row["source"] = source
            if data_status:
                row["data_status"] = data_status
            rows.append(row)
        if self._rest:
            # Batch size matters far more than it looks: writing a year of
            # hourly weather in 500-row chunks is 18 round trips to Supabase,
            # which from a Vercel function in another region dominated the
            # whole request (7.3 s of a ~15 s first-visit response). 5000-row
            # chunks do it in two (2.0 s) with a payload PostgREST accepts
            # comfortably.
            batch_size = int(os.getenv("SUPABASE_WRITE_BATCH", "5000"))
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                written = self._pg(
                    "POST", "weather",
                    params={"on_conflict": "location_id,ts_utc"},
                    body=batch,
                    prefer="resolution=merge-duplicates")
                if written is None and (source or data_status):
                    # pre-0006 database: retry without the provenance columns
                    plain = [{k: v for k, v in r.items()
                              if k not in ("source", "data_status")}
                             for r in batch]
                    written = self._pg(
                        "POST", "weather",
                        params={"on_conflict": "location_id,ts_utc"},
                        body=plain, prefer="resolution=merge-duplicates")
                if written is None:           # serverless safety net
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

    def load_weather(self, location_id: str, start_utc: str | None = None,
                     end_utc: str | None = None) -> pd.DataFrame | None:
        """Return hourly weather for the location as a UTC-indexed DataFrame.

        Bounds are optional — the caller usually filters by local year after
        timezone conversion (hourly rows can straddle the UTC year boundary).
        Only the numeric weather columns are selected.
        """
        if self._rest:
            # PostgREST caps reads at its `max_rows` setting (Supabase
            # defaults to 1000; this project is raised to 10000 so a full
            # year of hourly weather arrives in one round trip instead of
            # nine). Page until a short page comes back, so this stays
            # correct whatever the server cap actually is.
            rows = []
            offset = 0
            page_size = int(os.getenv("SUPABASE_PAGE_SIZE", "10000"))
            cols = "ts_utc,t2m,rh2m,ws10m,wd10m,ps,ghi,ghi_clear,precip,t2mdew"
            while True:
                params = {"select": cols, "order": "ts_utc.asc"}
                params["location_id"] = f"eq.{location_id}"
                # Both bounds must go in ONE `and=(...)` group: assigning
                # params["ts_utc"] twice silently dropped the lower bound, so
                # a bounded read returned the whole multi-year series.
                if start_utc and end_utc:
                    params["and"] = f"(ts_utc.gte.{start_utc},ts_utc.lte.{end_utc})"
                elif start_utc:
                    params["ts_utc"] = f"gte.{start_utc}"
                elif end_utc:
                    params["ts_utc"] = f"lte.{end_utc}"
                page = self._pg("GET", "weather", params=params,
                                range_=(offset, offset + page_size - 1))
                rows.extend(page)
                if len(page) < page_size:
                    break
                offset += page_size
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
        if self._rest:
            self._pg("POST", "simulations",
                     params={"on_conflict": "sim_id"},
                     body={"sim_id": sim_id, "created_at": _now(),
                           "location_id": location_id, "design": design,
                           "engine": "rc", "period": period,
                           "metrics": metrics},
                     prefer="resolution=merge-duplicates")
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
                    self._pg("POST", "simulation_results",
                             params={"on_conflict": "sim_id,ts"},
                             body=rows[i:i + 500],
                             prefer="resolution=merge-duplicates")
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
        if self._rest:
            res = self._pg("GET", "simulations",
                           params={"select": "*", "order": "created_at.desc",
                                   "limit": str(limit)})
            out = []
            for r in res:
                r["design"] = _maybe_json(r.get("design")) or {}
                r["metrics"] = _maybe_json(r.get("metrics")) or {}
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
        if self._rest:
            self._pg("POST", "optimization_runs",
                     params={"on_conflict": "run_id"},
                     body={"run_id": run_id, "created_at": _now(),
                           "location_id": location_id, "n_trials": n_trials,
                           "best_tpi": float(best_tpi),
                           "best_design": best_design},
                     prefer="resolution=merge-duplicates")
            rows = [{"run_id": run_id, "trial_no": int(t["trial_no"]),
                     "tpi": float(t["tpi"]), "params": t["params"]}
                    for t in trials]
            for i in range(0, len(rows), 200):
                self._pg("POST", "optimization_trials",
                         params={"on_conflict": "run_id,trial_no"},
                         body=rows[i:i + 200],
                         prefer="resolution=merge-duplicates")
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
        if self._rest:
            res = self._pg("GET", "optimization_runs",
                           params={"select": "*", "order": "created_at.desc",
                                   "limit": str(limit)})
            out = []
            for r in res:
                r["best_design"] = _maybe_json(r.get("best_design")) or {}
                out.append(r)
            return out
        rows = self._conn.execute(
            "SELECT * FROM optimization_runs ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
        cols = [c[0] for c in self._conn.execute("SELECT * FROM optimization_runs").description]
        return [dict(zip(cols, r)) for r in rows]


    # ------------------------------------------------------------ cad imports
    def save_cad_import(self, record: dict) -> None:
        row = {"import_id": record.get("import_id") or new_id("cad"),
               "created_at": _now(), "filename": record.get("filename", ""),
               "format": record.get("format", ""),
               "source": record.get("source", "upload"),
               "bbox": record.get("bbox") or {},
               "dimensions": record.get("dimensions_m") or {},
               "entity_counts": record.get("entity_counts") or {}}
        if self._rest:
            self._pg("POST", "cad_imports",
                     params={"on_conflict": "import_id"},
                     body=row, prefer="resolution=merge-duplicates")
        else:
            try:
                self._conn.execute(
                    """INSERT OR REPLACE INTO cad_imports
                       (import_id, created_at, filename, format, source,
                        bbox, dimensions, entity_counts)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (row["import_id"], row["created_at"], row["filename"],
                     row["format"], row["source"], _j(row["bbox"]),
                     _j(row["dimensions"]), _j(row["entity_counts"])))
                self._conn.commit()
            except sqlite3.OperationalError:
                pass

    # ------------------------------------------------------- nlp feedback
    def save_nlp_feedback(self, record: dict) -> None:
        """One user verdict on a plain-English answer (migration 0007).

        These rows are the real-phrasing corpus used to re-evaluate and
        retrain the assistant — see ml/nlp/import_feedback.py."""
        row = {"created_at": _now(),
               "text": record.get("text", ""),
               "intent": record.get("intent"),
               "confidence": record.get("confidence"),
               "slots": record.get("slots") or {},
               "design": record.get("design") or {},
               "correct": bool(record.get("correct")),
               "correction": record.get("correction")}
        if self._rest:
            self._pg("POST", "nlp_feedback", body=row)
        else:
            try:
                self._conn.execute(
                    """INSERT INTO nlp_feedback
                       (created_at, text, intent, confidence, slots, design,
                        correct, correction) VALUES (?,?,?,?,?,?,?,?)""",
                    (row["created_at"], row["text"], row["intent"],
                     row["confidence"], _j(row["slots"]), _j(row["design"]),
                     1 if row["correct"] else 0, row["correction"]))
                self._conn.commit()
            except sqlite3.OperationalError:
                pass

    def list_nlp_feedback(self, limit: int = 1000) -> list[dict]:
        if self._rest:
            res = self._pg("GET", "nlp_feedback",
                           params={"select": "*", "order": "created_at.desc",
                                   "limit": str(limit)})
            for r in res:
                r["slots"] = _maybe_json(r.get("slots")) or {}
                r["design"] = _maybe_json(r.get("design")) or {}
            return res
        rows = self._conn.execute(
            "SELECT * FROM nlp_feedback ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
        cols = [c[0] for c in
                self._conn.execute("SELECT * FROM nlp_feedback").description]
        out = []
        for r in rows:
            d = dict(zip(cols, r))
            d["correct"] = bool(d["correct"])
            d["slots"] = _maybe_json(d.get("slots")) or {}
            d["design"] = _maybe_json(d.get("design")) or {}
            out.append(d)
        return out

    # ------------------------------------------------------------ designs
    def save_design(self, record: dict, user_id: str | None = None) -> None:
        row = {"design_id": record.get("design_id") or new_id("dsg"),
               "name": record.get("name") or "Design",
               "created_at": record.get("created_at") or _now(),
               "updated_at": _now(),
               "design": record.get("design") or {},
               "notes": record.get("notes", ""),
               "favorite": 1 if record.get("favorite") else 0,
               "user_id": user_id or record.get("user_id")}
        if self._rest:
            self._pg("POST", "designs", params={"on_conflict": "design_id"},
                     body=row, prefer="resolution=merge-duplicates")
        else:
            try:
                self._conn.execute(
                    """INSERT OR REPLACE INTO designs
                       (design_id, name, created_at, updated_at, design,
                        notes, favorite, user_id) VALUES (?,?,?,?,?,?,?,?)""",
                    (row["design_id"], row["name"], row["created_at"],
                     row["updated_at"], _j(row["design"]), row["notes"],
                     row["favorite"], row["user_id"]))
                self._conn.commit()
            except sqlite3.OperationalError:
                pass

    def list_designs(self, limit: int = 100, favorite_only: bool = False,
                     user_id: str | None = None) -> list[dict]:
        if self._rest:
            params = {"select": "*", "order": "updated_at.desc",
                      "limit": max(1, min(limit, 200))}
            if favorite_only:
                params["favorite"] = "eq.true"
            if user_id is None:
                params["user_id"] = "is.null"      # guests see guest designs
            else:
                params["user_id"] = f"eq.{user_id}"
            rows = self._pg("GET", "designs", params=params) or []
            for r in rows:
                r["design"] = _maybe_json(r.get("design")) or {}
            return rows
        try:
            q = ("SELECT design_id, name, created_at, updated_at, design, "
                 "notes, favorite, user_id FROM designs")
            where = ["user_id IS NULL"] if user_id is None else ["user_id = ?"]
            if favorite_only:
                where.append("favorite = 1")
            if where:
                q += " WHERE " + " AND ".join(where)
            q += " ORDER BY updated_at DESC LIMIT ?"
            args: list = [] if user_id is None else [user_id]
            args.append(max(1, min(limit, 200)))
            cur = self._conn.execute(q, tuple(args))
            cols = [c[0] for c in cur.description]
            out = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                if isinstance(d.get("design"), str):
                    try:
                        d["design"] = json.loads(d["design"])
                    except Exception:
                        d["design"] = {}
                out.append(d)
            return out
        except sqlite3.OperationalError:
            return []

    def get_design(self, design_id: str) -> dict | None:
        if self._rest:
            rows = self._pg("GET", "designs",
                            params={"select": "*",
                                    "design_id": f"eq.{design_id}",
                                    "limit": 1}) or []
            if not rows:
                return None
            r = rows[0]
            r["design"] = _maybe_json(r.get("design")) or {}
            return r
        try:
            cur = self._conn.execute(
                # user_id must be selected too: the Supabase branch uses
                # select=* so it comes back there, and the API's ownership
                # check reads it. Omitting it here made every authenticated
                # PATCH/DELETE 404 on the SQLite backend.
                "SELECT design_id, name, created_at, updated_at, design, "
                "notes, favorite, user_id FROM designs WHERE design_id = ?",
                (design_id,))
            row = cur.fetchone()
            if not row:
                return None
            d = dict(zip([c[0] for c in cur.description], row))
            if isinstance(d.get("design"), str):
                try:
                    d["design"] = json.loads(d["design"])
                except Exception:
                    d["design"] = {}
            return d
        except sqlite3.OperationalError:
            return None

    def delete_design(self, design_id: str) -> bool:
        if self._rest:
            rows = self._pg("DELETE", "designs",
                            params={"design_id": f"eq.{design_id}"})
            return rows is not None
        try:
            self._conn.execute("DELETE FROM designs WHERE design_id = ?",
                               (design_id,))
            self._conn.commit()
            return True
        except sqlite3.OperationalError:
            return False

    # ------------------------------------------------------------------ users
    def create_user(self, user_id: str, username: str, pass_hash: str,
                    salt: str) -> None:
        row = {"user_id": user_id, "username": username,
               "pass_hash": pass_hash, "salt": salt, "created_at": _now()}
        if self._rest:
            self._pg("POST", "sih_users", body=row)
        else:
            try:
                self._conn.execute(
                    """INSERT INTO sih_users (user_id, username, pass_hash,
                       salt, created_at) VALUES (?,?,?,?,?)""",
                    (user_id, username, pass_hash, salt, _now()))
                self._conn.commit()
            except sqlite3.IntegrityError:
                raise
            except sqlite3.OperationalError:
                pass

    def get_user_by_username(self, username: str) -> dict | None:
        if self._rest:
            rows = self._pg("GET", "sih_users",
                            params={"select": "*",
                                    "username": f"eq.{username}",
                                    "limit": 1}) or []
            return rows[0] if rows else None
        try:
            cur = self._conn.execute(
                "SELECT * FROM sih_users WHERE username = ?", (username,))
            row = cur.fetchone()
            if not row:
                return None
            return dict(zip([c[0] for c in cur.description], row))
        except sqlite3.OperationalError:
            return None

    def get_user(self, user_id: str) -> dict | None:
        if self._rest:
            rows = self._pg("GET", "sih_users",
                            params={"select": "*",
                                    "user_id": f"eq.{user_id}", "limit": 1}) or []
            return rows[0] if rows else None
        try:
            cur = self._conn.execute(
                "SELECT * FROM sih_users WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            if not row:
                return None
            return dict(zip([c[0] for c in cur.description], row))
        except sqlite3.OperationalError:
            return None

    def delete_user(self, user_id: str) -> None:
        if self._rest:
            self._pg("DELETE", "sih_users", params={"user_id": f"eq.{user_id}"})
        else:
            try:
                self._conn.execute("DELETE FROM sih_users WHERE user_id = ?",
                                   (user_id,))
                self._conn.commit()
            except sqlite3.OperationalError:
                pass

    # -------------------------------------------------------------- shelters
    def save_shelter(self, record: dict) -> None:
        row = {"shelter_id": record.get("shelter_id") or new_id("shl"),
               "user_id": record.get("user_id"),
               "name": record.get("name") or "Shelter",
               "location_name": record.get("location_name", ""),
               "latitude": record.get("latitude"),
               "longitude": record.get("longitude"),
               "design": record.get("design") or {},
               "status": record.get("status") or "planned",
               "deployed_at": record.get("deployed_at"),
               "notes": record.get("notes", ""),
               "metrics": record.get("metrics"),
               "created_at": record.get("created_at") or _now(),
               "updated_at": _now()}
        if self._rest:
            self._pg("POST", "shelters", params={"on_conflict": "shelter_id"},
                     body=row, prefer="resolution=merge-duplicates")
        else:
            try:
                self._conn.execute(
                    """INSERT OR REPLACE INTO shelters
                       (shelter_id, user_id, name, location_name, latitude,
                        longitude, design, status, deployed_at, notes,
                        metrics, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (row["shelter_id"], row["user_id"], row["name"],
                     row["location_name"], row["latitude"], row["longitude"],
                     _j(row["design"]), row["status"], row["deployed_at"],
                     row["notes"], _j(row["metrics"]) if row["metrics"] else None,
                     row["created_at"], row["updated_at"]))
                self._conn.commit()
            except sqlite3.OperationalError:
                pass

    def list_shelters(self, limit: int = 200,
                      user_id: str | None = None) -> list[dict]:
        if self._rest:
            params = {"select": "*", "order": "updated_at.desc",
                      "limit": max(1, min(limit, 200))}
            if user_id is None:
                params["user_id"] = "is.null"
            else:
                params["user_id"] = f"eq.{user_id}"
            rows = self._pg("GET", "shelters", params=params) or []
            for r in rows:
                r["design"] = _maybe_json(r.get("design")) or {}
                r["metrics"] = _maybe_json(r.get("metrics"))
            return rows
        try:
            cur = self._conn.execute(
                "SELECT * FROM shelters WHERE user_id IS ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (None if user_id is None else user_id,
                 max(1, min(limit, 200))))
            cols = [c[0] for c in cur.description]
            out = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                d["design"] = json.loads(d["design"]) if d.get("design") else {}
                d["metrics"] = json.loads(d["metrics"]) if d.get("metrics") else None
                out.append(d)
            return out
        except sqlite3.OperationalError:
            return []

    def get_shelter(self, shelter_id: str) -> dict | None:
        if self._rest:
            rows = self._pg("GET", "shelters",
                            params={"select": "*",
                                    "shelter_id": f"eq.{shelter_id}",
                                    "limit": 1}) or []
            if not rows:
                return None
            r = rows[0]
            r["design"] = _maybe_json(r.get("design")) or {}
            r["metrics"] = _maybe_json(r.get("metrics"))
            return r
        try:
            cur = self._conn.execute(
                "SELECT * FROM shelters WHERE shelter_id = ?", (shelter_id,))
            row = cur.fetchone()
            if not row:
                return None
            d = dict(zip([c[0] for c in cur.description], row))
            d["design"] = json.loads(d["design"]) if d.get("design") else {}
            d["metrics"] = json.loads(d["metrics"]) if d.get("metrics") else None
            return d
        except sqlite3.OperationalError:
            return None

    def update_shelter(self, shelter_id: str, patch: dict) -> dict | None:
        row = dict(patch)
        row["updated_at"] = _now()
        if "metrics" in row and row["metrics"] is not None:
            pass  # keep raw object for JSONB
        if self._rest:
            self._pg("PATCH", "shelters",
                     params={"shelter_id": f"eq.{shelter_id}"}, body=row)
            return self.get_shelter(shelter_id)
        try:
            keys = [k for k in row if k != "shelter_id"]
            if not keys:
                return self.get_shelter(shelter_id)
            sets = ", ".join(f"{k} = ?" for k in keys)
            vals = [json.dumps(row[k], default=str) if k in ("design", "metrics")
                    and row[k] is not None else row[k] for k in keys]
            self._conn.execute(
                f"UPDATE shelters SET {sets} WHERE shelter_id = ?",
                (*vals, shelter_id))
            self._conn.commit()
            return self.get_shelter(shelter_id)
        except sqlite3.OperationalError:
            return None

    def delete_shelter(self, shelter_id: str) -> bool:
        if self._rest:
            rows = self._pg("DELETE", "shelters",
                            params={"shelter_id": f"eq.{shelter_id}"})
            return rows is not None
        try:
            self._conn.execute("DELETE FROM shelters WHERE shelter_id = ?",
                               (shelter_id,))
            self._conn.commit()
            return True
        except sqlite3.OperationalError:
            return False

    def count_rows(self, table: str) -> int:
        """Row count for stats (any table)."""
        if self._rest:
            try:
                r = requests.get(f"{self._rest}/{table}",
                                 headers={**self._headers,
                                          "Prefer": "count=exact",
                                          "Range": "0-0"},
                                 params={"select": "*"}, timeout=30)
                cr = r.headers.get("Content-Range", "")
                if cr and "/" in cr:
                    return int(cr.split("/")[-1])
                return 0
            except requests.RequestException:
                return 0
        try:
            cur = self._conn.execute(f"SELECT COUNT(*) FROM {table}")
            return int(cur.fetchone()[0])
        except sqlite3.OperationalError:
            return 0


    def list_cad_imports(self, limit: int = 8) -> list[dict]:
        if self._rest:
            rows = self._pg("GET", "cad_imports",
                            params={"select": "*", "order": "created_at.desc",
                                    "limit": max(1, min(limit, 50))}) or []
            for r in rows:
                for k in ("bbox", "dimensions", "entity_counts"):
                    r[k] = _maybe_json(r.get(k))
            return rows
        try:
            cur = self._conn.execute(
                "SELECT import_id, created_at, filename, format, source, "
                "bbox, dimensions, entity_counts FROM cad_imports "
                "ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 50)),))
            cols = [c[0] for c in cur.description]
            out = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                for k in ("bbox", "dimensions", "entity_counts"):
                    if isinstance(d.get(k), str):
                        try:
                            d[k] = json.loads(d[k])
                        except Exception:
                            d[k] = None
                out.append(d)
            return out
        except sqlite3.OperationalError:
            return []


def _f(v) -> float | None:
    if v is None or (isinstance(v, float) and v != v):   # NaN
        return None
    return float(v)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"
