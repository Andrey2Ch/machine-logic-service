-- Миграция: Переименование станков для консистентности с MTConnect
-- Дата: 2026-01-14
-- Причина: TG бот отправляет sync_counter_to_mtconnect с именем из PostgreSQL,
--          но MTConnect хранит данные под другими именами, что приводит к 
--          созданию дубликатов вместо обновления существующих записей.

-- 1. B-38 -> BT-38 (ADAM станок)
UPDATE public.machines SET name = 'BT-38' WHERE name = 'B-38';

-- 2. D-26 -> DT-26 (FANUC станок)
UPDATE public.machines SET name = 'DT-26' WHERE name = 'D-26';

-- 3. K-16-2 -> K-162 (ADAM станок)
UPDATE public.machines SET name = 'K-162' WHERE name = 'K-16-2';

-- 4. K-16-3 -> K-163 (ADAM станок)
UPDATE public.machines SET name = 'K-163' WHERE name = 'K-16-3';

-- Проверка результата:
-- SELECT id, name FROM machines WHERE name IN ('BT-38', 'DT-26', 'K-162', 'K-163');
