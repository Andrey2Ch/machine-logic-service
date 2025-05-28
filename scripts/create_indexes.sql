-- Создание индексов для таблицы cards
CREATE INDEX IF NOT EXISTS idx_cards_machine_status ON cards(machine_id, status);
CREATE INDEX IF NOT EXISTS idx_cards_batch_id ON cards(batch_id) WHERE batch_id IS NOT NULL; 