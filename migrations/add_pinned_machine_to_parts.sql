-- Миграция: Добавление закрепления деталей за станками
-- Дата: 2024-12-07
-- Описание: Позволяет закреплять детали за конкретными станками.
--           Если деталь закреплена, рекомендации показывают только этот станок.

-- Добавляем колонку pinned_machine_id
ALTER TABLE parts 
ADD COLUMN IF NOT EXISTS pinned_machine_id INTEGER REFERENCES machines(id);

-- Комментарий
COMMENT ON COLUMN parts.pinned_machine_id IS 'ID станка, за которым закреплена деталь (NULL = не закреплена)';

-- Индекс для быстрого поиска закреплённых деталей
CREATE INDEX IF NOT EXISTS idx_parts_pinned_machine ON parts(pinned_machine_id) WHERE pinned_machine_id IS NOT NULL;

