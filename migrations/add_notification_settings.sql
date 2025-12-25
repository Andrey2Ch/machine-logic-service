-- Таблица настроек WhatsApp уведомлений
-- Позволяет включать/выключать уведомления для разных групп

CREATE TABLE IF NOT EXISTS notification_settings (
    id SERIAL PRIMARY KEY,
    
    -- Тип уведомления (ключ)
    notification_type VARCHAR(50) NOT NULL UNIQUE,
    
    -- Название для отображения
    display_name VARCHAR(100) NOT NULL,
    
    -- Описание
    description TEXT,
    
    -- Категория для группировки в UI
    category VARCHAR(50) DEFAULT 'general',
    
    -- Включено ли уведомление для каждой группы
    enabled_machinists BOOLEAN DEFAULT true,
    enabled_operators BOOLEAN DEFAULT true,
    enabled_qa BOOLEAN DEFAULT true,
    enabled_admin BOOLEAN DEFAULT true,
    
    -- Telegram тоже можно контролировать
    enabled_telegram BOOLEAN DEFAULT true,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индекс для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_notification_settings_type ON notification_settings(notification_type);

-- Заполняем начальными данными
INSERT INTO notification_settings (notification_type, display_name, description, category) VALUES
    ('setup_allowed', 'Разрешение наладки', 'Уведомление когда ОТК разрешает наладку', 'setup'),
    ('setup_pending_qc', 'Передача в ОТК', 'Уведомление когда наладка отправлена на проверку ОТК', 'setup'),
    ('setup_completed', 'Завершение работы', 'Уведомление когда оператор завершает работу на станке', 'setup'),
    ('machine_free', 'Станок освободился', 'Уведомление наладчикам о свободном станке', 'machine'),
    ('defect_detected', 'Обнаружен брак', 'Уведомление о браке при производстве', 'quality'),
    ('lot_completed', 'Лот завершён', 'Уведомление о полном завершении партии', 'production'),
    ('reading_saved', 'Показания записаны', 'Уведомление о записи показаний оператором', 'production')
ON CONFLICT (notification_type) DO NOTHING;

-- Функция для автообновления updated_at
CREATE OR REPLACE FUNCTION update_notification_settings_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Триггер
DROP TRIGGER IF EXISTS trigger_update_notification_settings ON notification_settings;
CREATE TRIGGER trigger_update_notification_settings
    BEFORE UPDATE ON notification_settings
    FOR EACH ROW
    EXECUTE FUNCTION update_notification_settings_timestamp();

COMMENT ON TABLE notification_settings IS 'Настройки WhatsApp и Telegram уведомлений для разных групп';

