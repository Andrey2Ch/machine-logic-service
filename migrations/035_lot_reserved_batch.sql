-- 035: Добавляет поле reserved_batch_id в таблицу lots
-- Позволяет менеджеру привязать партию со склада к лоту (резервирование)

ALTER TABLE lots
  ADD COLUMN IF NOT EXISTS reserved_batch_id VARCHAR(255);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('035_lot_reserved_batch', NOW())
ON CONFLICT (version) DO NOTHING;
