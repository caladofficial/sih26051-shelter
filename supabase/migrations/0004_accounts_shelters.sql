-- 0004 — accounts + shelter fleet management
-- Per-user design saving and the shelter management module.

ALTER TABLE designs ADD COLUMN IF NOT EXISTS user_id TEXT;

CREATE TABLE IF NOT EXISTS users (
  user_id    TEXT PRIMARY KEY,
  username   TEXT NOT NULL UNIQUE,
  pass_hash  TEXT NOT NULL,           -- PBKDF2-HMAC-SHA256 hex
  salt       TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shelters (
  shelter_id    TEXT PRIMARY KEY,
  user_id       TEXT,
  name          TEXT NOT NULL,
  location_name TEXT NOT NULL DEFAULT '',
  latitude      REAL,
  longitude     REAL,
  design        JSONB NOT NULL DEFAULT '{}'::jsonb,
  status        TEXT NOT NULL DEFAULT 'planned',   -- planned | deployed | maintenance | retired
  deployed_at   TEXT,
  notes         TEXT NOT NULL DEFAULT '',
  metrics       JSONB,               -- predicted comfort metrics (RC model)
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_designs_user   ON designs(user_id);
CREATE INDEX IF NOT EXISTS idx_shelters_user  ON shelters(user_id);
