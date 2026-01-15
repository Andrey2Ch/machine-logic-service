-- Migration: AI Assistant Tables
-- Description: Tables for AI assistant knowledge base, conversations, and vector embeddings
-- Date: 2026-01-15

-- Enable pgvector extension for vector embeddings
CREATE EXTENSION IF NOT EXISTS vector;

-- ====================================================================
-- 1. AI Knowledge Base - документы базы знаний с embeddings
-- ====================================================================
CREATE TABLE IF NOT EXISTS ai_knowledge_documents (
    id SERIAL PRIMARY KEY,
    
    -- Идентификация
    document_type VARCHAR(50) NOT NULL,  -- 'schema', 'glossary', 'workflow', 'sql_example', 'code', 'faq'
    source_path VARCHAR(500),             -- Путь к исходному файлу (если есть)
    
    -- Контент
    title VARCHAR(500) NOT NULL,          -- Заголовок документа
    content TEXT NOT NULL,                -- Текстовое содержимое
    content_hash VARCHAR(64),             -- SHA-256 для детекции изменений
    
    -- Метаданные
    metadata JSONB DEFAULT '{}',          -- Дополнительные метаданные (tags, related_tables, etc.)
    
    -- Векторное представление
    embedding vector(1536),               -- OpenAI text-embedding-3-small output dimension
    
    -- Аудит
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE
);

-- Индексы для поиска
CREATE INDEX IF NOT EXISTS idx_ai_knowledge_type ON ai_knowledge_documents(document_type);
CREATE INDEX IF NOT EXISTS idx_ai_knowledge_active ON ai_knowledge_documents(is_active);
CREATE INDEX IF NOT EXISTS idx_ai_knowledge_metadata ON ai_knowledge_documents USING GIN(metadata);

-- Индекс для векторного поиска (IVFFlat - быстрее для небольших датасетов)
CREATE INDEX IF NOT EXISTS idx_ai_knowledge_embedding ON ai_knowledge_documents 
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);


