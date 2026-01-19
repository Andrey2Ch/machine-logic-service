-- Исправление: установить enabled_viewer = true для всех записей где оно NULL
-- Это нужно потому что при добавлении колонки старые записи могли получить NULL

UPDATE notification_settings 
SET enabled_viewer = true 
WHERE enabled_viewer IS NULL;

-- Также добавим явное значение по умолчанию
ALTER TABLE notification_settings 
ALTER COLUMN enabled_viewer SET DEFAULT true;

-- Убедимся что все языковые колонки имеют дефолты
UPDATE notification_settings 
SET language_viewer = 'he' 
WHERE language_viewer IS NULL;

-- Логируем результат
DO $$
DECLARE
    updated_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO updated_count 
    FROM notification_settings 
    WHERE enabled_viewer = true;
    
    RAISE NOTICE 'Viewer notifications enabled for % notification types', updated_count;
END $$;
