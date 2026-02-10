-- Cloud Print Queue: print jobs for Print Station (DYMO, etc.)

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'print_job_status') THEN
    CREATE TYPE print_job_status AS ENUM ('queued', 'leased', 'printing', 'done', 'failed', 'cancelled');
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS print_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  idempotency_key varchar(200) NOT NULL UNIQUE,
  kind varchar(50) NOT NULL,
  payload jsonb NOT NULL,
  copies integer NOT NULL DEFAULT 1,
  priority integer NOT NULL DEFAULT 100,
  status print_job_status NOT NULL DEFAULT 'queued',
  assigned_station_name varchar(120),
  lease_token uuid,
  lease_expires_at timestamptz,
  attempt_count integer NOT NULL DEFAULT 0,
  last_error text,
  started_at timestamptz,
  completed_at timestamptz,
  failed_at timestamptz,
  cancelled_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  created_by_employee_id integer REFERENCES employees(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_print_jobs_status_created_at
  ON print_jobs(status, created_at);

CREATE INDEX IF NOT EXISTS idx_print_jobs_lease_expires_at
  ON print_jobs(lease_expires_at);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('033_print_jobs', NOW())
ON CONFLICT (version) DO NOTHING;

