# AI Ассистент Isramat - Руководство по установке

## 📋 Что было создано

### 1. База знаний (Knowledge Base)

**Файлы:**
- `machine-logic-service/src/text2sql/docs/schema_docs.md` — **единый источник правды по схеме БД для AI** (отдаётся через `/ai/schema-docs`)
- `isramat-dashboard/src/lib/ai-assistant/knowledge/schema/tables.json` — дополнительная статическая справка (может отставать)
- `isramat-dashboard/src/lib/ai-assistant/knowledge/domain/glossary.md` — глоссарий терминов
- `isramat-dashboard/src/lib/ai-assistant/knowledge/domain/workflows.md` — бизнес-процессы
- **stoppage_reasons** — загружается из БД (таблица `stoppage_reasons`, миграция 037). Источник правды — БД.

### 2. SQL миграция

**Файл:** `machine-logic-service/migrations/021_ai_assistant_tables.sql`

**Таблицы:**
- `ai_knowledge_documents` — документы с embeddings (pgvector)
- `ai_conversations` — история диалогов
- `ai_messages` — сообщения в диалогах
- `ai_memory` — долгосрочная память AI
- `ai_sql_examples` — примеры SQL запросов
- `ai_feedback` — обратная связь пользователей

### 3. API Endpoints

**Файл:** `machine-logic-service/src/routers/ai.py`

| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/ai/search-knowledge` | POST | Поиск в базе знаний (vector similarity) |
| `/ai/search-sql-examples` | POST | Поиск похожих SQL примеров |
| `/ai/conversations` | POST | Создать беседу |
| `/ai/conversations/{id}/messages` | GET | Получить сообщения |
| `/ai/messages` | POST | Сохранить сообщение |
| `/ai/feedback` | POST | Отправить обратную связь |
| `/ai/memory` | GET/POST | Управление памятью |

### 4. Chat API

**Файл:** `isramat-dashboard/src/app/api/ai/chat/route.ts`

- Streaming ответы (SSE)
- Поддержка Claude Sonnet 4 и GPT-4o
- RAG: автоматический поиск релевантных документов
- Автоматическое выполнение SQL из ответа
- **Схема БД подтягивается из MLS**: `/ai/schema-docs` (с кешированием по `/ai/schema-docs-info`)

### 5. UI Чата

**Файл:** `isramat-dashboard/src/app/ai-assistant/page.tsx`

- Полноценный чат интерфейс
- Streaming отображение ответов
- Отображение SQL и результатов
- Подсказки для начала работы

### 6. Скрипт загрузки знаний

**Файл:** `machine-logic-service/scripts/load_ai_knowledge.py`

Загружает: schema, glossary, workflows, **stoppage_reasons (из БД)**, SQL примеры.

**План развития:** [AI_KNOWLEDGE_LOAD_PLAN.md](AI_KNOWLEDGE_LOAD_PLAN.md)

---

## 🚀 Установка

### Шаг 1: Установить pgvector

```bash
# PostgreSQL extension
CREATE EXTENSION IF NOT EXISTS vector;
```

Для Railway/Supabase это обычно уже включено.

### Шаг 2: Применить миграцию

```bash
cd machine-logic-service
psql $DATABASE_URL -f migrations/021_ai_assistant_tables.sql
```

### Шаг 3: Добавить API ключи

В `.env` или Railway environment:

```env
# Обязательно один из двух:
ANTHROPIC_API_KEY=sk-ant-...   # Для Claude
OPENAI_API_KEY=sk-...          # Для GPT-4o и embeddings
```

**Важно:** Для RAG и embeddings нужен `OPENAI_API_KEY`.

### Шаг 4: Установить зависимости Dashboard

```bash
cd isramat-dashboard
pnpm install
```

Добавлены пакеты:
- `@anthropic-ai/sdk`
- `openai`
- `react-markdown`

### Шаг 5: Загрузить базу знаний

```bash
cd machine-logic-service
export DATABASE_URL="postgresql://..."
export OPENAI_API_KEY="sk-..."
python scripts/load_ai_knowledge.py
```

Это создаст embeddings для всех документов.

### Шаг 6: Деплой

```bash
# Редеплой MLS для новых endpoints
# Редеплой Dashboard для нового UI
```

---

## 📱 Использование

1. Открыть `/ai-assistant` в Dashboard
2. Задать вопрос на русском языке
3. AI найдёт релевантные документы (RAG)
4. Сгенерирует SQL если нужно
5. Автоматически выполнит SQL и покажет результат

### Примеры вопросов:

- "Сколько машино-часов за декабрь?"
- "Топ-5 операторов по выработке"
- "Какие лоты сейчас в производстве?"
- "Покажи брак за последнюю неделю"
- "Статистика по станку SR-32"

---

## 🔧 Конфигурация

### System Prompt

В файле `isramat-dashboard/src/app/api/ai/chat/route.ts` системный промпт включает:
- **Схему БД (подгружается из MLS `/ai/schema-docs`)**
- Ключевых формул
- Статусов и их значений
- Правил ответа

### ВАЖНО: изменения БД и “знание схемы” у AI

Если меняешь БД (новые таблицы/колонки/индексы/миграции), нужно обновить **сразу**:

1) **Миграции** в `machine-logic-service/migrations/*.sql`  
2) **Schema docs для AI**: `machine-logic-service/src/text2sql/docs/schema_docs.md`  
   - либо вручную
   - либо через генератор (см. `machine-logic-service/src/text2sql/scripts/generate_schema_docs_from_prisma.py` / `.../generate_schema_docs.py`)
3) **Деплой MLS** (чтобы `/ai/schema-docs` отдавал новую схему)

### RAG параметры

```typescript
// В route.ts
const threshold = 0.6;  // Минимальное сходство для документов
const limit = 5;        // Количество документов в контексте
```

### Провайдеры AI

По умолчанию используется Anthropic (Claude). Если `ANTHROPIC_API_KEY` не задан — fallback на OpenAI.

---

## 🔮 Дальнейшее развитие

### TODO:

1. **Сохранение истории диалогов** — сейчас не персистятся
2. **Суммаризация длинных диалогов** — для экономии контекста
3. **Self-learning** — добавление новых SQL примеров из удачных запросов
4. **Голосовой ввод** — интеграция с Whisper
5. **Экспорт отчётов** — генерация PDF/Excel из ответов
6. **Alerting** — AI анализирует данные и предупреждает о проблемах

---

## 🐛 Troubleshooting

### pgvector не работает

```sql
-- Проверить extension
SELECT * FROM pg_extension WHERE extname = 'vector';

-- Установить если нет
CREATE EXTENSION vector;
```

### Embeddings не создаются

- Проверить `OPENAI_API_KEY`
- Проверить лимиты API

### Медленный поиск

```sql
-- Создать IVFFlat индекс
CREATE INDEX IF NOT EXISTS idx_ai_knowledge_embedding 
ON ai_knowledge_documents USING ivfflat (embedding vector_cosine_ops) 
WITH (lists = 100);
```

### SQL не выполняется

- Проверить endpoint `/sql/execute` в MLS
- Проверить права доступа к таблицам
