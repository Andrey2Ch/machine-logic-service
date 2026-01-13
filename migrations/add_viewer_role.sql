-- Добавляем роль Viewer (ID 7) для уведомлений
-- Viewer - это наблюдатели, которые получают информационные уведомления

ALTER TABLE notification_settings 
ADD COLUMN IF NOT EXISTS enabled_viewer BOOLEAN DEFAULT true;

-- Добавляем выбор языка для каждой роли
-- ru = русский, he = иврит, en = английский, ar = арабский
ALTER TABLE notification_settings 
ADD COLUMN IF NOT EXISTS language_machinists VARCHAR(5) DEFAULT 'ru',
ADD COLUMN IF NOT EXISTS language_operators VARCHAR(5) DEFAULT 'ru',
ADD COLUMN IF NOT EXISTS language_qa VARCHAR(5) DEFAULT 'ru',
ADD COLUMN IF NOT EXISTS language_admin VARCHAR(5) DEFAULT 'ru',
ADD COLUMN IF NOT EXISTS language_viewer VARCHAR(5) DEFAULT 'he';

COMMENT ON COLUMN notification_settings.enabled_viewer IS 'Включено ли уведомление для Viewer (ID 7)';
COMMENT ON COLUMN notification_settings.language_machinists IS 'Язык уведомлений для наладчиков';
COMMENT ON COLUMN notification_settings.language_operators IS 'Язык уведомлений для операторов';
COMMENT ON COLUMN notification_settings.language_qa IS 'Язык уведомлений для ОТК';
COMMENT ON COLUMN notification_settings.language_admin IS 'Язык уведомлений для админов';
COMMENT ON COLUMN notification_settings.language_viewer IS 'Язык уведомлений для наблюдателей';
