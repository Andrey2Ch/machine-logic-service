-- Проверка синхронизации структуры базы данных
-- Дата: 2024-12-23
-- Описание: Скрипт для проверки различий между локальной и продакшн базой

-- 1. Проверяем все таблицы
\dt

-- 2. Проверяем структуру критических таблиц

-- Таблица parts (уже исправили material)
\d parts

-- Таблица lots (может не хватать новых полей)
\d lots

-- Таблица batches (много новых полей для складской логики)
\d batches

-- Таблица cards (может полностью отсутствовать)
\d cards

-- Таблица machines (проверим is_active)
\d machines

-- Таблица employees (проверим is_active)
\d employees

-- 3. Проверяем индексы
\di

-- 4. Проверяем ограничения
SELECT conname, contype, confrelid::regclass, conkey
FROM pg_constraint 
WHERE conrelid IN (
    SELECT oid FROM pg_class 
    WHERE relname IN ('parts', 'lots', 'batches', 'cards', 'machines', 'employees', 'setup_jobs', 'machine_readings')
);

-- 5. Проверяем внешние ключи
SELECT
    tc.table_name,
    tc.constraint_name,
    tc.constraint_type,
    kcu.column_name,
    ccu.table_name AS foreign_table_name,
    ccu.column_name AS foreign_column_name
FROM
    information_schema.table_constraints AS tc
    JOIN information_schema.key_column_usage AS kcu
      ON tc.constraint_name = kcu.constraint_name
      AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage AS ccu
      ON ccu.constraint_name = tc.constraint_name
      AND ccu.table_schema = tc.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
    AND tc.table_name IN ('parts', 'lots', 'batches', 'cards', 'machines', 'employees', 'setup_jobs', 'machine_readings')
ORDER BY tc.table_name; 