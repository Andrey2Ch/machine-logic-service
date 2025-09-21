-- Создание таблицы для качественных примеров Text2SQL
CREATE TABLE IF NOT EXISTS text2sql_examples (
    id SERIAL PRIMARY KEY,
    normalized_sql TEXT NOT NULL,
    business_question_ru TEXT NOT NULL,
    business_question_en TEXT,
    table_names TEXT[],
    operation_type VARCHAR(10) CHECK (operation_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')),
    quality_score INTEGER DEFAULT 0 CHECK (quality_score >= 0 AND quality_score <= 10),
    source_captured_id INTEGER REFERENCES text2sql_captured(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_text2sql_examples_operation ON text2sql_examples(operation_type);
CREATE INDEX IF NOT EXISTS idx_text2sql_examples_quality ON text2sql_examples(quality_score);
CREATE INDEX IF NOT EXISTS idx_text2sql_examples_tables ON text2sql_examples USING GIN(table_names);

-- Комментарии
COMMENT ON TABLE text2sql_examples IS 'Качественные примеры для few-shot обучения Text2SQL';
COMMENT ON COLUMN text2sql_examples.normalized_sql IS 'SQL с нормализованными параметрами';
COMMENT ON COLUMN text2sql_examples.business_question_ru IS 'Реальный бизнес-вопрос на русском';
COMMENT ON COLUMN text2sql_examples.quality_score IS 'Оценка качества (0-10)';
