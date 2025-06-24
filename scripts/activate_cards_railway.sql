-- ===============================================
-- SQL СКРИПТ ДЛЯ АКТИВАЦИИ КАРТОЧЕК В RAILWAY
-- Запускать через Retool или другой PostgreSQL клиент
-- ===============================================

-- 1. СНАЧАЛА ПОСМОТРИ СПИСОК СТАНКОВ
SELECT 
    id,
    name,
    type,
    is_active
FROM machines 
WHERE is_active = true
ORDER BY name;

-- 2. ПРОВЕРЬ, СУЩЕСТВУЕТ ЛИ ТАБЛИЦА CARDS
SELECT EXISTS (
    SELECT FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_name = 'cards'
) as cards_table_exists;

-- 3. СОЗДАЙ ТАБЛИЦУ CARDS (ЕСЛИ НЕ СУЩЕСТВУЕТ)
CREATE TABLE IF NOT EXISTS cards (
    card_number INTEGER NOT NULL,
    machine_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'free',
    batch_id BIGINT NULL,
    last_event TIMESTAMP NOT NULL DEFAULT NOW(),
    
    PRIMARY KEY (card_number, machine_id),
    
    FOREIGN KEY (machine_id) REFERENCES machines(id),
    FOREIGN KEY (batch_id) REFERENCES batches(id),
    
    CHECK (status IN ('free', 'in_use', 'lost')),
    CHECK (card_number >= 1 AND card_number <= 20)
);

-- 4. СОЗДАЙ ИНДЕКСЫ
CREATE INDEX IF NOT EXISTS idx_cards_machine_status ON cards(machine_id, status);
CREATE INDEX IF NOT EXISTS idx_cards_batch_id ON cards(batch_id) WHERE batch_id IS NOT NULL;

-- ===============================================
-- АКТИВАЦИЯ КАРТОЧЕК ДЛЯ КОНКРЕТНОГО СТАНКА
-- ===============================================

-- РАБОЧИЙ ВАРИАНТ: Активация карточек с 1 по 5 (ИЗМЕНИ ИМЯ СТАНКА)
INSERT INTO cards (card_number, machine_id, status, last_event)
SELECT 
    generate_series(1, 5) as card_number,
    m.id as machine_id,
    'free' as status,
    NOW() as last_event
FROM machines m
WHERE m.name = 'SR-32'  -- ИЗМЕНИ НА НУЖНЫЙ СТАНОК
AND m.is_active = true
AND NOT EXISTS (
    SELECT 1 FROM cards c 
    WHERE c.machine_id = m.id 
    AND c.card_number BETWEEN 1 AND 5
);

-- ДОБАВИТЬ ЕЩЕ КАРТОЧКИ: с 6 по 10 (ИЗМЕНИ ИМЯ СТАНКА)
INSERT INTO cards (card_number, machine_id, status, last_event)
SELECT 
    generate_series(6, 10) as card_number,
    m.id as machine_id,
    'free' as status,
    NOW() as last_event
FROM machines m
WHERE m.name = 'SR-32'  -- ИЗМЕНИ НА НУЖНЫЙ СТАНОК
AND m.is_active = true
AND NOT EXISTS (
    SELECT 1 FROM cards c 
    WHERE c.machine_id = m.id 
    AND c.card_number BETWEEN 6 AND 10
);

-- АКТИВАЦИЯ ДЛЯ ДРУГОГО СТАНКА: 3 карточки (ИЗМЕНИ ИМЯ СТАНКА)
INSERT INTO cards (card_number, machine_id, status, last_event)
SELECT 
    generate_series(1, 3) as card_number,
    m.id as machine_id,
    'free' as status,
    NOW() as last_event
FROM machines m
WHERE m.name = 'XD-20'  -- ИЗМЕНИ НА НУЖНЫЙ СТАНОК
AND m.is_active = true
AND NOT EXISTS (
    SELECT 1 FROM cards c 
    WHERE c.machine_id = m.id 
    AND c.card_number BETWEEN 1 AND 3
);

-- ===============================================
-- ПРОВЕРКА РЕЗУЛЬТАТОВ
-- ===============================================

-- Проверь созданные карточки
SELECT 
    m.name as machine_name,
    m.id as machine_id,
    COUNT(c.card_number) as total_cards,
    COUNT(CASE WHEN c.status = 'free' THEN 1 END) as free_cards,
    COUNT(CASE WHEN c.status = 'in_use' THEN 1 END) as used_cards,
    COUNT(CASE WHEN c.status = 'lost' THEN 1 END) as lost_cards,
    STRING_AGG(c.card_number::text, ', ' ORDER BY c.card_number) as card_numbers
FROM machines m
LEFT JOIN cards c ON m.id = c.machine_id
WHERE m.is_active = true
GROUP BY m.id, m.name
HAVING COUNT(c.card_number) > 0
ORDER BY m.name;

-- Общая статистика
SELECT 
    status,
    COUNT(*) as count
FROM cards
GROUP BY status
ORDER BY status;

-- ===============================================
-- БЫСТРЫЕ КОМАНДЫ ДЛЯ КОПИРОВАНИЯ В RETOOL
-- ===============================================

-- Шаг 1: Посмотреть станки
-- SELECT id, name, type FROM machines WHERE is_active = true ORDER BY name;

-- Шаг 2: Создать таблицу (один раз)
-- CREATE TABLE IF NOT EXISTS cards (card_number INTEGER NOT NULL, machine_id BIGINT NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'free', batch_id BIGINT NULL, last_event TIMESTAMP NOT NULL DEFAULT NOW(), PRIMARY KEY (card_number, machine_id), FOREIGN KEY (machine_id) REFERENCES machines(id), FOREIGN KEY (batch_id) REFERENCES batches(id), CHECK (status IN ('free', 'in_use', 'lost')), CHECK (card_number >= 1 AND card_number <= 20));

-- Шаг 3: Создать индексы (один раз)
-- CREATE INDEX IF NOT EXISTS idx_cards_machine_status ON cards(machine_id, status);

-- Шаг 4: Активировать карточки (ИЗМЕНИ ИМЯ СТАНКА И ДИАПАЗОН)
-- INSERT INTO cards (card_number, machine_id, status, last_event) SELECT generate_series(1, 5) as card_number, m.id as machine_id, 'free' as status, NOW() as last_event FROM machines m WHERE m.name = 'SR-32' AND m.is_active = true AND NOT EXISTS (SELECT 1 FROM cards c WHERE c.machine_id = m.id AND c.card_number BETWEEN 1 AND 5);

-- Шаг 5: Проверить результат
-- SELECT m.name as machine_name, COUNT(c.card_number) as total_cards, STRING_AGG(c.card_number::text, ', ' ORDER BY c.card_number) as card_numbers FROM machines m LEFT JOIN cards c ON m.id = c.machine_id WHERE m.is_active = true AND c.card_number IS NOT NULL GROUP BY m.id, m.name ORDER BY m.name;

-- ===============================================
-- УТИЛИТЫ
-- ===============================================

-- Удалить все карточки станка (если нужно начать заново)
-- DELETE FROM cards WHERE machine_id = (SELECT id FROM machines WHERE name = 'SR-32');

-- Удалить конкретные карточки станка
-- DELETE FROM cards WHERE machine_id = (SELECT id FROM machines WHERE name = 'SR-32') AND card_number BETWEEN 6 AND 10; 