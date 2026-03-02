-- Fix: warehouse discrepancy must use machine counter reading (same as analytics "Принято X из Y")
-- NOT SUM(current_quantity) which is inflated on parent batches.
-- Formula: discrepancy = max(0, machine_reading_at_last_acceptance - SUM(recounted_quantity))

-- Step 1: Zero out all warehouse_discrepancy_adjustment
UPDATE setup_quantity_adjustments SET warehouse_discrepancy_adjustment = 0;

-- Step 2: Calculate lot-level discrepancy using machine readings and assign to latest setup
WITH lot_disc AS (
    SELECT
        l.id as lot_id,
        GREATEST(0,
            COALESCE(
                (SELECT mr.reading 
                 FROM machine_readings mr
                 JOIN setup_jobs sj2 ON mr.setup_job_id = sj2.id
                 WHERE sj2.lot_id = l.id 
                   AND mr.setup_job_id IS NOT NULL
                   AND mr.created_at <= (
                       SELECT MAX(b2.warehouse_received_at) 
                       FROM batches b2
                       WHERE b2.lot_id = l.id 
                         AND b2.warehouse_received_at IS NOT NULL
                   )
                 ORDER BY mr.created_at DESC
                 LIMIT 1
                ), 0
            ) - COALESCE(
                (SELECT SUM(b.recounted_quantity)
                 FROM batches b
                 WHERE b.lot_id = l.id
                   AND b.recounted_quantity IS NOT NULL
                   AND b.parent_batch_id IS NULL
                ), 0
            )
        ) as disc
    FROM lots l
    WHERE EXISTS (
        SELECT 1 FROM batches b 
        WHERE b.lot_id = l.id 
          AND b.recounted_quantity IS NOT NULL
          AND b.parent_batch_id IS NULL
    )
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
WHERE adj.setup_job_id = ls.setup_id
  AND ld.disc > 0;

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
VALUES ('044_fix_warehouse_discrepancy_use_machine_reading', NOW())
ON CONFLICT (version) DO NOTHING;
