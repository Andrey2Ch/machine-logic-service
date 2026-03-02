-- Fix: warehouse discrepancy must be calculated at LOT level (matching analytics "Принято X из Y"),
-- not per-setup. Per-setup calculation inflates when a lot has multiple setups.
-- Strategy: one setup per lot holds the discrepancy (MAX setup_job_id), others get 0.

-- Step 1: Zero out all warehouse_discrepancy_adjustment
UPDATE setup_quantity_adjustments SET warehouse_discrepancy_adjustment = 0;

-- Step 2: Calculate lot-level discrepancy and assign to the latest setup per lot
WITH lot_disc AS (
    SELECT
        b.lot_id,
        GREATEST(0, COALESCE(SUM(COALESCE(b.current_quantity, 0) - b.recounted_quantity), 0)) as disc
    FROM batches b
    WHERE b.recounted_quantity IS NOT NULL
      AND b.parent_batch_id IS NULL
    GROUP BY b.lot_id
    HAVING SUM(COALESCE(b.current_quantity, 0) - b.recounted_quantity) > 0
),
latest_setup AS (
    SELECT DISTINCT ON (sj.lot_id) sj.id as setup_id, sj.lot_id
    FROM setup_jobs sj
    JOIN setup_quantity_adjustments adj ON adj.setup_job_id = sj.id
    ORDER BY sj.lot_id, sj.id DESC
)
UPDATE setup_quantity_adjustments adj
SET warehouse_discrepancy_adjustment = ld.disc
FROM latest_setup ls
JOIN lot_disc ld ON ld.lot_id = ls.lot_id
WHERE adj.setup_job_id = ls.setup_id;

-- Step 3: Recalculate additional_quantity for ALL setups
UPDATE setup_jobs sj
SET additional_quantity = COALESCE(adj.defect_adjustment, 0) + COALESCE(adj.warehouse_discrepancy_adjustment, 0)
FROM setup_quantity_adjustments adj
WHERE adj.setup_job_id = sj.id;

-- Step 4: Recalculate total_planned_quantity for ALL lots
UPDATE lots l
SET total_planned_quantity = COALESCE(l.initial_planned_quantity, 0) + COALESCE(
    (SELECT MAX(COALESCE(sj.additional_quantity, 0))
     FROM setup_jobs sj
     WHERE sj.lot_id = l.id),
    0
)
WHERE EXISTS (SELECT 1 FROM setup_jobs sj WHERE sj.lot_id = l.id);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('043_fix_warehouse_discrepancy_lot_level', NOW())
ON CONFLICT (version) DO NOTHING;
