-- Fix corrupted data from buggy warehouse discrepancy code (commit 57e140a, now reverted)
-- That code incorrectly modified manual_adjustment, warehouse_discrepancy_adjustment,
-- additional_quantity and total_planned_quantity.

-- Step 1: Reset warehouse_discrepancy_adjustment and manual_adjustment
UPDATE setup_quantity_adjustments
SET warehouse_discrepancy_adjustment = 0,
    manual_adjustment = NULL;

-- Step 2: Recalculate setup_jobs.additional_quantity from defect_adjustment only
-- (auto_adjustment and manual_adjustment are not used programmatically)
UPDATE setup_jobs sj
SET additional_quantity = COALESCE(
    (SELECT COALESCE(adj.defect_adjustment, 0)
     FROM setup_quantity_adjustments adj
     WHERE adj.setup_job_id = sj.id),
    0
)
WHERE EXISTS (
    SELECT 1 FROM setup_quantity_adjustments adj WHERE adj.setup_job_id = sj.id
);

-- Step 3: Recalculate lots.total_planned_quantity from setup_jobs
UPDATE lots l
SET total_planned_quantity = COALESCE(l.initial_planned_quantity, 0) + COALESCE(
    (SELECT MAX(COALESCE(sj.additional_quantity, 0))
     FROM setup_jobs sj
     WHERE sj.lot_id = l.id),
    0
)
WHERE EXISTS (
    SELECT 1 FROM setup_jobs sj WHERE sj.lot_id = l.id
);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('041_fix_warehouse_discrepancy_data', NOW())
ON CONFLICT (version) DO NOTHING;
