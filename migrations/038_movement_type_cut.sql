-- Add 'cut' to allowed movement types for batch cutting operations
ALTER TABLE warehouse_movements DROP CONSTRAINT IF EXISTS check_movement_type;
ALTER TABLE warehouse_movements
    ADD CONSTRAINT check_movement_type
    CHECK (movement_type IN ('receive', 'move', 'issue', 'return', 'writeoff', 'cut'));

INSERT INTO schema_migrations (version, applied_at)
VALUES ('038_movement_type_cut', NOW())
ON CONFLICT (version) DO NOTHING;
