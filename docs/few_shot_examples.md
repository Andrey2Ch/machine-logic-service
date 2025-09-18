# Few-Shot Examples для Text2SQL

## Базовые запросы

### 1. Подсчет записей
**Вопрос:** "сколько всего записей в таблице batches?"
**SQL:** `SELECT COUNT(*) as total_batches FROM batches;`

### 2. Подсчет по условию
**Вопрос:** "сколько открытых батчей?"
**SQL:** `SELECT COUNT(*) as open_batches FROM batches WHERE status = 'open';`

### 3. Группировка по статусу
**Вопрос:** "покажи количество батчей по статусам"
**SQL:** `SELECT status, COUNT(*) as count FROM batches GROUP BY status;`

### 4. Текущее время
**Вопрос:** "какое сейчас время?"
**SQL:** `SELECT NOW() as current_time;`

### 5. Последние записи
**Вопрос:** "покажи последние 10 батчей"
**SQL:** `SELECT * FROM batches ORDER BY created_at DESC LIMIT 10;`

## Аналитика по батчам

### 6. Батчи за сегодня
**Вопрос:** "сколько батчей создано сегодня?"
**SQL:** `SELECT COUNT(*) as today_batches FROM batches WHERE DATE(created_at) = CURRENT_DATE;`

### 7. Батчи по дням недели
**Вопрос:** "покажи батчи по дням недели"
**SQL:** `SELECT EXTRACT(DOW FROM created_at) as day_of_week, COUNT(*) as count FROM batches GROUP BY EXTRACT(DOW FROM created_at);`

### 8. Среднее количество запланированных деталей
**Вопрос:** "какое среднее количество запланированных деталей в батчах?"
**SQL:** `SELECT AVG(qty_planned) as avg_planned FROM batches WHERE qty_planned IS NOT NULL;`

### 9. Батчи с наибольшим количеством деталей
**Вопрос:** "покажи топ-5 батчей по количеству запланированных деталей"
**SQL:** `SELECT batch_id, part_name, qty_planned FROM batches ORDER BY qty_planned DESC LIMIT 5;`

### 10. Статистика по статусам
**Вопрос:** "покажи статистику по статусам батчей"
**SQL:** `SELECT status, COUNT(*) as count, AVG(qty_planned) as avg_planned FROM batches GROUP BY status;`

## Аналитика по операциям

### 11. Операции по станкам
**Вопрос:** "сколько операций на каждом станке?"
**SQL:** `SELECT machine_id, COUNT(*) as operation_count FROM batch_operations GROUP BY machine_id;`

### 12. Среднее время операций
**Вопрос:** "какое среднее время выполнения операций?"
**SQL:** `SELECT AVG(actual_time_sec) as avg_time FROM batch_operations WHERE actual_time_sec IS NOT NULL;`

### 13. Операции по статусам
**Вопрос:** "покажи операции по статусам"
**SQL:** `SELECT status, COUNT(*) as count FROM batch_operations GROUP BY status;`

### 14. Самые долгие операции
**Вопрос:** "покажи топ-10 самых долгих операций"
**SQL:** `SELECT batch_id, operation_no, machine_id, actual_time_sec FROM batch_operations WHERE actual_time_sec IS NOT NULL ORDER BY actual_time_sec DESC LIMIT 10;`

### 15. Операции за последний час
**Вопрос:** "сколько операций завершено за последний час?"
**SQL:** `SELECT COUNT(*) as recent_operations FROM batch_operations WHERE updated_at > NOW() - INTERVAL '1 hour';`

## Аналитика по станкам

### 16. Все станки
**Вопрос:** "покажи все станки"
**SQL:** `SELECT machine_id, machine_name, area_name FROM machines;`

### 17. Станки по зонам
**Вопрос:** "сколько станков в каждой зоне?"
**SQL:** `SELECT area_name, COUNT(*) as machine_count FROM machines GROUP BY area_name;`

### 18. Последние показания станков
**Вопрос:** "покажи последние показания всех станков"
**SQL:** `SELECT machine_id, last_reading_at, last_status FROM machines WHERE last_reading_at IS NOT NULL ORDER BY last_reading_at DESC;`

### 19. Станки без показаний
**Вопрос:** "какие станки не передавали показания больше суток?"
**SQL:** `SELECT machine_id, machine_name, last_reading_at FROM machines WHERE last_reading_at < NOW() - INTERVAL '1 day' OR last_reading_at IS NULL;`

### 20. Станки по статусам
**Вопрос:** "покажи станки по статусам"
**SQL:** `SELECT last_status, COUNT(*) as count FROM machines WHERE last_status IS NOT NULL GROUP BY last_status;`

## Аналитика по времени

### 21. Батчи за последние 7 дней
**Вопрос:** "сколько батчей создано за последние 7 дней?"
**SQL:** `SELECT COUNT(*) as last_week_batches FROM batches WHERE created_at > NOW() - INTERVAL '7 days';`

### 22. Операции по часам
**Вопрос:** "покажи активность операций по часам"
**SQL:** `SELECT EXTRACT(HOUR FROM created_at) as hour, COUNT(*) as count FROM batch_operations GROUP BY EXTRACT(HOUR FROM created_at) ORDER BY hour;`

### 23. Батчи по месяцам
**Вопрос:** "покажи количество батчей по месяцам"
**SQL:** `SELECT EXTRACT(YEAR FROM created_at) as year, EXTRACT(MONTH FROM created_at) as month, COUNT(*) as count FROM batches GROUP BY EXTRACT(YEAR FROM created_at), EXTRACT(MONTH FROM created_at) ORDER BY year, month;`

### 24. Время работы системы
**Вопрос:** "сколько времени работает система?"
**SQL:** `SELECT NOW() - MIN(created_at) as system_uptime FROM batches;`

### 25. Последняя активность
**Вопрос:** "когда была последняя активность в системе?"
**SQL:** `SELECT MAX(GREATEST(created_at, updated_at)) as last_activity FROM batches;`

## Сложные запросы

### 26. Батчи с операциями
**Вопрос:** "покажи батчи с количеством операций"
**SQL:** `SELECT b.batch_id, b.part_name, COUNT(bo.operation_no) as operation_count FROM batches b LEFT JOIN batch_operations bo ON b.batch_id = bo.batch_id GROUP BY b.batch_id, b.part_name;`

### 27. Станки с батчами
**Вопрос:** "покажи станки с количеством батчей"
**SQL:** `SELECT m.machine_id, m.machine_name, COUNT(DISTINCT bo.batch_id) as batch_count FROM machines m LEFT JOIN batch_operations bo ON m.machine_id = bo.machine_id GROUP BY m.machine_id, m.machine_name;`

### 28. Среднее время по станкам
**Вопрос:** "покажи среднее время операций по станкам"
**SQL:** `SELECT machine_id, AVG(actual_time_sec) as avg_time FROM batch_operations WHERE actual_time_sec IS NOT NULL GROUP BY machine_id ORDER BY avg_time DESC;`

### 29. Батчи с превышением времени
**Вопрос:** "покажи батчи где фактическое время больше запланированного"
**SQL:** `SELECT batch_id, operation_no, planned_time_sec, actual_time_sec FROM batch_operations WHERE actual_time_sec > planned_time_sec;`

### 30. Общая статистика
**Вопрос:** "покажи общую статистику системы"
**SQL:** `SELECT 'batches' as table_name, COUNT(*) as count FROM batches UNION ALL SELECT 'operations', COUNT(*) FROM batch_operations UNION ALL SELECT 'machines', COUNT(*) FROM machines;`
