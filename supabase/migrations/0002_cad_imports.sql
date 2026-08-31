-- CAD ingestion log — every import from any CAD channel is recorded
-- (file name, source format, bounding box, dimensions, entity counts).
CREATE TABLE IF NOT EXISTS cad_imports (
  import_id TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  filename TEXT NOT NULL,
  format TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'upload',
  bbox JSONB,
  dimensions JSONB,
  entity_counts JSONB
);

ALTER TABLE cad_imports ENABLE ROW LEVEL SECURITY;

-- service_role (the API) bypasses RLS; no public access by default.
CREATE POLICY "cad_imports_service_all"
  ON cad_imports FOR ALL
  TO service_role USING (true) WITH CHECK (true);
