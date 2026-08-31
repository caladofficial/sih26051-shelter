-- Design library — saved design history (designs, favorites, notes).
CREATE TABLE IF NOT EXISTS designs (
  design_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  design JSONB NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  favorite BOOLEAN NOT NULL DEFAULT FALSE
);

ALTER TABLE designs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "designs_service_all"
  ON designs FOR ALL
  TO service_role USING (true) WITH CHECK (true);
