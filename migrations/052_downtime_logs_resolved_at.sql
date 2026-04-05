-- 052: Add resolved_at to machine_downtime_logs
--
-- Filled by DowntimeSupervisor when machine returns to working state.
-- Allows calculating actual downtime duration: resolved_at - alert_sent_at.

BEGIN;

ALTER TABLE machine_downtime_logs
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN machine_downtime_logs.resolved_at IS
    'When the machine returned to working state (set by DowntimeSupervisor). NULL = still idle or unknown.';

INSERT INTO schema_migrations (version, applied_at)
VALUES ('052_downtime_logs_resolved_at', NOW())
ON CONFLICT (version) DO NOTHING;

COMMIT;
