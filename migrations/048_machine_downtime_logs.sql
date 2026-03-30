-- 048: Machine downtime logs — records idle alerts and operator responses
-- Stores each downtime alert sent by DowntimeSupervisor and the operator's reason code reply

BEGIN;

CREATE TABLE IF NOT EXISTS machine_downtime_logs (
    id                  SERIAL PRIMARY KEY,
    machine_name        TEXT NOT NULL,
    alert_sent_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    idle_minutes        REAL NOT NULL,
    operator_name       TEXT,           -- operator assigned to machine at alert time
    machinist_name      TEXT,           -- machinist from MTConnect at alert time
    reason_code         INTEGER REFERENCES stoppage_reasons(code),
    reason_reported_at  TIMESTAMPTZ,
    reporter_phone      TEXT,           -- normalized phone of the employee who replied
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_machine_downtime_logs_machine_name
    ON machine_downtime_logs (machine_name);

CREATE INDEX IF NOT EXISTS idx_machine_downtime_logs_alert_sent_at
    ON machine_downtime_logs (alert_sent_at DESC);

COMMENT ON TABLE machine_downtime_logs IS
    'Log of downtime alerts sent by DowntimeSupervisor and operator reason-code replies via WhatsApp.';

INSERT INTO schema_migrations (version, applied_at)
VALUES ('048_machine_downtime_logs', NOW())
ON CONFLICT (version) DO NOTHING;

COMMIT;
