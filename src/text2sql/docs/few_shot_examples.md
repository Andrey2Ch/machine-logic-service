# Few-shot examples for Text2SQL

## Примеры вопросов и SQL запросов

### Работающие станки
Q: "Сколько станков сейчас работает?"
SQL:
```sql
SELECT COUNT(*) AS working_machines_count
FROM machines m
JOIN setup_jobs sj ON m.id = sj.machine_id
WHERE sj.status = 'started' AND sj.end_time IS NULL
```

Q: "Покажи все работающие станки"
SQL:
```sql
SELECT m.id, m.name, sj.start_time
FROM machines m
JOIN setup_jobs sj ON m.id = sj.machine_id
WHERE sj.status = 'started' AND sj.end_time IS NULL
```

### Свободные станки
Q: "Сколько станков свободно?"
SQL:
```sql
SELECT COUNT(*) AS free_machines_count
FROM machines m
LEFT JOIN setup_jobs sj ON m.id = sj.machine_id AND sj.status = 'started' AND sj.end_time IS NULL
WHERE sj.id IS NULL AND m.is_active = true
```

Q: "Покажи все свободные станки"
SQL:
```sql
SELECT m.id, m.name
FROM machines m
LEFT JOIN setup_jobs sj ON m.id = sj.machine_id AND sj.status = 'active' AND sj.end_time IS NULL
WHERE sj.id IS NULL AND m.is_active = true
```

### Поиск по имени станка
Q: "сколько деталей вчера сделал станок SR23?"
SQL:
```sql
SELECT SUM(b.initial_quantity - b.current_quantity) AS total_parts_produced
FROM batches b
JOIN setup_jobs sj ON b.setup_job_id = sj.id
JOIN machines m ON sj.machine_id = m.id
WHERE m.name LIKE '%SR23%'
  AND b.batch_time::date = CURRENT_DATE - INTERVAL '1 day'
```

### Батчи
Q: "Сколько открытых батчей?"
SQL:
```sql
SELECT COUNT(*) AS open_batches_count
FROM batches
WHERE current_quantity > 0
```

Q: "Покажи все открытые батчи"
SQL:
```sql
SELECT id, current_quantity, created_at
FROM batches
WHERE current_quantity > 0
```

Q: "Сколько деталей в батче 123?"
SQL:
```sql
SELECT current_quantity
FROM batches
WHERE id = 123
```

### Карточки
Q: "Сколько свободных карточек?"
SQL:
```sql
SELECT COUNT(*) AS free_cards_count
FROM cards
WHERE status = 'free'
```

Q: "Покажи все свободные карточки"
SQL:
```sql
SELECT card_number, machine_id, last_event
FROM cards
WHERE status = 'free'
```

Q: "Какие карточки используются в батче 456?"
SQL:
```sql
SELECT card_number, machine_id, status
FROM cards
WHERE batch_id = 456
```

### Настройки станков
Q: "Сколько активных настроек?"
SQL:
```sql
SELECT COUNT(*) AS active_setups_count
FROM setup_jobs
WHERE status = 'active' AND end_time IS NULL
```

Q: "Покажи все активные настройки"
SQL:
```sql
SELECT sj.id, m.name, sj.start_time, sj.planned_quantity
FROM setup_jobs sj
JOIN machines m ON sj.machine_id = m.id
WHERE sj.status = 'active' AND sj.end_time IS NULL
```

### Статистика по станкам
Q: "Сколько всего станков?"
SQL:
```sql
SELECT COUNT(*) AS total_machines_count
FROM machines
WHERE is_active = true
```

Q: "Покажи все станки"
SQL:
```sql
SELECT id, name, type, is_active
FROM machines
WHERE is_active = true
ORDER BY name
```

### Статистика по батчам
Q: "Сколько всего батчей?"
SQL:
```sql
SELECT COUNT(*) AS total_batches_count
FROM batches
```

Q: "Покажи все батчи за сегодня"
SQL:
```sql
SELECT id, current_quantity, created_at
FROM batches
WHERE DATE(created_at) = CURRENT_DATE
ORDER BY created_at DESC
```

### Статистика по карточкам
Q: "Сколько всего карточек?"
SQL:
```sql
SELECT COUNT(*) AS total_cards_count
FROM cards
```

Q: "Покажи карточки по станку 5"
SQL:
```sql
SELECT card_number, status, batch_id, last_event
FROM cards
WHERE machine_id = 5
ORDER BY card_number
```