-- Диагностика лота 2530694-1
-- Проверяем почему статус не синхронизирован

-- 1. Основная информация о лоте
SELECT 
    l.id,
    l.lot_number,
    l.status as lot_status,
    l.assigned_machine_id,
    m.name as machine_name,
    l.drawing_number,
    l.initial_planned_quantity,
    l.total_planned_quantity,
    l.created_at as lot_created
FROM lots l
LEFT JOIN machines m ON m.id = l.assigned_machine_id
WHERE l.lot_number = '2530694-1';

-- 2. Все наладки для этого лота
SELECT 
    sj.id as setup_id,
    sj.status as setup_status,
    sj.machine_id,
    m.name as machine_name,
    sj.planned_quantity,
    sj.actual_quantity,
    sj.cycle_time,
    sj.created_at as setup_created,
    sj.updated_at as setup_updated,
    e.full_name as operator_name
FROM setup_jobs sj
LEFT JOIN machines m ON m.id = sj.machine_id
LEFT JOIN employees e ON e.id = sj.operator_id
WHERE sj.lot_id = (SELECT id FROM lots WHERE lot_number = '2530694-1')
ORDER BY sj.created_at DESC;

-- 3. История изменений статуса наладок (если есть логи)
-- Проверяем активные наладки
SELECT 
    sj.id,
    sj.status,
    sj.created_at,
    sj.updated_at,
    CASE 
        WHEN sj.status IN ('created', 'started', 'pending_qc', 'allowed', 'queued') THEN 'ACTIVE'
        ELSE 'INACTIVE'
    END as is_active
FROM setup_jobs sj
WHERE sj.lot_id = (SELECT id FROM lots WHERE lot_number = '2530694-1')
ORDER BY sj.created_at DESC;

-- 4. Последние показания со станка для этого лота
SELECT 
    mr.id,
    mr.machine_id,
    m.name as machine_name,
    mr.setup_job_id,
    mr.part_count,
    mr.timestamp,
    mr.created_at
FROM machine_readings mr
LEFT JOIN machines m ON m.id = mr.machine_id
WHERE mr.setup_job_id IN (
    SELECT id FROM setup_jobs 
    WHERE lot_id = (SELECT id FROM lots WHERE lot_number = '2530694-1')
)
ORDER BY mr.timestamp DESC
LIMIT 10;

-- 5. Проверяем логику: должен ли лот быть в статусе in_production?
SELECT 
    l.lot_number,
    l.status as current_lot_status,
    COUNT(sj.id) as total_setups,
    COUNT(CASE WHEN sj.status IN ('created', 'started', 'pending_qc', 'allowed', 'queued') THEN 1 END) as active_setups,
    MAX(CASE WHEN sj.status = 'allowed' THEN 1 ELSE 0 END) as has_allowed_setup,
    MAX(CASE WHEN sj.status = 'started' THEN 1 ELSE 0 END) as has_started_setup,
    CASE 
        WHEN COUNT(CASE WHEN sj.status IN ('created', 'started', 'pending_qc', 'allowed') THEN 1 END) > 0 
        THEN 'SHOULD BE in_production'
        ELSE 'OK to be assigned/new'
    END as expected_status
FROM lots l
LEFT JOIN setup_jobs sj ON sj.lot_id = l.id
WHERE l.lot_number = '2530694-1'
GROUP BY l.id, l.lot_number, l.status;

-- 6. Ищем противоречия
SELECT 
    'PROBLEM: Lot is assigned but has active setup' as issue_type,
    l.lot_number,
    l.status as lot_status,
    sj.id as setup_id,
    sj.status as setup_status
FROM lots l
JOIN setup_jobs sj ON sj.lot_id = l.id
WHERE l.lot_number = '2530694-1'
  AND l.status IN ('new', 'assigned')
  AND sj.status IN ('created', 'started', 'pending_qc', 'allowed');

-- 7. Предлагаемое исправление (если нужно)
-- UNCOMMENT TO FIX:
-- UPDATE lots 
-- SET status = 'in_production'
-- WHERE lot_number = '2530694-1' 
--   AND status IN ('new', 'assigned')
--   AND EXISTS (
--     SELECT 1 FROM setup_jobs 
--     WHERE lot_id = lots.id 
--       AND status IN ('created', 'started', 'pending_qc', 'allowed')
--   );

