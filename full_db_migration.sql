-- Полная миграция для синхронизации продакшн базы с локальной
-- Дата: 2024-12-23
-- Описание: Добавление всех отсутствующих колонок и таблиц

-- ===============================
-- 1. ТАБЛИЦА PARTS (уже исправлена)
-- ===============================
-- ALTER TABLE parts ADD COLUMN IF NOT EXISTS material TEXT;

-- ===============================
-- 2. ТАБЛИЦА MACHINES
-- ===============================
-- Добавляем is_active если отсутствует
ALTER TABLE machines ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;

-- ===============================
-- 3. ТАБЛИЦА EMPLOYEES
-- ===============================
-- Добавляем is_active если отсутствует
ALTER TABLE employees ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;

-- ===============================
-- 4. ТАБЛИЦА LOTS - РАСШИРЕННЫЕ ПОЛЯ
-- ===============================
-- Поля для Order Manager
ALTER TABLE lots ADD COLUMN IF NOT EXISTS order_manager_id INTEGER REFERENCES employees(id);
ALTER TABLE lots ADD COLUMN IF NOT EXISTS created_by_order_manager_at TIMESTAMP;

-- Поля для планирования
ALTER TABLE lots ADD COLUMN IF NOT EXISTS due_date TIMESTAMP;
ALTER TABLE lots ADD COLUMN IF NOT EXISTS initial_planned_quantity INTEGER;
ALTER TABLE lots ADD COLUMN IF NOT EXISTS total_planned_quantity INTEGER;

-- Статус лота
ALTER TABLE lots ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'new' NOT NULL;

-- ===============================
-- 5. ТАБЛИЦА BATCHES - СКЛАДСКАЯ ЛОГИКА
-- ===============================
-- Колонки для количества
ALTER TABLE batches ADD COLUMN IF NOT EXISTS operator_reported_quantity INTEGER;
ALTER TABLE batches ADD COLUMN IF NOT EXISTS recounted_quantity INTEGER;

-- Колонки для сотрудников
ALTER TABLE batches ADD COLUMN IF NOT EXISTS warehouse_employee_id INTEGER REFERENCES employees(id);
ALTER TABLE batches ADD COLUMN IF NOT EXISTS qc_inspector_id INTEGER REFERENCES employees(id);

-- Временные метки
ALTER TABLE batches ADD COLUMN IF NOT EXISTS warehouse_received_at TIMESTAMP;
ALTER TABLE batches ADD COLUMN IF NOT EXISTS qa_date TIMESTAMP;

-- Комментарии ОТК
ALTER TABLE batches ADD COLUMN IF NOT EXISTS qc_comment TEXT;

-- Расхождения при приемке складом
ALTER TABLE batches ADD COLUMN IF NOT EXISTS discrepancy_absolute INTEGER;
ALTER TABLE batches ADD COLUMN IF NOT EXISTS discrepancy_percentage DECIMAL;
ALTER TABLE batches ADD COLUMN IF NOT EXISTS admin_acknowledged_discrepancy BOOLEAN DEFAULT FALSE NOT NULL;

-- Метки времени обновления
ALTER TABLE batches ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

-- Родительский батч для разделения
ALTER TABLE batches ADD COLUMN IF NOT EXISTS parent_batch_id INTEGER REFERENCES batches(id);

-- ===============================
-- 6. ТАБЛИЦА SETUP_JOBS
-- ===============================
-- Дополнительное количество
ALTER TABLE setup_jobs ADD COLUMN IF NOT EXISTS additional_quantity INTEGER DEFAULT 0;

-- ===============================
-- 7. ТАБЛИЦА CARDS (может полностью отсутствовать)
-- ===============================
CREATE TABLE IF NOT EXISTS cards (
    card_number INTEGER NOT NULL,
    machine_id BIGINT NOT NULL REFERENCES machines(id),
    status VARCHAR(20) NOT NULL DEFAULT 'free',
    batch_id BIGINT REFERENCES batches(id),
    last_event TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (card_number, machine_id),
    CONSTRAINT check_card_status CHECK (status IN ('free', 'in_use', 'lost'))
);

-- Создаем индексы для таблицы cards
CREATE INDEX IF NOT EXISTS idx_cards_machine_status ON cards(machine_id, status);
CREATE INDEX IF NOT EXISTS idx_cards_batch_id ON cards(batch_id);

-- ===============================
-- ОБНОВЛЯЕМ ФУНКЦИИ ДЛЯ АВТООБНОВЛЕНИЯ UPDATED_AT
-- ===============================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
   NEW.updated_at = NOW(); 
   RETURN NEW;
END;
$$ language 'plpgsql';

-- Создаем триггер для batches если его нет
DROP TRIGGER IF EXISTS update_batches_updated_at ON batches;
CREATE TRIGGER update_batches_updated_at 
    BEFORE UPDATE ON batches 
    FOR EACH ROW 
    EXECUTE FUNCTION update_updated_at_column();

-- ===============================
-- ПРОВЕРЯЕМ РЕЗУЛЬТАТ
-- ===============================
\echo 'Миграция завершена. Проверяем структуру таблиц:'
\d parts
\d lots  
\d batches
\d cards
\d machines
\d employees 