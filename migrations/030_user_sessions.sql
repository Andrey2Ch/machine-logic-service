-- Create user_sessions table for session tracking
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS user_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id INTEGER NOT NULL,
  device_type VARCHAR(20) NOT NULL,
  user_agent TEXT NULL,
  ip_address VARCHAR(64) NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT true,
  revoked_at TIMESTAMPTZ NULL,
  revoked_reason VARCHAR(50) NULL,
  revoked_by INTEGER NULL
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fk_user_sessions_user'
  ) THEN
    ALTER TABLE user_sessions
      ADD CONSTRAINT fk_user_sessions_user
        FOREIGN KEY (user_id)
        REFERENCES employees(id)
        ON DELETE CASCADE;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fk_user_sessions_revoked_by'
  ) THEN
    ALTER TABLE user_sessions
      ADD CONSTRAINT fk_user_sessions_revoked_by
        FOREIGN KEY (revoked_by)
        REFERENCES employees(id)
        ON DELETE SET NULL;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_user_sessions_user_active
  ON user_sessions(user_id, is_active);

CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at
  ON user_sessions(expires_at);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('030_user_sessions', NOW())
ON CONFLICT (version) DO NOTHING;
