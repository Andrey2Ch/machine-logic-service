# План модернизации Text2SQL

**Версия:** 1.0  
**Дата:** 2025-01-23  
**Статус:** Draft

---

## Резюме

Модернизация существующей Text2SQL системы для улучшения качества генерации SQL и удобства управления базой знаний. Основа — текущая реализация на Claude API + RAG-lite.

---

## Текущее состояние

### Что уже работает ✅
- Claude API интеграция (`llm_provider_claude.py`)
- Schema-aware валидация (`sql_validator.py`)
- Авто-ретрай при ошибке EXPLAIN
- Session history для контекста
- Few-shot примеры в Markdown
- Feedback endpoint для сохранения примеров
- Live-схема из `information_schema`

### Проблемы ⚠️
1. **Наивный отбор примеров** — keyword overlap вместо семантического поиска
2. **Примеры в файле** — `few_shot_examples.md` не масштабируется
3. **Нет UI для управления** — примеры добавляются вручную
4. **Нет метрик качества** — не отслеживается accuracy
5. **Нет кэширования** — повторные вопросы генерируют SQL заново

---

## Фазы модернизации

### Фаза 1: Семантический RAG (3-5 дней)

#### 1.1 Embeddings для примеров

**Задача:** Заменить keyword overlap на семантический поиск

**Компоненты:**
- `sentence-transformers` для генерации embeddings
- pgvector extension для PostgreSQL
- Индекс по embeddings

**Схема БД:**
```sql
-- Включить pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Таблица примеров
CREATE TABLE public.text2sql_examples (
    id BIGSERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    question_ru TEXT,
    question_en TEXT,
    question_he TEXT,
    sql TEXT NOT NULL,
    embedding vector(384),  -- all-MiniLM-L6-v2 dimension
    category TEXT,          -- 'machines', 'batches', 'operators', etc.
    difficulty TEXT,        -- 'simple', 'medium', 'complex'
    is_verified BOOLEAN DEFAULT FALSE,
    usage_count INT DEFAULT 0,
    success_rate REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Индекс для семантического поиска
CREATE INDEX idx_text2sql_examples_embedding 
ON text2sql_examples USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

**Новый сервис:** `semantic_rag_service.py`

```python
from sentence_transformers import SentenceTransformer
from sqlalchemy import text
import numpy as np

class SemanticRAGService:
    def __init__(self, db, model_name: str = 'all-MiniLM-L6-v2'):
        self.db = db
        self.model = SentenceTransformer(model_name)
    
    def embed(self, text: str) -> list[float]:
        """Генерация embedding для текста"""
        return self.model.encode(text).tolist()
    
    def find_similar_examples(self, question: str, top_k: int = 6, 
                               category: str = None) -> list[tuple[str, str]]:
        """Семантический поиск похожих примеров"""
        q_embedding = self.embed(question)
        
        sql = """
            SELECT question, sql, 
                   1 - (embedding <=> :emb::vector) as similarity
            FROM text2sql_examples
            WHERE is_verified = true
        """
        params = {"emb": str(q_embedding)}
        
        if category:
            sql += " AND category = :cat"
            params["cat"] = category
        
        sql += " ORDER BY embedding <=> :emb::vector LIMIT :k"
        params["k"] = top_k
        
        rows = self.db.execute(text(sql), params).fetchall()
        return [(r.question, r.sql) for r in rows]
    
    def add_example(self, question: str, sql: str, category: str = None):
        """Добавление примера с embedding"""
        embedding = self.embed(question)
        self.db.execute(text("""
            INSERT INTO text2sql_examples (question, sql, embedding, category)
            VALUES (:q, :sql, :emb::vector, :cat)
        """), {"q": question, "sql": sql, "emb": str(embedding), "cat": category})
        self.db.commit()
```

#### 1.2 Миграция существующих примеров

**Скрипт:** `migrate_examples_to_db.py`

```python
"""Миграция примеров из Markdown в PostgreSQL с embeddings"""

import re
from src.text2sql.services.semantic_rag_service import SemanticRAGService

def parse_markdown_examples(md_path: str) -> list[tuple[str, str]]:
    """Парсинг существующих примеров из few_shot_examples.md"""
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    examples = []
    pattern = r'Q:\s*["\']?(.+?)["\']?\s*\nSQL:\s*\n```sql\n(.+?)```'
    for match in re.finditer(pattern, content, re.DOTALL):
        question = match.group(1).strip()
        sql = match.group(2).strip()
        examples.append((question, sql))
    
    return examples

def migrate(db_session):
    rag = SemanticRAGService(db_session)
    examples = parse_markdown_examples('src/text2sql/docs/few_shot_examples.md')
    
    for question, sql in examples:
        # Определение категории по ключевым словам
        category = detect_category(question, sql)
        rag.add_example(question, sql, category)
    
    print(f"Migrated {len(examples)} examples")

