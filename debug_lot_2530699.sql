-- Диагностика лота 2530699
-- Проверяем почему наладка в статусе "created" вместо "assigned"

-- 1. Информация о лоте
SELECT 
    id,
    lot_number,
    part_id,
    status,
    assigned_machine_id,
    assigned_order,
    created_at
FROM lots
WHERE lot_number = '2530699';

-- 2. Все наладки для этого лота (активные и завершенные)
SELECT 
    sj.id as setup_id,
    sj.status as setup_status,
    sj.machine_id,
    m.name as machine_name,
    sj.created_at,
    sj.end_time,
    sj.planned_quantity,
    sj.additional_quantity,
    sj.cycle_time,
    l.lot_number,
    l.status as lot_status
FROM setup_jobs sj
LEFT JOIN machines m ON sj.machine_id = m.id
LEFT JOIN lots l ON sj.lot_id = l.id
WHERE l.lot_number = '2530699'
ORDER BY sj.created_at DESC;

-- 3. Последние readings для наладок этого лота
SELECT 
    mr.id as reading_id,
    mr.setup_job_id,
    mr.reading,
    mr.created_at as reading_time,
    sj.status as setup_status,
    l.lot_number
FROM machine_readings mr
JOIN setup_jobs sj ON mr.setup_job_id = sj.id
JOIN lots l ON sj.lot_id = l.id
WHERE l.lot_number = '2530699'
ORDER BY mr.created_at DESC
LIMIT 10;

-- 4. Проверка логики endpoint /lots/ для этого лота
-- Имитируем запрос, который делает endpoint
WITH setup_data AS (
    SELECT 
        sj.lot_id,
        l.lot_number,
        m.name as machine_name,
        sj.created_at,
        sj.status,
        sj.id as setup_id,
        ROW_NUMBER() OVER (PARTITION BY sj.lot_id ORDER BY sj.created_at DESC) as rn
    FROM setup_jobs sj
    JOIN machines m ON sj.machine_id = m.id
    JOIN lots l ON sj.lot_id = l.id
    WHERE l.lot_number = '2530699'
      AND sj.status IN ('created', 'started', 'pending_qc', 'allowed')  -- Только активные!
)
SELECT * FROM setup_data WHERE rn = 1;

-- 5. Проверка последнего reading для активной наладки
WITH latest_setup AS (
    SELECT 
        sj.id as setup_id,
        sj.lot_id,
        l.lot_number,
        sj.status
    FROM setup_jobs sj
    JOIN lots l ON sj.lot_id = l.id
    WHERE l.lot_number = '2530699'
      AND sj.status IN ('created', 'started', 'pending_qc', 'allowed')  -- Только активные!
    ORDER BY sj.created_at DESC
    LIMIT 1
),
latest_reading AS (
    SELECT 
        mr.setup_job_id,
        mr.reading,
        mr.created_at,
        ROW_NUMBER() OVER (PARTITION BY mr.setup_job_id ORDER BY mr.created_at DESC) as rn
    FROM machine_readings mr
    JOIN latest_setup ls ON mr.setup_job_id = ls.setup_id
)
SELECT 
    ls.setup_id,
    ls.lot_id,
    ls.lot_number,
    ls.status as setup_status,
    lr.reading as actual_produced,
    lr.created_at as last_reading_time
FROM latest_setup ls
LEFT JOIN latest_reading lr ON ls.setup_id = lr.setup_job_id AND lr.rn = 1;

