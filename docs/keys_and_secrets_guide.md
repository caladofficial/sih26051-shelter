# SIH26051 Shelter Studio — Keys, Tokens & Secrets Guide

This guide details all API keys, access tokens, and environment variables used across the **SIH26051 Shelter Studio** platform, where to obtain them, how they are secured, and where each one must be configured.

---

## 1. Secrets & Environment Variables Overview

| Variable / Secret | Secret Level | Required Environments | Purpose |
|---|---|---|---|
| `SUPABASE_URL` | Non-Secret / Endpoint | Local `.env`, Vercel, GitHub Actions | Supabase REST / PostgREST endpoint (`https://<project-ref>.supabase.co`) |
| `SUPABASE_ANON_KEY` | Public / Client Key | Local `.env`, Vercel | Supabase anonymous client JWT key (subject to RLS policies) |
| `SUPABASE_SERVICE_ROLE_KEY` | **High Secret** (Admin) | Local `.env`, Vercel, GitHub Actions | Administrative server key that bypasses RLS for backend data sync, simulation logs, and hourly weather storage |
| `GITHUB_TOKEN` | **High Secret** (PAT) | Local credentials, Git helper, Actions | Personal Access Token with `repo` / `contents: write` permissions for pushing changes and running workflows |
| `VERCEL_TOKEN` | **High Secret** (API) | Local deployment CLI, API CI | Vercel Personal Access Token for triggering deployments and checking deployment status |
| `VERCEL_PROJECT_ID` | Identifier | `.vercel/project.json` or CLI | Project ID (`prj_...`) in Vercel project settings |
| `VERCEL_TEAM_ID` | Identifier | `.vercel/project.json` or CLI | Team ID (`team_...`) in Vercel team settings |

---

## 2. Where to Obtain Each Key

### A. Supabase Credentials
1. Go to your Supabase project dashboard at [https://supabase.com/dashboard](https://supabase.com/dashboard).
2. Navigate to **Project Settings** (gear icon) → **API**.
3. Under **Project URL**, copy the URL → this is your `SUPABASE_URL`.
4. Under **Project API keys**:
   - `anon` `public` → this is your `SUPABASE_ANON_KEY`.
   - `service_role` `secret` (click *Reveal key*) → this is your `SUPABASE_SERVICE_ROLE_KEY`.

### B. GitHub Personal Access Token (PAT)
1. Go to GitHub → **Settings** → **Developer Settings** → **Personal Access Tokens** → **Tokens (classic)**.
2. Click **Generate new token (classic)**.
3. Select scopes:
   - `repo` (Full control of private repositories, including commit and workflow triggers)
   - `workflow` (Update GitHub Action workflows)
4. Copy the generated token immediately.

### C. Vercel Access Token
1. Go to [https://vercel.com/account/tokens](https://vercel.com/account/tokens).
2. Click **Create Token**, specify an expiration, and set permissions.
3. Copy the token.

---

## 3. Where Each Key Must Be Configured

### 1. Local Development (`.env`)
Create a file named `.env` in the root of the project:
```bash
cp .env.example .env
```
Fill in your credentials:
```env
# Supabase Database & PostgREST API
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOi...
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi...

# Performance Tuning
SUPABASE_WRITE_BATCH=5000
SUPABASE_PAGE_SIZE=10000

# Optional Server Port
PORT=8200
```
> **Security Note:** The `.env` file is excluded from git via `.gitignore`. Never commit `.env` containing live secrets.

---

### 2. GitHub Actions Secrets (CI/CD Climate Refresh)
To allow the automated nightly workflow (`.github/workflows/refresh-climate.yml`) to fetch weather data and update Supabase:
1. Open your repository on GitHub: `https://github.com/caladofficial/sih26051-shelter`.
2. Go to **Settings** → **Secrets and variables** → **Actions**.
3. Add two Repository Secrets:
   - `SUPABASE_URL`: Your Supabase project URL.
   - `SUPABASE_SERVICE_ROLE_KEY`: Your Supabase service role secret key.

---

### 3. Vercel Production Environment
To allow the live serverless API (`api/index.py` & `src/api_app.py`) to read and write to Supabase:
1. Go to your project on Vercel: [https://vercel.com/dashboard](https://vercel.com/dashboard).
2. Select your project → **Settings** → **Environment Variables**.
3. Add:
   - `SUPABASE_URL` (Production, Preview, Development)
   - `SUPABASE_ANON_KEY` (Production, Preview, Development)
   - `SUPABASE_SERVICE_ROLE_KEY` (Production, Preview, Development)
4. Trigger a redeploy so the environment variables take effect.

---

## 4. Git Synchronization Script

The repository includes a self-healing git sync script at `scripts/sync_github.sh`:
- It stores Git credentials securely in local user credentials rather than inside the repo.
- Re-wires `origin` if missing.
- Pulls with rebase before pushing to master.

---

## 5. Security Best Practices

1. **Never commit raw tokens or private keys** into Git, archives, or public logs.
2. **Never expose `SUPABASE_SERVICE_ROLE_KEY`** in browser-facing JavaScript (`public/app.js`). Browser code must only query backend endpoints (`/api/...`) or use the public `anon` key protected by Row Level Security (RLS).
3. **Row Level Security (RLS)**:
   - Ensure RLS is enabled on all tables (`weather`, `simulations`, `materials`, `nlp_feedback`).
   - Migrations in `supabase/migrations/` configure appropriate public read/insert policies.
4. If a token is ever accidentally exposed, revoke and rotate it immediately in the provider's dashboard.
