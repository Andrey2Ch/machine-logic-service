-- Миграция: Добавление полей для назначения лотов на станки в канбан-доске
-- Дата: 2025-01-XX
-- Описание: Добавление полей assigned_machine_id и assigned_order для хранения назначения лотов на станки

-- Расширение таблицы lots
ALTER TABLE lots 
  ADD COLUMN IF NOT EXISTS assigned_machine_id INTEGER,
  ADD COLUMN IF NOT EXISTS assigned_order INTEGER;

-- Добавляем внешний ключ на таблицу machines
ALTER TABLE lots 
  ADD CONSTRAINT fk_lots_assigned_machine 
  FOREIGN KEY (assigned_machine_id) 
  REFERENCES machines(id) 
  ON DELETE SET NULL;

-- Добавляем индекс для быстрого поиска лотов по назначенному станку
CREATE INDEX IF NOT EXISTS idx_lots_assigned_machine_id ON lots(assigned_machine_id) WHERE assigned_machine_id IS NOT NULL;

-- Добавляем индекс для сортировки по порядку в очереди
CREATE INDEX IF NOT EXISTS idx_lots_assigned_order ON lots(assigned_machine_id, assigned_order) WHERE assigned_machine_id IS NOT NULL;

COMMENT ON COLUMN lots.assigned_machine_id IS 'ID станка, на который назначен лот (для статуса assigned)';
COMMENT ON COLUMN lots.assigned_order IS 'Порядок лота в очереди на станке (для сортировки в канбан-доске)';

