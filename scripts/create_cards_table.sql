-- Создание таблицы карточек для операторов
-- Безопасное создание: только если таблица не существует

CREATE TABLE IF NOT EXISTS cards (
    card_number INTEGER NOT NULL,                    -- номер на пластике (1-20)
    machine_id BIGINT NOT NULL,                      -- ID станка
    status VARCHAR(20) NOT NULL DEFAULT 'free',     -- статус: free, in_use, lost
    batch_id BIGINT NULL,                            -- ID батча (когда карточка используется)
    last_event TIMESTAMP NOT NULL DEFAULT NOW(),    -- время последнего события
    
    -- Составной первичный ключ
    PRIMARY KEY (card_number, machine_id),
    
    -- Внешние ключи
    FOREIGN KEY (machine_id) REFERENCES machines(id),
    FOREIGN KEY (batch_id) REFERENCES batches(id),
    
    -- Ограничения
    CHECK (status IN ('free', 'in_use', 'lost')),
    CHECK (card_number >= 1 AND card_number <= 20)
);

-- Создание индексов для производительности
CREATE INDEX IF NOT EXISTS idx_cards_machine_status ON cards(machine_id, status);
CREATE INDEX IF NOT EXISTS idx_cards_batch_id ON cards(batch_id) WHERE batch_id IS NOT NULL;

-- Заполнение начальными данными (20 карточек для каждого активного станка)
INSERT INTO cards (card_number, machine_id, status, last_event)
SELECT 
    generate_series(1, 20) as card_number,
    m.id as machine_id,
    'free' as status,
    NOW() as last_event
FROM machines m
WHERE m.is_active = true
ON CONFLICT (card_number, machine_id) DO NOTHING;  -- Избегаем дубликатов при повторном запуске

-- Проверка результата
SELECT 
    m.name as machine_name,
    COUNT(c.card_number) as cards_count,
    COUNT(CASE WHEN c.status = 'free' THEN 1 END) as free_cards
FROM machines m
LEFT JOIN cards c ON m.id = c.machine_id
WHERE m.is_active = true
GROUP BY m.id, m.name
ORDER BY m.name;

-- Общая статистика
SELECT 
    status,
    COUNT(*) as count
FROM cards
GROUP BY status
ORDER BY status;

-- Комментарии:
-- - Составной первичный ключ (card_number, machine_id) позволяет иметь карточки с одинаковыми номерами для разных станков
-- - Например: карточка #5 может быть у станка SR-32 и у станка SR-45
-- - Это упрощает печать и управление карточками
-- - Всего будет создано: количество_активных_станков × 20 карточек 