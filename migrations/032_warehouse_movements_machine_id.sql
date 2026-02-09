-- Add related machine reference to warehouse movements
ALTER TABLE warehouse_movements
  ADD COLUMN IF NOT EXISTS related_machine_id INTEGER;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM information_schema.table_constraints
    WHERE constraint_name = 'warehouse_movements_related_machine_id_fkey'
      AND table_name = 'warehouse_movements'
  ) THEN
    ALTER TABLE warehouse_movements
      ADD CONSTRAINT warehouse_movements_related_machine_id_fkey
      FOREIGN KEY (related_machine_id) REFERENCES machines(id) ON DELETE SET NULL;
  END IF;
END $$;

INSERT INTO schema_migrations (version, applied_at)
VALUES ('032_warehouse_movements_machine_id', NOW())
ON CONFLICT (version) DO NOTHING;
