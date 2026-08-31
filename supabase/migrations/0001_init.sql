-- ============================================================================
-- SIH26051 Shelter — Supabase schema (Postgres)
-- Run in the Supabase SQL editor, or:  supabase db push
-- Mirrored 1:1 by src/db/store.py (SQLite fallback uses the same tables).
-- RLS: everyone may READ; only the service role may WRITE (server-side).
-- ============================================================================

create table if not exists public.locations (
  location_id text primary key,
  name text not null,
  latitude double precision not null,
  longitude double precision not null,
  elevation_m double precision not null default 0,
  timezone text not null default 'UTC',
  created_at timestamptz not null default now()
);

create table if not exists public.materials (
  material text primary key,
  category text,
  k_W_mK double precision not null,
  density_kg_m3 double precision not null,
  cp_J_kgK double precision not null,
  solar_absorptance double precision not null,
  emissivity double precision not null,
  thickness_m double precision not null,
  source text not null,
  notes text
);

create table if not exists public.weather (
  location_id text not null references public.locations(location_id),
  ts_utc timestamptz not null,
  t2m double precision, rh2m double precision, ws10m double precision,
  wd10m double precision, ps double precision, ghi double precision,
  ghi_clear double precision, precip double precision, t2mdew double precision,
  primary key (location_id, ts_utc)
);
create index if not exists idx_weather_loc_ts
  on public.weather (location_id, ts_utc desc);

create table if not exists public.simulations (
  sim_id text primary key,
  created_at timestamptz not null default now(),
  location_id text,
  design jsonb not null,
  engine text not null default 'rc',
  period text not null,
  metrics jsonb not null
);

create table if not exists public.simulation_results (
  sim_id text not null references public.simulations(sim_id) on delete cascade,
  ts timestamptz not null,
  indoor_t_c double precision,
  outdoor_t_c double precision,
  q_solar_w double precision,
  q_conduct_w double precision,
  q_vent_w double precision,
  q_net_w double precision,
  primary key (sim_id, ts)
);

create table if not exists public.optimization_runs (
  run_id text primary key,
  created_at timestamptz not null default now(),
  location_id text,
  n_trials integer not null,
  best_tpi double precision,
  best_design jsonb
);

create table if not exists public.optimization_trials (
  run_id text not null references public.optimization_runs(run_id) on delete cascade,
  trial_no integer not null,
  tpi double precision,
  params jsonb,
  primary key (run_id, trial_no)
);

-- ---------------------------------------------------------------------------
-- Row Level Security: public read, service-role write only
-- ---------------------------------------------------------------------------
alter table public.locations enable row level security;
alter table public.materials enable row level security;
alter table public.weather enable row level security;
alter table public.simulations enable row level security;
alter table public.simulation_results enable row level security;
alter table public.optimization_runs enable row level security;
alter table public.optimization_trials enable row level security;

do $$
declare t text;
begin
  foreach t in array array['locations','materials','weather','simulations',
                          'simulation_results','optimization_runs',
                          'optimization_trials']
  loop
    execute format(
      'create policy "public_read_%s" on public.%I for select using (true);',
      t, t);
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- Optional: keep the weather cache bounded (a full year ≈ 8.7k rows/location)
-- ---------------------------------------------------------------------------
-- create index if not exists idx_weather_ts on public.weather (ts_utc);