-- ====================================================================
-- 2. AI Conversations - история диалогов
-- ====================================================================
CREATE TABLE IF NOT EXISTS ai_conversations (
    id SERIAL PRIMARY KEY,
    
    -- Идентификация
    session_id UUID NOT NULL DEFAULT gen_random_uuid(),  -- Уникальный ID сессии
    user_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    
    -- Метаданные сессии
    title VARCHAR(500),                   -- Заголовок беседы (автогенерируется)
    summary TEXT,                         -- Краткое содержание (автогенерируется)
    
    -- Статистика
    message_count INTEGER DEFAULT 0,
    token_count INTEGER DEFAULT 0,
    
    -- Аудит
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    is_archived BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_ai_conversations_session ON ai_conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_ai_conversations_user ON ai_conversations(user_id);


-- ====================================================================
-- 3. AI Messages - сообщения в диалогах
-- ====================================================================
CREATE TABLE IF NOT EXISTS ai_messages (
    id SERIAL PRIMARY KEY,
    
    -- Связь с беседой
    conversation_id INTEGER NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
    
    -- Контент
    role VARCHAR(20) NOT NULL,            -- 'user', 'assistant', 'system'
    content TEXT NOT NULL,                -- Текст сообщения
    
    -- Если assistant выполнил SQL
    executed_sql TEXT,                    -- SQL запрос (если был)
    sql_result JSONB,                     -- Результат выполнения
    sql_error TEXT,                       -- Ошибка (если была)
    
    -- RAG контекст
    context_documents JSONB,              -- IDs документов, использованных для ответа
    
    -- Метаданные
    model_used VARCHAR(100),              -- 'claude-sonnet-4', 'gpt-4o', etc.
    tokens_used INTEGER,
    response_time_ms INTEGER,
    
    -- Аудит
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON ai_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_ai_messages_role ON ai_messages(role);


-- ====================================================================
-- 4. AI Memory - долгосрочная память ассистента
-- ====================================================================
CREATE TABLE IF NOT EXISTS ai_memory (
    id SERIAL PRIMARY KEY,
    
    -- Тип памяти
    memory_type VARCHAR(50) NOT NULL,     -- 'user_preference', 'learned_rule', 'correction', 'fact'
    
    -- Контент
    content TEXT NOT NULL,                -- Что запомнить
    context TEXT,                         -- Контекст, когда это применимо
    
    -- Привязка к пользователю (опционально)
    user_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
    
    -- Приоритет и валидность
    priority INTEGER DEFAULT 5,           -- 1-10, выше = важнее
    expires_at TIMESTAMP,                 -- Когда устаревает (null = никогда)
    
    -- Аудит
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE,
    source_conversation_id INTEGER REFERENCES ai_conversations(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_memory_type ON ai_memory(memory_type);
CREATE INDEX IF NOT EXISTS idx_ai_memory_user ON ai_memory(user_id);
CREATE INDEX IF NOT EXISTS idx_ai_memory_active ON ai_memory(is_active);


-- ====================================================================
-- 5. AI SQL Examples - примеры SQL для обучения
-- ====================================================================
CREATE TABLE IF NOT EXISTS ai_sql_examples (
    id SERIAL PRIMARY KEY,
    
    -- Вопрос и ответ
    question TEXT NOT NULL,               -- Вопрос на естественном языке
    question_embedding vector(1536),      -- Embedding вопроса для поиска похожих
    sql_query TEXT NOT NULL,              -- SQL запрос
    
    -- Метаданные
    tables_used TEXT[],                   -- Какие таблицы используются
    difficulty VARCHAR(20),               -- 'simple', 'medium', 'complex'
    tags TEXT[],                          -- Теги для категоризации
    
    -- Статистика использования
    use_count INTEGER DEFAULT 0,
    success_rate FLOAT DEFAULT 1.0,       -- Процент успешных выполнений
    
    -- Аудит
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    is_verified BOOLEAN DEFAULT FALSE,    -- Проверен ли человеком
    created_by INTEGER REFERENCES employees(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_sql_examples_tags ON ai_sql_examples USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_ai_sql_examples_tables ON ai_sql_examples USING GIN(tables_used);
CREATE INDEX IF NOT EXISTS idx_ai_sql_examples_embedding ON ai_sql_examples 
    USING ivfflat (question_embedding vector_cosine_ops) WITH (lists = 100);


-- ====================================================================
-- 6. AI Feedback - обратная связь от пользователей
-- ====================================================================
CREATE TABLE IF NOT EXISTS ai_feedback (
    id SERIAL PRIMARY KEY,
    
    -- Связь с сообщением
    message_id INTEGER NOT NULL REFERENCES ai_messages(id) ON DELETE CASCADE,
    
    -- Оценка
    rating INTEGER CHECK (rating >= 1 AND rating <= 5),  -- 1-5 звёзд
    feedback_type VARCHAR(50),            -- 'helpful', 'incorrect', 'unclear', 'slow'
    comment TEXT,                         -- Комментарий пользователя
    
    -- Аудит
    created_at TIMESTAMP DEFAULT NOW(),
    user_id INTEGER REFERENCES employees(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_feedback_message ON ai_feedback(message_id);
CREATE INDEX IF NOT EXISTS idx_ai_feedback_rating ON ai_feedback(rating);


-- ====================================================================
-- 7. Функция для поиска похожих документов
-- ====================================================================
CREATE OR REPLACE FUNCTION search_ai_knowledge(
    query_embedding vector(1536),
    match_threshold FLOAT DEFAULT 0.7,
    match_count INTEGER DEFAULT 5
)
RETURNS TABLE (
    id INTEGER,
    document_type VARCHAR(50),
    title VARCHAR(500),
    content TEXT,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        d.id,
        d.document_type,
        d.title,
        d.content,
        1 - (d.embedding <=> query_embedding) as similarity
    FROM ai_knowledge_documents d
    WHERE d.is_active = TRUE
      AND 1 - (d.embedding <=> query_embedding) > match_threshold
    ORDER BY d.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;


-- ====================================================================
-- 8. Функция для поиска похожих SQL примеров
-- ====================================================================
CREATE OR REPLACE FUNCTION search_sql_examples(
    query_embedding vector(1536),
    match_threshold FLOAT DEFAULT 0.6,
    match_count INTEGER DEFAULT 3
)
RETURNS TABLE (
    id INTEGER,
    question TEXT,
    sql_query TEXT,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        e.id,
        e.question,
        e.sql_query,
        1 - (e.question_embedding <=> query_embedding) as similarity
    FROM ai_sql_examples e
    WHERE 1 - (e.question_embedding <=> query_embedding) > match_threshold
    ORDER BY e.question_embedding <=> query_embedding
    LIMIT match_count;
END;
$$;


-- ====================================================================
-- 9. Триггер для обновления updated_at
-- ====================================================================
CREATE OR REPLACE FUNCTION update_ai_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_ai_knowledge_updated
    BEFORE UPDATE ON ai_knowledge_documents
    FOR EACH ROW EXECUTE FUNCTION update_ai_updated_at();

CREATE TRIGGER tr_ai_conversations_updated
    BEFORE UPDATE ON ai_conversations
    FOR EACH ROW EXECUTE FUNCTION update_ai_updated_at();

CREATE TRIGGER tr_ai_memory_updated
    BEFORE UPDATE ON ai_memory
    FOR EACH ROW EXECUTE FUNCTION update_ai_updated_at();

CREATE TRIGGER tr_ai_sql_examples_updated
    BEFORE UPDATE ON ai_sql_examples
    FOR EACH ROW EXECUTE FUNCTION update_ai_updated_at();


-- ====================================================================
-- 10. Начальные данные - типы документов
-- ====================================================================
COMMENT ON TABLE ai_knowledge_documents IS 'База знаний AI-ассистента с векторными embeddings';
COMMENT ON TABLE ai_conversations IS 'История диалогов с AI-ассистентом';
COMMENT ON TABLE ai_messages IS 'Сообщения в диалогах';
COMMENT ON TABLE ai_memory IS 'Долгосрочная память AI (предпочтения, исправления)';
COMMENT ON TABLE ai_sql_examples IS 'Примеры SQL для text-to-SQL';
COMMENT ON TABLE ai_feedback IS 'Обратная связь от пользователей';

-- Готово!
-- Для применения миграции выполните:
-- psql -h <host> -U <user> -d <database> -f 021_ai_assistant_tables.sql
