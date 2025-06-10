 -- Проверка синхронизации структуры базы данных для Retool
-- Дата: 2024-12-23
-- Описание: Скрипт для проверки различий между локальной и продакшн базой (только SQL запросы)

-- 1. Проверяем все таблицы
SELECT 
    schemaname,
    tablename,
    tableowner
FROM pg_tables 
WHERE schemaname = 'public'
ORDER BY tablename;

-- 2. Проверяем колонки в критических таблицах

-- Таблица parts
SELECT 
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns 
WHERE table_name = 'parts' 
    AND table_schema = 'public'
ORDER BY ordinal_position;

-- Таблица lots
SELECT 
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns 
WHERE table_name = 'lots' 
    AND table_schema = 'public'
ORDER BY ordinal_position;

-- Таблица batches
SELECT 
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns 
WHERE table_name = 'batches' 
    AND table_schema = 'public'
ORDER BY ordinal_position;

-- Таблица cards (проверяем существует ли)
SELECT 
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns 
WHERE table_name = 'cards' 
    AND table_schema = 'public'
ORDER BY ordinal_position;

-- Таблица machines
SELECT 
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns 
WHERE table_name = 'machines' 
    AND table_schema = 'public'
ORDER BY ordinal_position;

-- Таблица employees
SELECT 
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns 
WHERE table_name = 'employees' 
    AND table_schema = 'public'
ORDER BY ordinal_position;