-- Migration: Add avg_cycle_time column to parts table
-- Date: 2024-11-26
-- Description: Adds avg_cycle_time field to store average cycle time for parts.
--              For new parts, this is manually entered estimate.
--              For parts with history, this is automatically calculated from setup_jobs.

-- Add the column
ALTER TABLE parts 
ADD COLUMN IF NOT EXISTS avg_cycle_time INTEGER;

COMMENT ON COLUMN parts.avg_cycle_time IS 
'Average cycle time for the part in seconds. 
For new parts: manually entered estimate (editable).
For parts with history: automatically calculated from setup_jobs (read-only).';

-- Create index for faster queries
CREATE INDEX IF NOT EXISTS idx_parts_avg_cycle_time 
ON parts(avg_cycle_time) 
WHERE avg_cycle_time IS NOT NULL;

-- Initial population: Calculate avg_cycle_time for parts with existing setup history
UPDATE parts p
SET avg_cycle_time = subq.avg_ct
FROM (
    SELECT 
        part_id,
        ROUND(AVG(cycle_time))::int as avg_ct
    FROM setup_jobs
    WHERE cycle_time IS NOT NULL 
        AND cycle_time > 0
    GROUP BY part_id
) subq
WHERE p.id = subq.part_id
    AND p.avg_cycle_time IS NULL;  -- Only update if not already set

-- Create trigger function to auto-update avg_cycle_time
CREATE OR REPLACE FUNCTION update_part_avg_cycle_time()
RETURNS TRIGGER AS $$
BEGIN
    -- Only recalculate if the setup has a valid cycle_time
    IF NEW.cycle_time IS NOT NULL AND NEW.cycle_time > 0 THEN
        UPDATE parts
        SET avg_cycle_time = (
            SELECT ROUND(AVG(cycle_time))::int
            FROM setup_jobs
            WHERE part_id = NEW.part_id
                AND cycle_time IS NOT NULL
                AND cycle_time > 0
        )
        WHERE id = NEW.part_id;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger on setup_jobs
DROP TRIGGER IF EXISTS trigger_update_part_cycle_time ON setup_jobs;

CREATE TRIGGER trigger_update_part_cycle_time
AFTER INSERT OR UPDATE OF cycle_time ON setup_jobs
FOR EACH ROW
EXECUTE FUNCTION update_part_avg_cycle_time();

-- Verification queries (commented out, uncomment to test)
-- SELECT COUNT(*) as parts_with_avg_cycle_time 
-- FROM parts 
-- WHERE avg_cycle_time IS NOT NULL;

-- SELECT 
--     p.drawing_number,
--     p.avg_cycle_time,
--     COUNT(sj.id) as total_setups,
--     AVG(sj.cycle_time)::int as calculated_avg
-- FROM parts p
-- LEFT JOIN setup_jobs sj ON sj.part_id = p.id AND sj.cycle_time IS NOT NULL AND sj.cycle_time > 0
-- WHERE p.avg_cycle_time IS NOT NULL
-- GROUP BY p.id, p.drawing_number, p.avg_cycle_time
-- LIMIT 10;

