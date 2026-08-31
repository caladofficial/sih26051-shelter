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

## Costs & limits (Vercel Hobby + Supabase Free)

- 1 GB function size limit — our deploy is lean (numpy+pandas+pvlib+fastapi+optuna).
- Function timeout: default 10 s, we set `maxDuration: 60` in `vercel.json`.
  `/api/simulate` ≈ 1–3 s · `/api/optimize` (30 trials) ≈ 4–6 s — comfortably inside.
- Supabase free: 500 MB database, 50k monthly active rows read — plenty for a demo.
- NASA POWER + Open-Meteo: free, no keys.

## Troubleshooting

- **`Runtime is not supported` on deploy** → change `"runtime": "python3.12"` in
  `vercel.json` to a supported Python version for your account/plan.
- **API 500s on /api/climate** → Supabase env keys wrong or network-restricted;
  the endpoint falls back to live fetch either way.
- **Slow cold start** → first request after idle takes ~5–10 s (importing numpy/pandas);
  subsequent calls are fast.
- **EnergyPlus in the cloud?** Not with Vercel. Use `make eplus` locally for final
  validation; results can be saved via `POST /api/simulate` with `engine: eplus` in a
  future version.