def detect_category(question: str, sql: str) -> str:
    q = question.lower() + sql.lower()
    if 'станк' in q or 'machine' in q:
        return 'machines'
    elif 'батч' in q or 'batch' in q:
        return 'batches'
    elif 'оператор' in q or 'employee' in q:
        return 'operators'
    elif 'карточ' in q or 'card' in q:
        return 'cards'
    return 'general'
```

---

### Фаза 2: Admin UI для примеров (2-3 дня)

#### 2.1 API эндпоинты

**Файл:** `routers/examples.py`

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from src.database import get_db_session
from src.text2sql.services.semantic_rag_service import SemanticRAGService

router = APIRouter(prefix="/api/text2sql/examples", tags=["text2sql-admin"])

class ExampleCreate(BaseModel):
    question: str
    sql: str
    category: str | None = None

class ExampleUpdate(BaseModel):
    question: str | None = None
    sql: str | None = None
    category: str | None = None
    is_verified: bool | None = None

@router.get("/")
def list_examples(
    skip: int = 0, 
    limit: int = 50,
    category: str = None,
    verified_only: bool = False,
    db = Depends(get_db_session)
):
    """Список всех примеров с фильтрацией"""
    sql = "SELECT * FROM text2sql_examples WHERE 1=1"
    params = {}
    
    if category:
        sql += " AND category = :cat"
        params["cat"] = category
    if verified_only:
        sql += " AND is_verified = true"
    
    sql += " ORDER BY created_at DESC LIMIT :limit OFFSET :skip"
    params["limit"] = limit
    params["skip"] = skip
    
    rows = db.execute(text(sql), params).fetchall()
    return {"items": [dict(r._mapping) for r in rows]}

@router.post("/")
def create_example(item: ExampleCreate, db = Depends(get_db_session)):
    """Создание примера с автоматическим embedding"""
    rag = SemanticRAGService(db)
    rag.add_example(item.question, item.sql, item.category)
    return {"status": "created"}

@router.put("/{example_id}")
def update_example(example_id: int, item: ExampleUpdate, db = Depends(get_db_session)):
    """Обновление примера (пересчёт embedding при изменении question)"""
    # ... update logic with re-embedding if question changed
    pass

@router.delete("/{example_id}")
def delete_example(example_id: int, db = Depends(get_db_session)):
    """Удаление примера"""
    db.execute(text("DELETE FROM text2sql_examples WHERE id = :id"), {"id": example_id})
    db.commit()
    return {"status": "deleted"}

@router.post("/{example_id}/verify")
def verify_example(example_id: int, db = Depends(get_db_session)):
    """Отметка примера как проверенного"""
    db.execute(text("""
        UPDATE text2sql_examples SET is_verified = true, updated_at = NOW()
        WHERE id = :id
    """), {"id": example_id})
    db.commit()
    return {"status": "verified"}
```

#### 2.2 Frontend (Next.js страница)

**Путь:** `isramat-dashboard/src/app/sql/examples/page.tsx`

**Функционал:**
- Таблица всех примеров с пагинацией
- Фильтрация по категории, статусу verified
- Создание/редактирование/удаление примеров
- Bulk import из CSV/JSON
- Тестирование примера (выполнение SQL)

---

### Фаза 3: Метрики и обратная связь (2-3 дня)

#### 3.1 Расширение истории

```sql
ALTER TABLE text2sql_history ADD COLUMN IF NOT EXISTS 
    feedback TEXT;  -- 'positive', 'negative', NULL
ALTER TABLE text2sql_history ADD COLUMN IF NOT EXISTS 
    feedback_comment TEXT;
ALTER TABLE text2sql_history ADD COLUMN IF NOT EXISTS 
    execution_time_ms INT;
ALTER TABLE text2sql_history ADD COLUMN IF NOT EXISTS 
    row_count INT;
```

#### 3.2 Feedback API

```python
@router.post("/history/{history_id}/feedback")
def submit_feedback(history_id: int, feedback: str, comment: str = None):
    """Сохранение обратной связи по запросу"""
    # Если feedback='positive', автоматически добавить в примеры
    pass
```

#### 3.3 Dashboard метрик

**Метрики:**
- Общее количество запросов / день
- Success rate (валидные SQL)
- Среднее время генерации
- Топ-10 популярных категорий вопросов
- Примеры с низким success rate (для улучшения)

---

### Фаза 4: Кэширование и оптимизация (1-2 дня)

#### 4.1 Кэш частых запросов

```sql
CREATE TABLE public.text2sql_cache (
    id BIGSERIAL PRIMARY KEY,
    question_hash TEXT UNIQUE NOT NULL,
    question TEXT NOT NULL,
    sql TEXT NOT NULL,
    embedding vector(384),
    hit_count INT DEFAULT 1,
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP DEFAULT NOW() + INTERVAL '7 days'
);

CREATE INDEX idx_cache_embedding ON text2sql_cache 
USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);
```

