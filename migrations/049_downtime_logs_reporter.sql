-- 049: Add reporter_name and reporter_role to machine_downtime_logs

BEGIN;

ALTER TABLE machine_downtime_logs
    ADD COLUMN IF NOT EXISTS reporter_name TEXT,
    ADD COLUMN IF NOT EXISTS reporter_role TEXT CHECK (reporter_role IN ('operator', 'machinist'));

COMMENT ON COLUMN machine_downtime_logs.reporter_name IS 'Full name of employee who reported the reason';
COMMENT ON COLUMN machine_downtime_logs.reporter_role IS 'Role of reporter: operator or machinist';

INSERT INTO schema_migrations (version, applied_at)
VALUES ('049_downtime_logs_reporter', NOW())
ON CONFLICT (version) DO NOTHING;

COMMIT;
