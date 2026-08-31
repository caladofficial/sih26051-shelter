# Deploying SIH26051 — Supabase + Vercel

Two cloud pieces, both free-tier:
1. **Supabase** — Postgres storage (weather cache, materials, simulations, optimization runs)
2. **Vercel** — static frontend (`public/`) + Python serverless API (`api/index.py`)

> ⚠️ **What runs where:** the Vercel API runs the *fast RC model* (validated against
> EnergyPlus). EnergyPlus itself cannot run in serverless functions — it stays the
> desktop/CLI validation engine (`make eplus`). The API returns the same physics with
> <1 s response times.

## Step 1 — Supabase

1. Create a free project at https://supabase.com (region: nearest to your users, e.g. Mumbai).
2. Open **SQL Editor** and run the two scripts in order:
   - `supabase/migrations/0001_init.sql` (tables + RLS)
   - `supabase/seed.sql` (12 sourced materials + Prayagraj location)
3. Project Settings → **API**: copy the **Project URL**, **anon key**, **service_role key**.
4. Optional (fancy, not required): deploy the weather-proxy edge function
   ```bash
   supabase functions deploy fetch-weather
   ```

## Step 2 — Vercel

Option A — **no install, from GitHub** (recommended for the team):
1. Push this repo to GitHub.
2. vercel.com → *Add New Project* → import the repo → Vercel auto-detects `vercel.json`.
3. Add environment variables (Settings → Environment Variables):
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_ROLE_KEY`
4. **Deploy.** Done. The app is served from `public/`, the API from `api/index.py`.

Option B — CLI:
```bash
npm i -g vercel
vercel login
vercel --prod
```
First run asks: root directory `.` → framework `Other` → build `None` → overrides apply.

## Step 3 — Verify

```bash
curl https://<your-app>.vercel.app/api/health          # {"status":"ok","backend":"supabase"}
curl https://<your-app>.vercel.app/api/materials       # 12 materials from Postgres
curl -X POST https://<your-app>.vercel.app/api/simulate \
  -H "Content-Type: application/json" \
  -d '{"period":"hot_week","insulation_material":"eps","insulation_thickness_m":0.05}'
