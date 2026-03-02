-- Fix: warehouse_discrepancy_adjustment was inflated because of per-batch GREATEST(0,...).
-- Correct formula: GREATEST(0, SUM(current_quantity - recounted_quantity)) per setup.
-- This gives the NET discrepancy, matching what analytics shows ("Принято: X из Y").

-- Step 1: Recalculate warehouse_discrepancy_adjustment with correct formula
UPDATE setup_quantity_adjustments adj
SET warehouse_discrepancy_adjustment = sub.disc
FROM (
    SELECT
        b.setup_job_id,
        GREATEST(0, COALESCE(SUM(COALESCE(b.current_quantity, 0) - b.recounted_quantity), 0)) as disc
    FROM batches b
    WHERE b.recounted_quantity IS NOT NULL
      AND b.parent_batch_id IS NULL
    GROUP BY b.setup_job_id
) sub
WHERE adj.setup_job_id = sub.setup_job_id;

-- Zero out setups that have no recounted batches at all
UPDATE setup_quantity_adjustments adj
SET warehouse_discrepancy_adjustment = 0
WHERE NOT EXISTS (
    SELECT 1 FROM batches b
    WHERE b.setup_job_id = adj.setup_job_id
      AND b.recounted_quantity IS NOT NULL
      AND b.parent_batch_id IS NULL
);

-- Step 2: Recalculate additional_quantity = defect + warehouse_discrepancy
UPDATE setup_jobs sj
SET additional_quantity = COALESCE(adj.defect_adjustment, 0) + COALESCE(adj.warehouse_discrepancy_adjustment, 0)
FROM setup_quantity_adjustments adj
WHERE adj.setup_job_id = sj.id;

-- Step 3: Recalculate total_planned_quantity
UPDATE lots l
SET total_planned_quantity = COALESCE(l.initial_planned_quantity, 0) + COALESCE(
    (SELECT MAX(COALESCE(sj.additional_quantity, 0))
     FROM setup_jobs sj
     WHERE sj.lot_id = l.id),
    0
)
WHERE EXISTS (SELECT 1 FROM setup_jobs sj WHERE sj.lot_id = l.id);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('042_fix_warehouse_discrepancy_formula', NOW())
ON CONFLICT (version) DO NOTHING;
