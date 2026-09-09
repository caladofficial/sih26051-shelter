-- ============================================================================
-- 0007 · NLP feedback loop (real user phrasings)
--
-- The plain-English assistant (SEC/11) was trained on synthetic templates.
-- The single biggest accuracy win is sentences from real users, so every
-- design answer asks "did I understand you correctly?" and stores the verdict
-- together with the original sentence. Rows here are the retraining corpus:
--   correct = true   → confirmed as-is (intent + slots were right)
--   correct = false  → the model got it wrong; `correction` carries what the
--                      user actually meant, in their own words
-- ml/nlp/import_feedback.py exports this table for evaluation/retraining.
-- ============================================================================

create table if not exists public.nlp_feedback (
  id          bigint generated always as identity primary key,
  created_at  timestamptz not null default now(),
  text        text not null,               -- the sentence the user typed
  intent      text,                        -- classifier's read (design/…)
  confidence  double precision,
  slots       jsonb,                       -- extracted slots (site, materials…)
  design      jsonb,                       -- design the assistant produced
  correct     boolean not null,            -- user's verdict on the whole answer
  correction  text                         -- "what did you mean?" (optional)
);

comment on table public.nlp_feedback is
  'User verdicts on plain-English design answers — the real-phrasing '
  'training corpus for the NLP assistant.';

create index if not exists idx_nlp_feedback_created
  on public.nlp_feedback (created_at desc);

-- writes come from the API's service key (bypasses RLS); the insert policy
-- also allows the anon key so the web client could post directly. There is
-- deliberately NO select policy — user sentences are not public data.
alter table public.nlp_feedback enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'nlp_feedback'
      and policyname = 'nlp_feedback_insert_any'
  ) then
    create policy "nlp_feedback_insert_any" on public.nlp_feedback
      for insert with check (true);
  end if;
end $$;
