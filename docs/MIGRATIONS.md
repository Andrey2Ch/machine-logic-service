## Миграции БД — обязательно прочитать

Этот проект использует SQL-миграции из папки `migrations/`.

### Правила
- Каждый новый файл миграции должен быть **idempotent** (`IF NOT EXISTS`).
- В конце каждой миграции обязателен лог в `schema_migrations`:
  ```
  INSERT INTO schema_migrations (version, applied_at)
  VALUES ('XXX_migration_name', NOW())
  ON CONFLICT (version) DO NOTHING;
  ```
- После применения миграций **обновляем** `docs/schema_docs.md`.

### Чеклист при добавлении миграции
1. Создать SQL-файл в `migrations/` (idempotent, с записью в `schema_migrations`)
2. Применить SQL на БД (Appsmith или psql)
3. Обновить **оба** `schema_docs.md` — запустить `python scripts/refresh_schema_docs.py`
   - `docs/schema_docs.md` — документация для разработчиков
   - `src/text2sql/docs/schema_docs.md` — **контекст для AI-ассистента**
4. Проверить: `python scripts/verify_schema_docs.py`
5. Закоммитить миграцию + обновлённые `schema_docs.md` вместе

> **Pre-commit hook** автоматически блокирует коммит, если в `migrations/` есть изменения, а `schema_docs.md` не обновлён.

### Как применять миграции
Если применяете вручную (через Appsmith/psql) — запускайте SQL в порядке имен файлов.

### Как проверить, что все миграции применены
```bash
python scripts/check_migrations.py
```

### Автоматическая проверка перед PR/деплоем
Проверка файлов миграций без доступа к БД:
```bash
python scripts/verify_migration_files.py
```

Проверка, что schema_docs.md включает таблицы/колонки из миграций:
```bash
python scripts/verify_schema_docs.py
```

### Как обновить schema_docs.md
```bash
python scripts/refresh_schema_docs.py
```
