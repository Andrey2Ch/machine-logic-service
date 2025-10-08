# Логика синхронизации количеств между Lots и Setup_Jobs

## Проблема
Ранее количества дублировались и не синхронизировались между таблицами `lots` и `setup_jobs`, что приводило к расхождениям.

## Решение: Истина в Сетапах

### Источники истины

**Таблица `setup_jobs`:**
- `planned_quantity` - плановое количество (синхронизируется с `lot.initial`)
- `additional_quantity` - **ЕДИНСТВЕННЫЙ источник истины для дополнительного количества**

**Таблица `lots`:**
- `initial_planned_quantity` - плановое (можно менять)
- `total_planned_quantity` - хранится для совместимости и быстрого доступа:
  - Если сетап НЕ создан: `total = initial` (нет дополнительного)
  - Если сетап создан: `total = setup.planned + setup.additional` (вычисляется из сетапа)

### Правила работы

#### 1. До создания сетапа
- Можно свободно менять `lot.initial_planned_quantity`
- `lot.total_planned_quantity = lot.initial_planned_quantity`

#### 2. При создании сетапа
```python
setup.planned_quantity = lot.initial_planned_quantity
setup.additional_quantity = (lot.total - lot.initial) or 0
```

#### 3. После создания сетапа

**Изменение планового количества:**
```python
# Обновляем lot.initial
lot.initial_planned_quantity = новое_значение

# Синхронизируем сетап
setup.planned_quantity = новое_значение

# Пересчитываем total из сетапа
lot.total_planned_quantity = setup.planned_quantity + setup.additional_quantity
```

**Изменение дополнительного количества:**
```python
# Меняем ТОЛЬКО в сетапе (источник истины!)
setup.additional_quantity = новое_значение

# Пересчитываем total в лоте
lot.total_planned_quantity = setup.planned_quantity + setup.additional_quantity
```

### Реализация

#### Dashboard (Next.js)
- **Файл:** `src/app/api/lots/[id]/route.ts`
- **Логика:** При изменении `initial_planned_quantity` синхронизирует `setup.planned_quantity`

- **Файл:** `src/app/api/lots/[id]/setup/additional-quantity/route.ts`
- **Логика:** При изменении дополнительного количества обновляет `setup.additional_quantity` и пересчитывает `lot.total`

#### Machine Logic Service (FastAPI)
- **Файл:** `src/routers/lots.py`
- **Методы:**
  - `update_lot()` - синхронизирует при изменении `initial_planned_quantity`
  - `update_lot_quantity()` - обновляет `setup.additional_quantity`

#### TG Bot
- **Файл:** `database/connection.py`
- **Функция:** `save_setup_job()` - при создании сетапа копирует количества из лота

### Формулы

```
Для отображения везде:
total = initial + additional

Где брать:
- initial = lot.initial_planned_quantity (если есть сетап, должен быть = setup.planned_quantity)
- additional = setup.additional_quantity ЕСЛИ СЕТАП СУЩЕСТВУЕТ, иначе 0
- total = 
    если сетап существует: setup.planned + setup.additional
    если сетапа нет: lot.initial (дополнительного нет вообще)
```

**Важно:** `additional_quantity` существует **ТОЛЬКО** если создан сетап!
До создания сетапа это поле просто не существует (нет записи в `setup_jobs`).

### SQL запрос для отображения

```sql
SELECT 
  l.id,
  l.lot_number,
  l.initial_planned_quantity as initial,
  COALESCE(sj.additional_quantity, 0) as additional,
  CASE 
    WHEN sj.id IS NOT NULL 
    THEN sj.planned_quantity + COALESCE(sj.additional_quantity, 0)
    ELSE l.initial_planned_quantity
  END as total
FROM lots l
LEFT JOIN setup_jobs sj ON sj.lot_id = l.id AND sj.end_time IS NULL
WHERE sj.status IN ('created', 'started', 'pending_qc', 'allowed')
```

## Примеры

### Пример 1: Изменение планового до сетапа
```
1. Создали лот: initial=1000, total=1000
2. Изменили: initial=1500, total=1500
3. Создали сетап: planned=1500, additional=0
✅ Все синхронизировано
```

### Пример 2: Изменение планового после сетапа
```
1. Создали лот: initial=1000, total=1000
2. Создали сетап: planned=1000, additional=0
3. Изменили лот: initial=1500
   → setup.planned автоматически = 1500
   → lot.total = 1500 + 0 = 1500
✅ Все синхронизировано
```

### Пример 3: Добавление дополнительного
```
1. Создали лот: initial=1000, total=1000
2. Создали сетап: planned=1000, additional=0
3. Добавили доп.количество: additional=500
   → setup.additional = 500
   → lot.total = 1000 + 500 = 1500
✅ Все синхронизировано
```

## Миграция существующих данных

Используйте endpoint `/lots-management/backfill-total-planned` для пересчета всех `lot.total_planned_quantity` из сетапов.

```bash
POST /api/lots-management/backfill-total-planned
```

Это безопасно запускать многократно.

