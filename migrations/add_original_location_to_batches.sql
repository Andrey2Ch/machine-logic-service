-- ============================================
-- Миграция: Добавление original_location в batches
-- Дата: 2025-12-04
-- Описание: Сохраняет исходный статус батча ДО архивирования
--           для правильного учета батчей "на переборку" от операторов
-- ============================================

-- 1. Добавляем новое поле
ALTER TABLE batches 
  ADD COLUMN IF NOT EXISTS original_location VARCHAR(50);

-- 2. Добавляем комментарий
COMMENT ON COLUMN batches.original_location IS 
  'Исходный статус батча от оператора (сохраняется при архивировании для статистики)';

-- 3. Создаем индекс для ускорения запросов статистики
CREATE INDEX IF NOT EXISTS idx_batches_original_location 
  ON batches(original_location) 
  WHERE original_location IS NOT NULL;

-- 4. Создаем составной индекс для типичных запросов
CREATE INDEX IF NOT EXISTS idx_batches_parent_original_archived
  ON batches(parent_batch_id, original_location, current_location)
  WHERE current_location = 'archived';

-- 5. Заполняем поле для существующих батчей
-- Для активных батчей: original_location = current_location
UPDATE batches 
SET original_location = current_location
WHERE original_location IS NULL 
  AND current_location != 'archived';

-- 6. Для архивированных батчей пытаемся восстановить из дочерних
-- (если у родительского батча есть дочерние, берем их location)
UPDATE batches parent
SET original_location = (
  SELECT child.current_location 
  FROM batches child 
  WHERE child.parent_batch_id = parent.id 
  LIMIT 1
)
WHERE parent.current_location = 'archived'
  AND parent.parent_batch_id IS NULL
  AND parent.original_location IS NULL
  AND EXISTS (
    SELECT 1 FROM batches child 
    WHERE child.parent_batch_id = parent.id
  );

-- 7. Для остальных архивированных (без дочерних) ставим 'production' по умолчанию
UPDATE batches
SET original_location = 'production'
WHERE current_location = 'archived'
  AND original_location IS NULL;

-- ============================================
-- ПРОВЕРКА РЕЗУЛЬТАТА
-- ============================================

-- Проверяем распределение по original_location
SELECT 
  original_location,
  current_location,
  COUNT(*) as count,
  SUM(initial_quantity) as total_qty
FROM batches
GROUP BY original_location, current_location
ORDER BY original_location, current_location;

-- Проверяем батчи на переборку от операторов
SELECT 
  COUNT(*) as operator_rework_batches,
  SUM(initial_quantity) as operator_rework_qty
FROM batches
WHERE parent_batch_id IS NULL
  AND original_location IN ('sorting', 'pending_rework');

-- Проверяем батчи на переборку от ОТК (только из хороших)
SELECT 
  COUNT(*) as qc_rework_batches,
  SUM(child.current_quantity) as qc_rework_qty
FROM batches child
JOIN batches parent ON child.parent_batch_id = parent.id
WHERE child.current_location = 'pending_rework'
  AND parent.original_location IN ('production', 'good');

-- ============================================
-- ГОТОВО!
-- ============================================

