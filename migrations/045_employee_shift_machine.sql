-- Migration 045: Add shift and assigned_machine_id to employees
-- Смена (A/B) и закреплённый станок для операторов

ALTER TABLE employees
  ADD COLUMN IF NOT EXISTS shift VARCHAR(1) NULL CHECK (shift IN ('A', 'B')),
  ADD COLUMN IF NOT EXISTS assigned_machine_id INTEGER NULL REFERENCES machines(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_employees_shift ON employees(shift);
CREATE INDEX IF NOT EXISTS idx_employees_assigned_machine ON employees(assigned_machine_id);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('045', NOW())
ON CONFLICT (version) DO NOTHING;
