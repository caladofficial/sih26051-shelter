# Climate data — multi-year, self-updating archive

> Replaces the original single-year (2024, NASA POWER) snapshot with a rolling
> multi-year archive that keeps itself current.

## Why this exists

The first version pinned every simulation to calendar year **2024**. Two
problems for a shelter-design tool:

1. **It goes stale.** A judge opening the site in 2026 was being shown 2024
   weather with no indication it was two years old.
2. **You can't see the design case move.** A shelter is sized against the
   hottest and coldest *week*, not the annual mean. If that week has warmed,
   the envelope spec has to follow — and a single year can't show that.

## What's stored

| Item | Where | Size | Committed? |
|---|---|---|---|
| Hourly rows, 15 sites × 2024–present | Supabase `weather` + `data/climate/hourly/*.csv` | ~26 MB | ❌ cache only |
| Coverage manifest | `data/climate/index.json` | 32 KB | ✅ |
| Derived fallback bundle | `src/data/climate_bundle.json` (canonical, ships in the function) + `public/data/` (CDN mirror) | ~740 KB | ✅ |
| Per-year statistics | Supabase `climate_summary` | — | ✅ (DB) |
| Refresh audit trail | Supabase `climate_refresh` | — | ✅ (DB) |

**Source:** Open-Meteo Historical Weather API (ERA5-family reanalysis), used
for *every* year. NASA POWER remains the cross-check for live custom
coordinates, but a year-over-year comparison must never mix sources — a source
bias would be indistinguishable from real climate change.

### Sites (15)

Prayagraj, Delhi, **Jaipur** (new, from the dataset package), Jaisalmer,
Ahmedabad, Chennai, Mumbai, Kolkata, Bengaluru, Hyderabad, Pune, Leh, Srinagar,
Kargil, Dras.

## The three data tiers

Reads fall through in order, so the site degrades gracefully instead of
breaking:

```
1. Supabase          live, authoritative, kept current by the refresh job
2. Local CSV archive dev + CI only (gitignored, not in the serverless bundle)
3. Static bundle     src/data/climate_bundle.json — summaries, monthly
                     means, diurnal curves and both design weeks. Renders
                     Climate Recon + Trends with no database at all.
                     NOTE: it lives in src/, not public/ — Vercel serves
                     public/ from the CDN and does not put it in the
                     function's filesystem, so an API reading it from
                     public/ gets FileNotFoundError in production.
4. Live fetch        POWER + Open-Meteo, for arbitrary coordinates that aren't
                     one of the 15 canonical sites
```

## Analysis periods

The UI no longer hardcodes a year. `GET /api/climate/coverage` builds the
selector from what actually exists.

- **`latest` (default)** — a *rolling* 365-day window ending at the newest
  observed hour. It moves forward on its own every time the refresh job runs,
  so the platform always reflects recent conditions.
- **`2024` / `2025` / `2026`** — calendar years, sliced on **local** time.

> **Local-year slicing matters.** The archive is stored in UTC. At +05:30 the
> last ~5.5 h of 31 December UTC are already 1 January locally, so a naive UTC
> slice produced a stray 13th month and mangled December statistics — it also
> made 2024's "coldest week" come out as a 4-day stub straddling New Year.
> Fixed in `climate_archive.slice_period()`; regression-tested.

## Self-updating pipeline

`scripts/refresh_climate.py`, run daily by
`.github/workflows/refresh-climate.yml` (02:15 UTC / 07:45 IST):

1. Find the newest hour already stored per site.
2. Re-fetch the last **10 days** *and* everything newer. Recent hours are
   provisional (ERA5T) and get revised when the final reanalysis lands, so the
   tail is rewritten rather than blindly appended.
3. Roll into a new calendar-year file automatically on 1 January.
4. Rebuild `data/climate/index.json` and the static bundle.
5. Upsert locations, hourly weather and per-year summaries into Supabase.
6. Commit the derived artifacts → Vercel redeploys with the fresh manifest.

Idempotent: a second run in the same day reports `+0 h` and commits nothing.

```bash
python scripts/refresh_climate.py                  # incremental, all sites
python scripts/refresh_climate.py --full           # re-download everything
python scripts/refresh_climate.py --no-supabase    # local only
python scripts/refresh_climate.py --sites Leh,Delhi
```

### Nothing is fabricated

2026 is year-to-date. If the archive stops at day *D*, the data stops at day
*D* and the manifest says `"status": "year_to_date"`. There is no
interpolation, no padding and no forecast dressed up as observation.
`tests/test_climate_multiyear.py::test_no_future_hours_are_fabricated` and
`::test_partial_year_is_flagged_not_padded` enforce this.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/climate/coverage` | Sites, years, freshness, selector options |
| `GET /api/climate?year=latest\|2025` | Recon for a period |
| `GET /api/climate/trends?site=Leh` | Year-over-year comparison + design shift |
| `POST /api/simulate` | `climate_period` field selects the weather window |
| `POST /api/optimize` | same |

`climate_period` is deliberately *not* called `period` — `period` already means
the simulated stress window (`hot_week` / `cold_week` / `full_year`).

### Like-for-like year comparison

The newest year is incomplete, so **every year is truncated to the same
day-of-year span** before any statistic is computed. Otherwise "2026 is hotter"
would just mean "2026 stops in September, before winter". The truncation point
is reported as `comparable_through_doy` and stated in the UI.

## Climatic zones use a normal, not one year

Zone classification (NBC 2016 / ECBC 2017 approximation) is computed over
**every archived year**, averaging each calendar month across years, then
taking the min/max of those twelve normals.

Two bugs this fixes:

- **Knife-edge flipping.** Jaisalmer's coldest month was 13.8 °C in 2026 but
  15.6 °C in 2025 — either side of the 14 °C "cold" threshold. The zone label
  flipped depending on which period the user had selected. A desert is not
  sometimes cold.
- **Extremes masquerading as normals.** Taking `min()` over every month of a
  multi-year archive returns the coldest month *ever recorded*, which gets
  colder the more years you add.

Part-months are excluded (`< 20 days`), so a rolling window starting mid-month
can't turn a 7-day stub into "the coldest month of the year".

The selected period still drives every *design* number — degree-days, design
weeks, simulations. Only the zone label uses the normal, and the profile
payload reports which via `zone_basis`.

> **Known limitation (pre-existing).** The `t_cold <= 14 °C → cold` rule is
> checked first, so Delhi (January normal 12.7 °C, but a 33 °C hottest month)
> classifies as `cold`. Real NBC zoning also requires the hottest month to be
> below ~30 °C. The thresholds were left untouched in this change; adjusting
> them is a separate, science-level decision.

## Regenerating from scratch

```bash
python scripts/fetch_climate_multiyear.py     # download 15 sites × 2024-now
python scripts/build_climate_bundle.py        # derived static bundle
python scripts/build_frontend.py              # copies bundle into public/
python -m pytest tests/test_climate_multiyear.py -q
node scripts/e2e_climate_trends.js            # needs scripts/dev_server.py
```

## Supabase

Migration `supabase/migrations/0006_climate_multiyear.sql`:

- `weather` gains `source` + `data_status` (provenance per row)
- `climate_summary` — per-site per-year stats, so SEC/10 never scans ~400k rows
- `climate_refresh` — audit trail proving the archive is live
- `climate_coverage` view — what exists per site

The existing `weather` PK `(location_id, ts_utc)` was already multi-year
capable, so no data migration was needed.
