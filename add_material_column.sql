-- Добавление колонки material в таблицу parts
-- Дата: 2024-12-23
-- Описание: Синхронизация продакшн базы с локальной разработкой

ALTER TABLE parts 
ADD COLUMN material TEXT;

-- Проверяем результат
\d parts; 