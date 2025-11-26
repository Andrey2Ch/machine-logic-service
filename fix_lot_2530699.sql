-- 🔧 Ручное исправление лота 2530699
-- Переводим лот из статуса 'assigned' в 'in_production'
-- Так как у него уже есть созданная наладка (setup_id = 4747)

-- 1. Проверяем текущий статус лота
SELECT 
    l.id,
    l.lot_number,
    l.status as lot_status,
    l.assigned_machine_id,
    sj.id as setup_id,
    sj.status as setup_status,
    m.name as machine_name
FROM lots l
LEFT JOIN setup_jobs sj ON sj.lot_id = l.id AND sj.status IN ('created', 'started', 'pending_qc', 'allowed', 'queued')
LEFT JOIN machines m ON m.id = l.assigned_machine_id
WHERE l.lot_number = '2530699';

-- 2. Обновляем статус лота на 'in_production'
UPDATE lots 
SET status = 'in_production'
WHERE lot_number = '2530699' 
  AND status = 'assigned'
RETURNING id, lot_number, status;

-- 3. Проверяем результат
SELECT 
    l.id,
    l.lot_number,
    l.status as lot_status,
    l.assigned_machine_id,
    sj.id as setup_id,
    sj.status as setup_status,
    m.name as machine_name,
    sj.created_at as setup_created_at
FROM lots l
LEFT JOIN setup_jobs sj ON sj.lot_id = l.id AND sj.status IN ('created', 'started', 'pending_qc', 'allowed', 'queued')
LEFT JOIN machines m ON m.id = l.assigned_machine_id
WHERE l.lot_number = '2530699';

