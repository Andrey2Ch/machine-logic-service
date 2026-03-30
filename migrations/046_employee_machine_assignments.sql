-- Migration 046: Replace assigned_machine_id with many-to-many employee_machine_assignments

CREATE TABLE IF NOT EXISTS employee_machine_assignments (
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    machine_id  INTEGER NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
    PRIMARY KEY (employee_id, machine_id)
);

CREATE INDEX IF NOT EXISTS idx_ema_employee ON employee_machine_assignments(employee_id);
CREATE INDEX IF NOT EXISTS idx_ema_machine  ON employee_machine_assignments(machine_id);

-- Перенос существующих данных из старой колонки
INSERT INTO employee_machine_assignments (employee_id, machine_id)
SELECT id, assigned_machine_id
FROM employees
WHERE assigned_machine_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- Удаляем старую колонку
ALTER TABLE employees DROP COLUMN IF EXISTS assigned_machine_id;

INSERT INTO schema_migrations (version, applied_at)
VALUES ('046', NOW())
ON CONFLICT (version) DO NOTHING;
