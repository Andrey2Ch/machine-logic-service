-- Добавить 'auto' в допустимые значения method для автоматического закрытия смен
-- Выполнить вручную на production БД

-- Удаляем старый constraint
ALTER TABLE time_entries DROP CONSTRAINT IF EXISTS time_entries_method_check;

-- Создаём новый constraint с 'auto'
ALTER TABLE time_entries ADD CONSTRAINT time_entries_method_check 
    CHECK (method IN ('telegram', 'terminal', 'web', 'manual', 'auto'));

-- Обновляем комментарий
COMMENT ON COLUMN time_entries.method IS 'Способ фиксации: telegram, terminal, web, manual, auto (автоматическое закрытие)';

