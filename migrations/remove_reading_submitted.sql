-- Удаление неиспользуемого типа уведомления reading_submitted
-- Причина: уведомление никогда не отправлялось (код не был реализован)
-- и было бы слишком частым (каждый batch = уведомление)

DELETE FROM notification_settings 
WHERE notification_type = 'reading_submitted';

-- Проверка результата
SELECT notification_type, display_name 
FROM notification_settings 
ORDER BY category, notification_type;
