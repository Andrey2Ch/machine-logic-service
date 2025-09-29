# Text2SQL Module

Модуль для преобразования естественного языка в SQL запросы с использованием RAG (Retrieval Augmented Generation).

## Структура

```
text2sql/
├── services/           # Сервисы
│   ├── text2sql_service.py    # Основной сервис генерации SQL
│   └── text2sql_metrics.py    # Метрики качества (EX, Soft Accuracy)
├── routers/            # FastAPI роутеры
│   └── text2sql.py     # API эндпоинты
├── docs/               # Документация
│   ├── few_shot_examples.md      # 30 примеров NL->SQL
│   ├── analytics_views_plan.md   # План аналитических VIEW
│   └── schema_docs.md            # Автодокументация схемы БД
├── scripts/            # Утилиты
│   └── generate_schema_docs.py   # Генератор документации схемы
└── tests/              # Тесты
```

## API Endpoints

- `POST /api/text2sql/direct_query` - Выполнение NL запроса
- `GET /api/text2sql/evaluate` - Оценка качества на тестовых примерах

## Использование

### Backend (Python)
```python
from src.text2sql.services import Text2SQLService

# Генерация SQL
service = Text2SQLService(db_session)
result = service.answer("сколько открытых батчей?")
print(result['sql'])  # SELECT COUNT(*) as open_batches FROM batches WHERE status = 'open'
```

### Frontend (Next.js)
```typescript
// Страница: /sql/text2sql
// Компонент использует DashboardLayout
// API: http://localhost:8000/api/text2sql/direct_query
```

## Безопасность

- Только SELECT запросы
- Автоматический LIMIT 100
- Таймауты выполнения
- Denylist для опасных операций

## Метрики качества

- **EX (Exact Match)**: Точное совпадение SQL
- **Soft Accuracy**: Семантическое совпадение результатов

## Разработка

1. Добавление новых few-shot примеров: `docs/few_shot_examples.md`
2. Обновление схемы: `scripts/generate_schema_docs.py`
3. Тестирование: `GET /api/text2sql/evaluate`

## Модели Anthropic

Сервис использует последние модели Anthropic:

- **По умолчанию**: `claude-sonnet-4-20250514` (последняя версия Sonnet, май 2025)
- **Псевдонимы**: 
  - `opus` → `claude-opus-4-1-20250805`
  - `sonnet` → `claude-sonnet-4-20250514`

### Настройка модели

```bash
# Использовать Sonnet (по умолчанию, быстрее и дешевле)
export ANTHROPIC_MODEL="sonnet"

# Использовать Opus (самая мощная)
export ANTHROPIC_MODEL="opus"

# Использовать конкретную версию
export ANTHROPIC_MODEL="claude-sonnet-4-20250514"
```

## Статус

- ✅ MVP Backend Foundation
- ✅ MVP Frontend Integration  
- ✅ Семантический слой и RAG
- ✅ Обновлено до Claude Sonnet 4 (май 2025)
- 🔄 Production-готовность (в процессе)
