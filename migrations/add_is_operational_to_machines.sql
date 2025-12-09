-- Добавляем поле is_operational для отметки поломанных/неработающих станков
-- Станки с is_operational = false не будут предлагаться в рекомендациях

ALTER TABLE machines 
ADD COLUMN IF NOT EXISTS is_operational BOOLEAN DEFAULT TRUE NOT NULL;

COMMENT ON COLUMN machines.is_operational IS 'Станок в рабочем состоянии (false = поломан/на обслуживании)';

-- Индекс для быстрой фильтрации
CREATE INDEX IF NOT EXISTS idx_machines_is_operational ON machines(is_operational);

