-- 0005 — dedicated account table.
-- NOTE: this project's Postgres already contains a `users` table owned by a
-- different application (Supabase Auth mirror with patient records). We must
-- never touch it, so our accounts live in a namespaced table.
CREATE TABLE IF NOT EXISTS sih_users (
  user_id    TEXT PRIMARY KEY,
  username   TEXT NOT NULL UNIQUE,
  pass_hash  TEXT NOT NULL,           -- PBKDF2-HMAC-SHA256 hex
  salt       TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sih_users_username ON sih_users(username);