#### 4.2 Логика кэширования

```python
def get_cached_or_generate(question: str, similarity_threshold: float = 0.95):
    """Поиск в кэше по семантическому сходству"""
    q_emb = embed(question)
    
    cached = db.execute(text("""
        SELECT sql, 1 - (embedding <=> :emb::vector) as sim
        FROM text2sql_cache
        WHERE expires_at > NOW()
        ORDER BY embedding <=> :emb::vector
        LIMIT 1
    """), {"emb": str(q_emb)}).fetchone()
    
    if cached and cached.sim >= similarity_threshold:
        # Cache hit
        db.execute(text("""
            UPDATE text2sql_cache SET hit_count = hit_count + 1 WHERE id = :id
        """))
        return cached.sql
    
    # Cache miss - generate new
    return None
```

---

## Архитектура после модернизации

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Text2SQL v2.0                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    ┌──────────────┐    ┌─────────────────┐                │
│  │   Frontend  │───▶│   API Layer  │───▶│  Semantic RAG   │                │
│  │  (Next.js)  │    │   (FastAPI)  │    │   (pgvector)    │                │
│  └─────────────┘    └──────────────┘    └─────────────────┘                │
│         │                  │                    │                           │
│         │                  ▼                    ▼                           │
│         │          ┌──────────────┐    ┌─────────────────┐                │
│         │          │   Validator  │    │   Embeddings    │                │
│         │          │ (schema-aware)│    │  (sentence-tf)  │                │
│         │          └──────────────┘    └─────────────────┘                │
│         │                  │                    │                           │
│         │                  ▼                    ▼                           │
│         │          ┌──────────────┐    ┌─────────────────┐                │
│         └─────────▶│  Claude API  │◀───│   Cache Layer   │                │
│                    │  (LLM Gen)   │    │   (pgvector)    │                │
│                    └──────────────┘    └─────────────────┘                │
│                            │                                               │
│                            ▼                                               │
│                    ┌──────────────┐                                        │
│                    │  PostgreSQL  │                                        │
│                    │  (execute)   │                                        │
│                    └──────────────┘                                        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Зависимости

### Python (machine-logic-service)
```txt
sentence-transformers>=2.2.2
pgvector>=0.2.0
```

### PostgreSQL
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### Модель embeddings
- `all-MiniLM-L6-v2` — 384 dimensions, ~80MB, быстрая
- Альтернатива: `paraphrase-multilingual-MiniLM-L12-v2` для лучшей поддержки русского

---

## Приоритет задач

| # | Задача | Сложность | Влияние | Приоритет |
|---|--------|-----------|---------|-----------|
| 1 | pgvector + таблица примеров | Low | High | P0 |
| 2 | SemanticRAGService | Medium | High | P0 |
| 3 | Миграция примеров из MD | Low | High | P0 |
| 4 | Интеграция в llm_query | Medium | High | P0 |
| 5 | API для примеров (CRUD) | Low | Medium | P1 |
| 6 | UI для управления примерами | Medium | Medium | P1 |
| 7 | Feedback система | Low | Medium | P2 |
| 8 | Dashboard метрик | Medium | Low | P2 |
| 9 | Кэширование | Medium | Medium | P2 |

---

## Оценка времени

| Фаза | Описание | Время |
|------|----------|-------|
| **Фаза 1** | Semantic RAG + миграция | 3-5 дней |
| **Фаза 2** | Admin UI | 2-3 дня |
| **Фаза 3** | Метрики и feedback | 2-3 дня |
| **Фаза 4** | Кэширование | 1-2 дня |
| **Всего** | | **8-13 дней** |

---

## Ожидаемые результаты

| Метрика | До | После |
|---------|-----|-------|
| Точность отбора примеров | ~60% | ~90% |
| Время управления примерами | 5-10 мин | 30 сек |
| Успешность генерации SQL | ~75% | ~90% |
| Повторные запросы (кэш) | 0% | ~30% cached |

---

## Риски и митигация

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| pgvector недоступен на Railway | Low | Использовать Supabase Vector или внешний vector store |
| Модель embeddings медленная | Low | Кэшировать embeddings, использовать batch inference |
| Большой размер модели | Medium | Использовать quantized версию или API embeddings (OpenAI) |

---

## Следующие шаги

1. [ ] Проверить поддержку pgvector на Railway PostgreSQL
2. [ ] Создать миграцию БД для таблицы примеров
3. [ ] Реализовать SemanticRAGService
4. [ ] Мигрировать существующие примеры
5. [ ] Интегрировать в текущий `llm_query` endpoint
6. [ ] Протестировать качество на реальных запросах




