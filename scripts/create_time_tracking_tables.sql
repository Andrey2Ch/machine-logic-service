-- Система учета рабочего времени
-- Создание таблиц для фиксации входа/выхода сотрудников

-- Таблица записей времени (основная)
CREATE TABLE IF NOT EXISTS time_entries (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    entry_type VARCHAR(10) NOT NULL CHECK (entry_type IN ('check_in', 'check_out')),
    entry_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    
    -- Метод фиксации
    method VARCHAR(20) NOT NULL CHECK (method IN ('telegram', 'terminal', 'web', 'manual')),
    
    -- Данные геолокации (для Telegram)
    latitude DECIMAL(10, 8),
    longitude DECIMAL(11, 8),
    location_accuracy DECIMAL(10, 2),
    is_location_valid BOOLEAN DEFAULT true,
    
    -- Данные терминала
    terminal_device_id VARCHAR(255),
    face_confidence DECIMAL(5, 2), -- уверенность распознавания лица (0-100)
    
    -- Offline синхронизация
    client_timestamp TIMESTAMP WITH TIME ZONE, -- время на устройстве
    synced_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(), -- время синхронизации
    
    -- Корректировки
    is_manual_correction BOOLEAN DEFAULT false,
    corrected_by INTEGER REFERENCES employees(id),
    correction_reason TEXT,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Таблица устройств-терминалов
CREATE TABLE IF NOT EXISTS terminals (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(255) UNIQUE NOT NULL,
    device_name VARCHAR(255) NOT NULL,
    location_description TEXT,
    is_active BOOLEAN DEFAULT true,
    last_seen_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Таблица фото лиц для распознавания
CREATE TABLE IF NOT EXISTS face_embeddings (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    embedding BYTEA NOT NULL, -- сериализованный вектор лица
    photo_url TEXT, -- ссылка на фото для администрирования
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_active BOOLEAN DEFAULT true
);

-- Таблица смен (для расчета часов)
CREATE TABLE IF NOT EXISTS work_shifts (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    shift_date DATE NOT NULL,
    check_in_time TIMESTAMP WITH TIME ZONE,
    check_out_time TIMESTAMP WITH TIME ZONE,
    total_hours DECIMAL(5, 2),
    status VARCHAR(20) CHECK (status IN ('complete', 'incomplete', 'absent', 'corrected')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(employee_id, shift_date)
);

-- Индексы для производительности
CREATE INDEX IF NOT EXISTS idx_time_entries_employee_time ON time_entries(employee_id, entry_time DESC);
CREATE INDEX IF NOT EXISTS idx_time_entries_entry_time ON time_entries(entry_time);
CREATE INDEX IF NOT EXISTS idx_work_shifts_employee_date ON work_shifts(employee_id, shift_date DESC);
CREATE INDEX IF NOT EXISTS idx_face_embeddings_employee ON face_embeddings(employee_id) WHERE is_active = true;

-- Комментарии к таблицам
COMMENT ON TABLE time_entries IS 'Записи входа/выхода сотрудников с поддержкой разных методов фиксации';
COMMENT ON TABLE terminals IS 'Зарегистрированные терминалы для фиксации времени';
COMMENT ON TABLE face_embeddings IS 'Векторы лиц для распознавания на терминалах';
COMMENT ON TABLE work_shifts IS 'Агрегированные данные по сменам для быстрого расчета часов';

-- Комментарии к важным колонкам
COMMENT ON COLUMN time_entries.entry_type IS 'Тип записи: check_in (вход) или check_out (выход)';
COMMENT ON COLUMN time_entries.method IS 'Способ фиксации: telegram, terminal, web, manual';
COMMENT ON COLUMN time_entries.is_location_valid IS 'Валидна ли геолокация (в пределах радиуса завода)';
COMMENT ON COLUMN time_entries.face_confidence IS 'Уверенность распознавания лица в процентах (0-100)';
COMMENT ON COLUMN time_entries.client_timestamp IS 'Время на устройстве клиента (для offline синхронизации)';
COMMENT ON COLUMN work_shifts.total_hours IS 'Общее количество отработанных часов за смену';

