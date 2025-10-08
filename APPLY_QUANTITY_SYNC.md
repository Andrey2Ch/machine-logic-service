# Инструкция по применению изменений синхронизации количеств

## Что было изменено

✅ Dashboard (Next.js) - синхронизация при изменении `initial_planned_quantity`
✅ Machine Logic Service (FastAPI) - синхронизация в обоих API
✅ TG Bot - логика уже была правильной
✅ Документация - добавлена `QUANTITY_SYNC_LOGIC.md`

## Шаги для применения

### 1. Dashboard (isramat-dashboard)

```bash
cd C:\Projects\isramat-dashboard

# Пересобрать проект
npm run build

# Или запустить в dev режиме для тестирования
npm run dev
```

**Проверить:**
- Открыть https://isramat-dashboard-production.up.railway.app/order-management
- Изменить плановое количество лота (если есть сетап) → проверить что `setup.planned_quantity` тоже изменился
- Добавить дополнительное количество через "+" → проверить что `lot.total` обновился

### 2. Machine Logic Service (FastAPI)

```bash
cd C:\Projects\machine-logic-service

# Перезапустить сервис
# (если он запущен как systemd service или docker)
# или просто перезапустить процесс
```

**Проверить:**
- Тестировать API `/lots-management/{lot_id}` с обновлением `initial_planned_quantity`

### 3. Миграция существующих данных

Выполнить пересчет всех `total_planned_quantity` из сетапов:

```bash
# Через curl или Postman
POST https://your-api-url/lots-management/backfill-total-planned

# Или через SQL (если нужно)
UPDATE lots l
SET total_planned_quantity = (
  SELECT sj.planned_quantity + COALESCE(sj.additional_quantity, 0)
  FROM setup_jobs sj
  WHERE sj.lot_id = l.id 
    AND sj.end_time IS NULL
    AND sj.status IN ('created', 'started', 'pending_qc', 'allowed')
  LIMIT 1
)
WHERE EXISTS (
  SELECT 1 FROM setup_jobs sj 
  WHERE sj.lot_id = l.id AND sj.end_time IS NULL
);
```

### 4. TG Bot

**Ничего делать не нужно!** Логика уже была правильной.

## Проверка правильности работы

### Тест 1: Изменение планового до сетапа
1. Создать лот через Order Management с `initial=1000`
2. Изменить `initial=1500` через редактирование
3. Создать сетап через бота
4. **Ожидается:** `setup.planned_quantity = 1500`, `lot.total = 1500`

### Тест 2: Изменение планового после сетапа
1. Создать лот с `initial=1000`
2. Создать сетап (`planned=1000`)
3. Изменить лот `initial=1500`
4. **Ожидается:** `setup.planned_quantity = 1500`, `lot.total = 1500`

### Тест 3: Добавление дополнительного
1. Создать лот с `initial=1000`
2. Создать сетап
3. Добавить через "+" дополнительное `additional=500`
4. **Ожидается:** `setup.additional = 500`, `lot.total = 1500`

### Тест 4: Проверка лота 2530593
```sql
-- До изменений:
SELECT 
  l.id, l.lot_number,
  l.initial_planned_quantity, 
  l.total_planned_quantity,
  sj.planned_quantity,
  sj.additional_quantity
FROM lots l
JOIN setup_jobs sj ON sj.lot_id = l.id
WHERE l.lot_number = '2530593';

-- Изменить initial через Order Management на правильное значение
-- Проверить что setup.planned тоже изменился
```

## Важные SQL запросы для проверки

### Найти расхождения
```sql
-- Лоты где lot.initial != setup.planned
SELECT 
  l.id,
  l.lot_number,
  l.initial_planned_quantity as lot_initial,
  sj.planned_quantity as setup_planned,
  l.initial_planned_quantity - sj.planned_quantity as diff
FROM lots l
JOIN setup_jobs sj ON sj.lot_id = l.id
WHERE sj.end_time IS NULL
  AND l.initial_planned_quantity != sj.planned_quantity
ORDER BY ABS(l.initial_planned_quantity - sj.planned_quantity) DESC;

-- Лоты где lot.total != setup.total
SELECT 
  l.id,
  l.lot_number,
  l.total_planned_quantity as lot_total,
  (sj.planned_quantity + COALESCE(sj.additional_quantity, 0)) as setup_total,
  l.total_planned_quantity - (sj.planned_quantity + COALESCE(sj.additional_quantity, 0)) as diff
FROM lots l
JOIN setup_jobs sj ON sj.lot_id = l.id
WHERE sj.end_time IS NULL
  AND l.total_planned_quantity != (sj.planned_quantity + COALESCE(sj.additional_quantity, 0))
ORDER BY ABS(l.total_planned_quantity - (sj.planned_quantity + COALESCE(sj.additional_quantity, 0))) DESC;
```

## Откат изменений (если нужно)

```bash
cd C:\Projects\isramat-dashboard
git checkout HEAD -- src/app/api/lots/[id]/route.ts

cd C:\Projects\machine-logic-service
git checkout HEAD -- src/routers/lots.py
```

## Контакты

Если возникнут проблемы, проверьте логи:
- Dashboard: browser console + Railway logs
- Machine Logic Service: логи FastAPI
- TG Bot: `bot.log`, `setup_debug.log`