```
Then open the site in a browser — Location → Climate → Design → Simulate → Optimize.

## Digital structure & CAD channels

The API can generate the shelter's **digital structure** from the design
parameters (no CAD file needed — "create yourself") and can ingest geometry
from **any supported CAD channel**:

| Endpoint | Purpose |
|---|---|
| `POST /api/cad/structure` | digital structure: component boxes (floor/walls/insulation/roof/window), envelope assembly (R/U per layer), surfaces, bounding box |
| `GET /api/cad/export?format=dxf\|obj\|stl&…` | export the current design as AutoCAD DXF (LINE+3DFACE, named layers), Wavefront OBJ or 3D-print STL |
| `POST /api/cad/import` (multipart `file`) | ingest DXF / OBJ / STL from any source → bounding box + suggested length/width/height; every ingest is logged to `cad_imports` (Supabase) |
| `GET /api/cad/imports?limit=n` | recent ingestion log |

Formats are parsed in pure Python (`src/cad/`) — zero extra serverless
dependencies. Units note: DXF/OBJ/STL carry no units; dimensions are read as
**metres** and stated in the response. Sanity envelope 0.1–60 m per axis.
The 3D viewer in the UI renders the same component boxes the exporters write
(single source of truth: `src/cad/model.py` ↔ `public/app.js`).

## Step 4 — Preload data (optional but smart)

From your machine, upload the real 2024 weather cache so the deployed API never
needs a live POWER fetch for the demo location:
```bash
cp .env.example .env        # fill in the keys
make supabase               # python scripts/sync_supabase.py --weather-year 2024
```

## Environment variables

| Variable | Where | Purpose |
|---|---|---|
| `SUPABASE_URL` | Vercel + local `.env` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Vercel + local `.env` | server-side writes (never expose to the browser) |
| `SUPABASE_ANON_KEY` | local `.env` | fallback; not required server-side |

With no keys set, everything still works locally on **SQLite** (`data/processed/app.db`).

## Serverless entrypoint rules (IMPORTANT — learned the hard way)

The Vercel Python builder statically analyses `api/index.py` and chooses between
two routing modes:

- **"app" mode** — a top-level ASGI `app` is detected → ONE catch-all function
  (`out/fastapi`) → every path is forwarded to FastAPI, which routes internally.
  ✅ This is the mode that works.
- **"api-dir" mode** — no top-level `app` detected → each `api/*.py` becomes its
  own function (`out/api/index`) AND the builder emits an explicit
  `{"src": "^/api(/.*)?$", "status": 404}` route, so **every `/api/*` request
  404s at the platform edge**. ❌ This is what broke our first deployments.

Rules that keep us in "app" mode:

1. **`api/index.py` must be a one-line re-export** — the full app lives in
   `src/api_app.py`:
   ```python
   from src.api_app import app  # noqa: F401  (re-export for uvicorn/tests)
   ```
   Anything more complex (module-level imports of pandas/numpy, `sys.path`
   fiddling, helpers) can trip the static analyser into "api-dir" mode.
   `uvicorn api.index:app` and `tests/test_api.py` still work unchanged.
2. **Never let `pyproject.toml` / `uv.lock` / `.python-version` reach the
   upload** — running `vercel build` locally generates these files at the repo
   root, and once uploaded they flip the builder into a broken "api-dir" build.
   They are now in both `.gitignore` and `.vercelignore`; delete them if you
   ever see them in `git status` (they must never be committed).
3. **A fresh deployment after changing anything**: if the platform reports
   `Restored build cache from previous deployment`, add `--force` to the deploy
   command — the build cache can carry a broken analysis across deploys.
4. **Deploy with the Vercel CLI**, not the raw REST `/v13/deployments` API
   (the REST upload produced `invalid_vercel_json`/static-only deployments):
   ```bash
   vercel deploy --prod --yes
   ```
   Re-deploy after changing env vars — variables are baked in per deployment.

## Costs & limits (Vercel Hobby + Supabase Free)

- The standard function-bundle threshold is **225 MB** (`LAMBDA_SIZE_THRESHOLD`);
  above it Vercel "optimizes dependencies" and, in our experience, the function
  stops being routed. Our deploy is lean on purpose:
  `numpy + pandas + fastapi + optuna` ≈ 150 MB — no pvlib/scipy/h5py/supabase
  SDK (replaced by the bit-equivalent pure-NumPy port in `src/data/solar.py`
  and plain PostgREST over `requests` in `src/db/store.py`).
- Function timeout: default 10 s, we set `maxDuration: 60` in `vercel.json`.
  `/api/simulate` ≈ 1–3 s · `/api/optimize` (30 trials) ≈ 5–8 s — comfortably inside.
- Supabase free: 500 MB database, 50k monthly active rows read — plenty for a demo.
- NASA POWER + Open-Meteo: free, no keys.

## Troubleshooting

- **Every `/api/*` returns platform `NOT_FOUND`** (a Vercel 404 page, not JSON)
  → the builder chose "api-dir" mode. Check `api/index.py` is still the thin
  shim, delete `pyproject.toml`/`uv.lock`/`.python-version` if present, and
  redeploy with `--force`.
- **`{"detail":"Not Found"}` from `/api/xyz`** → that's FastAPI answering — the
  function is routed correctly; the path just isn't a registered route.
- **API 500s on /api/climate** → Supabase env keys wrong or network-restricted;
  the endpoint falls back to live fetch either way.
- **Slow cold start** → first request after idle takes ~2–4 s (importing numpy/pandas);
  subsequent calls are fast.
- **EnergyPlus in the cloud?** Not with Vercel. Use `make eplus` locally for final
  validation; the deployed API runs the validated RC model.
