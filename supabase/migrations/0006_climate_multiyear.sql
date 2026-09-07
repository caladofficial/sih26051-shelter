-- ============================================================================
-- 0006 — Multi-year, self-updating climate archive
--
-- The existing public.weather table is already multi-year capable (its PK is
-- (location_id, ts_utc)), so hourly rows for 2024/2025/2026 coexist without a
-- schema change. What was missing:
--   * provenance per row  -> which service produced it, and is it final?
--   * a fast summary table so the Trends module doesn't scan ~350k rows
--   * an audit trail for the scheduled refresh job
-- ============================================================================

-- ---------------------------------------------------------------- provenance
alter table public.weather
  add column if not exists source text not null default 'nasa_power';

alter table public.weather
  add column if not exists data_status text not null default 'historical_reanalysis';

comment on column public.weather.source is
  'nasa_power | open-meteo-archive — never mix sources when comparing years';
comment on column public.weather.data_status is
  'historical_reanalysis (final) | provisional (ERA5T, may be revised) — '
  'forecast hours are never stored here';

create index if not exists idx_weather_source on public.weather (source);

-- ------------------------------------------------------------ year summaries
-- One row per (location, year). Written by scripts/refresh_climate.py.
create table if not exists public.climate_summary (
  location_id text not null references public.locations(location_id),
  year integer not null,
  site text not null,
  status text not null default 'complete',        -- complete | year_to_date
  source text not null default 'open-meteo-archive',
  summary jsonb not null,                          -- stats blob (see below)
  updated_at timestamptz not null default now(),
  primary key (location_id, year)
);

comment on table public.climate_summary is
  'Per-site per-year climate statistics: means, extremes, HDD/CDD, hours '
  'above 35C / below 0C, rainfall. Powers SEC/10 Climate Trends without '
  'scanning the hourly table.';

create index if not exists idx_climate_summary_site
  on public.climate_summary (site, year desc);

-- -------------------------------------------------------------- refresh log
-- Audit trail: proves the dataset is live rather than a one-off dump.
create table if not exists public.climate_refresh (
  id bigserial primary key,
  ran_at timestamptz not null default now(),
  sites integer not null default 0,
  weather_rows integer not null default 0,
  latest_hour_utc timestamptz,
  source text not null default 'open-meteo-archive',
  notes text
);

create index if not exists idx_climate_refresh_ran
  on public.climate_refresh (ran_at desc);

-- ----------------------------------------------------- RLS: public read only
alter table public.climate_summary enable row level security;
alter table public.climate_refresh enable row level security;

do $$
declare t text;
begin
  foreach t in array array['climate_summary','climate_refresh']
  loop
    if not exists (
      select 1 from pg_policies
      where schemaname = 'public' and tablename = t
        and policyname = 'public_read_' || t
    ) then
      execute format(
        'create policy "public_read_%s" on public.%I for select using (true);',
        t, t);
    end if;
  end loop;
end $$;

-- ------------------------------------------------- convenience: latest hour
create or replace view public.climate_coverage as
select
  l.location_id,
  l.name              as site,
  l.latitude,
  l.longitude,
  l.timezone,
  count(*)            as n_hours,
  min(w.ts_utc)       as first_hour_utc,
  max(w.ts_utc)       as last_hour_utc,
  count(distinct extract(year from w.ts_utc)) as n_years
from public.locations l
join public.weather w using (location_id)
group by l.location_id, l.name, l.latitude, l.longitude, l.timezone;

comment on view public.climate_coverage is
  'What climate data actually exists per site — drives the UI year selector '
  'and the "data current through" badge.';
