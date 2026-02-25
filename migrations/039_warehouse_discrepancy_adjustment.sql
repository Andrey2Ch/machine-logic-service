-- Add warehouse discrepancy adjustment to setup_quantity_adjustments
-- Tracks difference between operator-reported quantity and warehouse-recounted quantity
ALTER TABLE setup_quantity_adjustments
    ADD COLUMN IF NOT EXISTS warehouse_discrepancy_adjustment INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN setup_quantity_adjustments.warehouse_discrepancy_adjustment
    IS 'Auto-calculated: sum of (current_quantity - recounted_quantity) for warehouse-received batches in this setup';

-- Recreate total_adjustment as generated column including the new field
ALTER TABLE setup_quantity_adjustments DROP COLUMN IF EXISTS total_adjustment;
ALTER TABLE setup_quantity_adjustments
    ADD COLUMN total_adjustment INTEGER GENERATED ALWAYS AS (
        COALESCE(auto_adjustment, 0)
        + COALESCE(manual_adjustment, 0)
        + COALESCE(defect_adjustment, 0)
        + COALESCE(warehouse_discrepancy_adjustment, 0)
    ) STORED;

INSERT INTO schema_migrations (version, applied_at)
VALUES ('039_warehouse_discrepancy_adjustment', NOW())
ON CONFLICT (version) DO NOTHING;
