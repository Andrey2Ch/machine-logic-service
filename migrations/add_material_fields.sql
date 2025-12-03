-- ============================================
-- Миграция: Добавление отсутствующих колонок
-- Таблица: lot_materials
-- Дата: 2025-12-03
-- Описание: Добавляет defect_bars, closed_at, closed_by
--           и пересоздает generated column used_bars
-- ============================================

BEGIN;

-- 1. Добавить колонку defect_bars (бракованные прутки)
ALTER TABLE lot_materials 
ADD COLUMN IF NOT EXISTS defect_bars INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN lot_materials.defect_bars 
IS 'Количество бракованных/погнутых прутков';

-- 2. Добавить колонку closed_at (дата закрытия записи)
ALTER TABLE lot_materials 
ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP WITH TIME ZONE NULL;

COMMENT ON COLUMN lot_materials.closed_at 
IS 'Дата закрытия записи кладовщиком';

-- 3. Добавить колонку closed_by (кто закрыл)
ALTER TABLE lot_materials 
ADD COLUMN IF NOT EXISTS closed_by INTEGER NULL;

-- Добавить foreign key на employees
ALTER TABLE lot_materials 
ADD CONSTRAINT IF NOT EXISTS fk_lot_materials_closed_by 
FOREIGN KEY (closed_by) 
REFERENCES employees(id) 
ON DELETE SET NULL;

COMMENT ON COLUMN lot_materials.closed_by 
IS 'ID сотрудника, который закрыл запись';

-- 4. Пересоздать generated column used_bars (если существует)
ALTER TABLE lot_materials 
DROP COLUMN IF EXISTS used_bars;

ALTER TABLE lot_materials 
ADD COLUMN used_bars INTEGER 
GENERATED ALWAYS AS (
    COALESCE(issued_bars, 0) - 
    COALESCE(returned_bars, 0) - 
    COALESCE(defect_bars, 0)
) STORED;

COMMENT ON COLUMN lot_materials.used_bars 
IS 'Использованные прутки (issued - returned - defect). Вычисляется автоматически.';

COMMIT;

-- ============================================
-- Проверка после миграции:
-- ============================================

-- Проверить что колонки созданы:
SELECT 
    column_name, 
    data_type, 
    is_nullable,
    column_default
FROM information_schema.columns 
WHERE table_name = 'lot_materials' 
  AND column_name IN ('defect_bars', 'closed_at', 'closed_by', 'used_bars')
ORDER BY column_name;

-- Проверить foreign key:
SELECT 
    constraint_name,
    table_name,
    column_name,
    foreign_table_name,
    foreign_column_name
FROM information_schema.key_column_usage kcu
JOIN information_schema.table_constraints tc 
    ON kcu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND kcu.table_name = 'lot_materials'
  AND kcu.column_name = 'closed_by';

