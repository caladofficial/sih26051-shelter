#!/usr/bin/env python3
"""Repair the Supabase `locations` table after the weather-cache naming bug.

What happened (root cause, fixed in src/api_app.py): every weather fetch that
missed the Supabase cache inserted a `locations` row built from
``{**DEFAULT_LOCATION, "latitude": lat, "longitude": lon}`` — so every site
ever simulated (Leh, Jaisalmer, Chennai, …) was stored with the config
default's name ("Prayagraj"), polluting the location dropdown with ~20
"Prayagraj" rows and making site selection useless.

This script:
  1. Loads the 14 engine-verified canonical sites (src/data/shelter_presets.json).
  2. Re-labels every artifact row to its nearest canonical site (≤0.35°,
     ~39 km) or to "Custom location" when it matches nothing.
  3. Keeps exactly one row per city — the one whose coordinates match the
     canonical site — and deletes stale coordinate variants of the same city
     (their weather-cache rows are keyed by location_id and are untouched).
  4. Fills real elevations from the Open-Meteo elevation API (same free
     source the climate pipeline already uses) for the canonical rows.

Only rows created by the seeding/live-fetch bug window (created before this
project's launch date, Aug 31 -> Sep 7 2026) are eligible; anything added
later by real users is left alone.

Usage:  python3 scripts/repair_locations.py        (needs Supabase env/.env)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from src.db.store import Store  # noqa: E402

PRESETS_FILE = ROOT / "src" / "data" / "shelter_presets.json"
TOL_DEG = 0.35
# artifact window: rows written by the buggy weather-cache upsert path
EARLIEST = "2026-08-31T00:00:00"
LATEST = "2026-09-07T00:00:00"

ELEV_API = "https://api.open-meteo.com/v1/elevation"


def nearest_site(data: dict, lat: float, lon: float):
    best, best_d = None, TOL_DEG
    for nm, sd in data["sites"].items():
        d = ((float(sd["latitude"]) - lat) ** 2
             + (float(sd["longitude"]) - lon) ** 2) ** 0.5
        if d < best_d:
            best, best_d = nm, d
    return best


def artifact(row: dict) -> bool:
    created = str(row.get("created_at", ""))
    return EARLIEST <= created[:19] <= LATEST


def main() -> int:
    data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    store = Store()
    if store.backend != "supabase":
        print("STOP: this repair targets the Supabase store "
              f"(backend={store.backend}).")
        return 1

    rows = store.list_locations() or []
    print(f"before: {len(rows)} rows")
    for r in rows:
        print("  -", r.get("location_id"), "|", r.get("name"),
              "|", r.get("latitude"), r.get("longitude"),
              "|", r.get("created_at", "")[:19])

    # 1) label every artifact row by nearest canonical site
    labels: dict[str, str] = {}
    for r in rows:
        if not artifact(r):
            labels[r["location_id"]] = r["name"]   # real user row: keep name
            continue
        nm = nearest_site(data, float(r["latitude"]), float(r["longitude"]))
        labels[r["location_id"]] = nm or "Custom location"

    # 2) keep one row per city: canonical coordinates win
    keep: dict[str, dict] = {}          # city -> row to keep
    for r in rows:
        city = labels[r["location_id"]]
        if city not in keep:
            keep[city] = r
            continue
        cur = keep[city]
        canon = data["sites"].get(city)
        if canon is None:
            keep[city] = cur            # custom rows: first occurrence stays
            continue
        d_new = (abs(float(r["latitude"]) - canon["latitude"])
                 + abs(float(r["longitude"]) - canon["longitude"]))
        d_old = (abs(float(cur["latitude"]) - canon["latitude"])
                 + abs(float(cur["longitude"]) - canon["longitude"]))
        if d_new < d_old:
            keep[city] = r

    keep_ids = {v["location_id"] for v in keep.values()}
    delete_ids = [r["location_id"] for r in rows
                  if r["location_id"] not in keep_ids]
    if delete_ids:
        # weather rows FK -> locations (RESTRICT); clear them first. Only the
        # stale coordinate variants' caches are dropped; kept sites keep theirs.
        store._pg("DELETE", "weather",
                  params={"location_id": f"in.({','.join(delete_ids)})"})
        ok = store._pg("DELETE", "locations",
                       params={"location_id": f"in.({','.join(delete_ids)})"})
        if ok is None:
            print("ERROR: could not delete stale location rows — aborting "
                  "further writes so the table stays consistent")
            return 1
        print(f"\ndeleted {len(delete_ids)} stale coordinate variants: "
              f"{delete_ids}")

    # 3) rename kept rows to the canonical label
    for r in rows:
        if r["location_id"] in delete_ids:
            continue
        want = labels[r["location_id"]]
        if r["name"] != want:
            ok = store._pg("PATCH", "locations",
                           params={"location_id": f"eq.{r['location_id']}"},
                           body={"name": want})
            print(f"renamed {r['location_id']}: "
                  f"{r['name']!r} -> {want!r}  ({'ok' if ok is not None else 'FAILED'})")

    # 4) real elevations for canonical rows (Open-Meteo elevation API)
    canon_rows = [r for r in rows if r["location_id"] not in delete_ids
                  and labels[r["location_id"]] in data["sites"]]
    lat_s = ",".join(f"{r['latitude']:.4f}" for r in canon_rows)
    lon_s = ",".join(f"{r['longitude']:.4f}" for r in canon_rows)
    try:
        resp = requests.get(ELEV_API,
                            params={"latitude": lat_s, "longitude": lon_s},
                            timeout=30)
        resp.raise_for_status()
        elevs = resp.json().get("elevation", [])
        for r, e in zip(canon_rows, elevs):
            if abs(float(e) - float(r.get("elevation_m", 0))) > 5:
                store._pg("PATCH", "locations",
                          params={"location_id": f"eq.{r['location_id']}"},
                          body={"elevation_m": round(float(e), 1)})
                print(f"elevation {r['location_id']}: "
                      f"{r.get('elevation_m')} -> {round(float(e), 1)} m")
    except requests.RequestException as exc:
        print(f"[warn] elevation refresh skipped: {exc}")

    after = store.list_locations() or []
    print(f"\nafter: {len(after)} rows")
    for r in sorted(after, key=lambda x: x["name"]):
        print("  -", r["name"], "|", r["latitude"], r["longitude"],
              "| elev", r.get("elevation_m"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
