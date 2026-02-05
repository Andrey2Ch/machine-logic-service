# Schema documentation for schema `public`

## КРИТИЧЕСКИ ВАЖНО: Расчёт деталей и машино-часов

### Количество деталей = `recounted_quantity`

**ВСЕГДА используй `b.recounted_quantity`** — это реальное количество деталей, пересчитанное складом.

❌ НЕПРАВИЛЬНО: `initial_quantity - current_quantity`
❌ НЕПРАВИЛЬНО: `machine_readings` с LAG
✅ ПРАВИЛЬНО: `SUM(b.recounted_quantity)`

### Машино-часы = детали × цикл / 3600

```sql
ROUND(SUM(b.recounted_quantity * sj.cycle_time / 3600.0), 1) AS "Маш.часы"
```

### Шаблон: детали/часы по ОПЕРАТОРУ
```sql
SELECT 
  e.full_name AS "Оператор",
  SUM(b.recounted_quantity) AS "Детали",
  ROUND(SUM(b.recounted_quantity * sj.cycle_time / 3600.0), 1) AS "Маш.часы"
FROM batches b
JOIN setup_jobs sj ON b.setup_job_id = sj.id
JOIN employees e ON b.operator_id = e.id
WHERE b.batch_time >= '2025-12-01' AND b.batch_time < '2026-01-01'
  AND sj.cycle_time > 0
GROUP BY e.full_name
ORDER BY "Маш.часы" DESC;
```

### Шаблон: детали/часы по СТАНКУ
```sql
SELECT 
  m.name AS "Станок",
  SUM(b.recounted_quantity) AS "Детали",
  ROUND(SUM(b.recounted_quantity * sj.cycle_time / 3600.0), 1) AS "Маш.часы"
FROM batches b
JOIN setup_jobs sj ON b.setup_job_id = sj.id
JOIN machines m ON sj.machine_id = m.id
WHERE b.batch_time >= '2025-12-01' AND b.batch_time < '2026-01-01'
  AND sj.cycle_time > 0
GROUP BY m.name
ORDER BY "Маш.часы" DESC;
```

### Шаблон: один станок по имени
```sql
WHERE m.name ILIKE '%SR-24%'
```

### Фильтр по неделе:
```sql
WHERE EXTRACT(ISOYEAR FROM b.batch_time) = 2025
  AND EXTRACT(WEEK FROM b.batch_time) = 50
```

## Брак и статусы батчей

**Статус батча — поле `current_location`:**
- `defect` — брак
- `rework_repair` — на переделку
- `good` — хорошие
- `warehouse_counted` — на складе пересчитаны
- `archived` — архив
- `production` — в производстве
- `inspection` — на проверке
- `sorting_warehouse` — сортировка

### Шаблон: брак по операторам
```sql
SELECT e.full_name AS "Оператор",
  COUNT(*) AS "Батчей с браком",
  SUM(b.recounted_quantity) AS "Деталей"
FROM batches b
JOIN employees e ON b.operator_id = e.id
WHERE b.batch_time >= '2025-12-01' AND b.batch_time < '2026-01-01'
  AND b.current_location = 'defect'
GROUP BY e.full_name
```

### Шаблон: брак по сотрудникам ОТК
**Связь через `setup_jobs.qa_id`** — кто разрешил наладку:
```sql
SELECT e.full_name AS "ОТК",
  COUNT(*) AS "Батчей с браком",
  SUM(b.recounted_quantity) AS "Деталей"
FROM batches b
JOIN setup_jobs sj ON b.setup_job_id = sj.id
JOIN employees e ON sj.qa_id = e.id
WHERE b.batch_time >= '2025-12-01' AND b.batch_time < '2026-01-01'
  AND b.current_location = 'defect'
GROUP BY e.full_name
```

### Шаблон: общая статистика по статусам
```sql
SELECT current_location AS "Статус", COUNT(*) AS "Батчей"
FROM batches
WHERE batch_time >= '2025-12-01'
GROUP BY current_location
```

## Бизнес-логика системы

### Станки (machines)
- **Работающий станок** = станок с активной настройкой (setup_job) где `status = 'started'` и `end_time IS NULL`
- **Свободный станок** = станок без активной настройки
- **Статусы станков**: `active` (работает), `idle` (простой), `maintenance` (техобслуживание)

### Батчи (batches) 
- **Открытый батч** = `current_quantity > 0` (есть детали для производства)
- **Закрытый батч** = `current_quantity = 0` (все детали произведены)
- **Статусы**: `open` (открыт), `closed` (закрыт), `cancelled` (отменен)

### Карточки (cards)
- **Свободная карточка** = `status = 'free'` и `batch_id IS NULL`
- **Используемая карточка** = `status = 'in_use'` и `batch_id IS NOT NULL`
- **Статусы**: `free` (свободна), `in_use` (используется), `defective` (брак)

### Настройки (setup_jobs)
- **Активная настройка** = `status = 'started'` и `end_time IS NULL`
- **Завершенная настройка** = `status = 'completed'` или `end_time IS NOT NULL`
- **Статусы**: `started` (активна), `completed` (завершена), `cancelled` (отменена), `created` (создана), `allowed` (разрешена)

### Смены (shifts)
- **Дневная смена**: с 06:00 до 18:00 того же дня
- **Ночная смена**: с 18:00 до 06:00 следующего дня (с переходом суток)
- Расчёт `shift_name` и `shift_start` для отметки времени `t`:
  - `shift_name = 'day'`, если `06:00 ≤ t::time < 18:00`, иначе `'night'`
  - `shift_start =`  
    - `date_trunc('day', t) + interval '6 hour'`, если `06:00 ≤ t::time < 18:00`  
    - `date_trunc('day', t) + interval '18 hour'`, если `t::time ≥ 18:00`  
    - `date_trunc('day', t - interval '1 day') + interval '18 hour'`, если `t::time < 06:00`

## Таблицы

## access_attempts

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| telegram_id | bigint | NO |  |
| username | character varying | YES |  |
| full_name | character varying | YES |  |
| timestamp | timestamp without time zone | NO |  |
| processed | boolean | YES |  |

## areas

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| name | character varying | NO |  |
| code | character varying | YES |  |
| is_active | boolean | NO |  |
| created_at | timestamp without time zone | NO |  |

## batch_operations

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| batch_id | integer | YES |  |
| new_batch_id | integer | YES |  |
| operation_type | character varying | NO |  |
| quantity | integer | NO |  |
| employee_id | integer | YES |  |
| created_at | timestamp without time zone | YES |  |

## batches

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| setup_job_id | integer | YES |  |
| lot_id | integer | YES |  |
| parent_batch_id | integer | YES |  |
| initial_quantity | integer | NO |  |
| current_quantity | integer | NO |  |
| current_location | character varying | NO |  |
| original_location | character varying | YES |  |
| operator_id | integer | YES |  |
| batch_time | timestamp without time zone | NO |  |
| created_at | timestamp without time zone | YES |  |
| recounted_quantity | integer | YES |  |
| qc_good_quantity | integer | YES |  |
| qc_rejected_quantity | integer | YES |  |
| qc_rework_quantity | integer | YES |  |
| warehouse_employee_id | integer | YES |  |
| warehouse_received_at | timestamp without time zone | YES |  |
| qc_inspector_id | integer | YES |  |
| qc_comment | character varying | YES |  |
| qc_end_time | timestamp without time zone | YES |  |
| qa_date | timestamp without time zone | YES |  |
| discrepancy_percentage | double precision | YES |  |
| admin_acknowledged_discrepancy | boolean | NO |  |
| updated_at | timestamp without time zone | YES |  |
| operator_reported_quantity | integer | YES |  |
| discrepancy_absolute | integer | YES |  |
| qc_start_time | timestamp without time zone | YES |  |

## calendar_holidays

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| date | date | NO |  |
| name | character varying | NO |  |
| name_en | character varying | YES |  |
| is_company_wide | boolean | YES |  |
| is_recurring | boolean | YES |  |
| created_at | timestamp without time zone | YES |  |
| created_by | integer | YES |  |

## calendar_request_types

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| name | character varying | NO |  |
| name_en | character varying | YES |  |
| description | text | YES |  |
| color | character varying | YES |  |
| is_active | boolean | YES |  |
| is_system | boolean | YES |  |
| created_at | timestamp without time zone | YES |  |
| created_by | integer | YES |  |
| updated_at | timestamp without time zone | YES |  |

## calendar_requests

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| employee_id | integer | NO |  |
| request_type_id | integer | NO |  |
| start_date | date | NO |  |
| end_date | date | NO |  |
| status | character varying | YES |  |
| comment | text | YES |  |
| admin_comment | text | YES |  |
| approved_by | integer | YES |  |
| approved_at | timestamp without time zone | YES |  |
| google_calendar_event_id | character varying | YES |  |
| created_at | timestamp without time zone | YES |  |
| updated_at | timestamp without time zone | YES |  |

## cards

| column | type | nullable | description |
|---|---|---|---|
| card_number | integer | NO |  |
| machine_id | integer | NO |  |
| status | character varying | NO |  |
| batch_id | integer | YES |  |
| last_event | timestamp without time zone | NO |  |

## employee_area_roles

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| employee_id | integer | NO |  |
| area_id | integer | NO |  |
| created_at | timestamp without time zone | NO |  |

## employee_machine_subscriptions

| column | type | nullable | description |
|---|---|---|---|
| employee_id | bigint | NO |  |
| machine_id | bigint | NO |  |
| subscribed | boolean | NO |  |
| active | boolean | NO |  |
| last_activated_at | timestamp without time zone | YES |  |
| updated_at | timestamp without time zone | NO |  |

## employees

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| telegram_id | bigint | YES | Unique identifier |
| username | character varying | YES |  |
| full_name | character varying | YES |  |
| role_id | integer | YES |  |
| created_at | timestamp without time zone | YES |  |
| added_by | bigint | YES |  |
| is_active | boolean | NO |  |
| factory_number | character varying | YES | Unique identifier |
| default_area_id | integer | YES |  |
| whatsapp_phone | character varying | YES |  |

## face_embeddings

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| employee_id | integer | NO |  |
| embedding | bytea | NO |  |
| photo_url | text | YES |  |
| created_at | timestamp without time zone | YES |  |
| is_active | boolean | YES |  |

## lot_materials

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| lot_id | integer | NO |  |
| machine_id | integer | YES |  |
| material_receipt_id | integer | YES |  |
| material_type | character varying | YES |  |
| diameter | double precision | YES |  |
| calculated_bars_needed | integer | YES |  |
| calculated_weight_kg | double precision | YES |  |
| issued_bars | integer | NO |  |
| issued_weight_kg | double precision | YES |  |
| issued_at | timestamp without time zone | YES |  |
| issued_by | integer | YES |  |
| returned_bars | integer | NO |  |
| returned_weight_kg | double precision | YES |  |
| returned_at | timestamp without time zone | YES |  |
| returned_by | integer | YES |  |
| used_bars | integer | NO |  |
| status | character varying | YES |  |
| notes | text | YES |  |
| created_at | timestamp without time zone | YES |  |

## lots

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| part_id | integer | YES |  |
| lot_number | character varying | NO | Unique identifier |
| total_planned_quantity | integer | YES |  |
| status | character varying | NO |  |
| actual_diameter | double precision | YES |  |
| actual_profile_type | character varying | YES |  |
| material_status | character varying | YES |  |
| created_at | timestamp without time zone | YES |  |
| order_manager_id | integer | YES |  |
| created_by_order_manager_at | timestamp without time zone | YES |  |
| due_date | timestamp without time zone | YES |  |
| initial_planned_quantity | integer | YES |  |
| assigned_machine_id | integer | YES |  |
| assigned_order | integer | YES |  |

## machine_readings

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| employee_id | integer | YES |  |
| machine_id | integer | YES |  |
| reading | integer | YES |  |
| created_at | timestamp without time zone | YES |  |
| setup_job_id | integer | YES |  |

## machines

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| name | character varying | YES |  |
| type | character varying | NO |  |
| min_diameter | double precision | YES |  |
| max_diameter | double precision | YES |  |
| max_bar_length | double precision | YES |  |
| is_jbs | boolean | NO |  |
| supports_no_guidebush | boolean | NO |  |
| max_part_length | double precision | YES |  |
| created_at | timestamp without time zone | YES |  |
| is_active | boolean | NO |  |
| is_operational | boolean | NO |  |

## material_operations

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| lot_material_id | integer | NO |  |
| operation_type | character varying | NO |  |
| quantity_bars | integer | NO |  |
| diameter | double precision | YES |  |
| performed_by | integer | YES |  |
| performed_at | timestamp without time zone | YES |  |
| notes | text | YES |  |
| created_at | timestamp without time zone | YES |  |

## material_types

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| material_name | character varying | NO | Unique identifier |
| density_kg_per_m3 | double precision | NO |  |
| description | text | YES |  |
| created_at | timestamp without time zone | YES |  |

## notification_settings

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| notification_type | character varying | NO |  |
| display_name | character varying | NO |  |
| description | text | YES |  |
| category | character varying | YES |  |
| enabled_machinists | boolean | YES |  |
| enabled_operators | boolean | YES |  |
| enabled_qa | boolean | YES |  |
| enabled_admin | boolean | YES |  |
| enabled_telegram | boolean | YES |  |
| created_at | timestamp without time zone | YES |  |
| updated_at | timestamp without time zone | YES |  |

## operator_mapping

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| telegram_id | bigint | NO |  |
| username | character varying | YES |  |
| full_name | character varying | NO |  |
| operator_name | character varying | NO |  |

## parts

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| drawing_number | character varying | NO | Unique identifier |
| material | character varying | YES |  |
| recommended_diameter | double precision | YES |  |
| profile_type | character varying | YES |  |
| part_length | double precision | YES |  |
| description | text | YES |  |
| drawing_url | text | YES |  |
| created_at | timestamp without time zone | YES |  |
| pinned_machine_id | integer | YES |  |

## role_permissions

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| role_id | integer | NO |  |
| resource_type | text | NO |  |
| resource_path | text | NO |  |
| action | text | YES |  |
| allowed | boolean | NO |  |
| created_at | timestamp without time zone | YES |  |
| updated_at | timestamp without time zone | YES |  |

## roles

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| role_name | character varying | NO |  |
| description | character varying | NO |  |
| is_readonly | boolean | NO |  |
| created_at | timestamp without time zone | YES |  |

## setup_defects

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| setup_job_id | integer | YES |  |
| defect_quantity | integer | YES |  |
| defect_reason | character varying | YES |  |
| employee_id | integer | YES |  |
| created_at | timestamp without time zone | YES |  |

## setup_jobs

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| employee_id | integer | YES |  |
| machine_id | integer | YES |  |
| lot_id | integer | YES |  |
| part_id | integer | YES |  |
| planned_quantity | integer | NO |  |
| status | character varying | YES |  |
| start_time | timestamp without time zone | YES |  |
| end_time | timestamp without time zone | YES |  |
| created_at | timestamp without time zone | YES |  |
| cycle_time | integer | YES |  |
| qa_date | timestamp without time zone | YES |  |
| qa_id | integer | YES |  |
| additional_quantity | integer | YES |  |
| order_manager_id | bigint | YES |  |
| created_by_order_manager_at | timestamp without time zone | YES |  |
| pending_qc_date | timestamp without time zone | YES |  |
| setup_quantity_adjustments | text | YES |  |

## setup_quantity_adjustments

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| setup_job_id | integer | YES | Unique identifier |
| created_at | timestamp without time zone | YES |  |
| created_by | integer | YES |  |
| auto_adjustment | integer | YES |  |
| manual_adjustment | integer | YES |  |
| defect_adjustment | integer | YES |  |
| total_adjustment | integer | YES |  |

## setup_statuses

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| status_name | character varying | NO | Unique identifier |
| description | character varying | YES |  |
| created_at | timestamp without time zone | YES |  |

## terminals

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| device_id | character varying | NO |  |
| device_name | character varying | NO |  |
| location_description | text | YES |  |
| is_active | boolean | YES |  |
| last_seen_at | timestamp without time zone | YES |  |
| created_at | timestamp without time zone | YES |  |

## text2sql_captured

| column | type | nullable | description |
|---|---|---|---|
| id | bigint | NO | Primary key |
| captured_at | timestamp without time zone | NO |  |
| duration_ms | integer | YES |  |
| is_error | boolean | YES |  |
| sql | text | NO |  |
| params_json | jsonb | YES |  |
| rows_affected | integer | YES |  |
| route | text | YES |  |
| user_id | text | YES |  |
| role | text | YES |  |
| source_host | text | YES |  |
| question_ru | text | YES |  |
| question_hints | jsonb | YES |  |
| question_generated_at | timestamp without time zone | YES |  |

## text2sql_examples

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| normalized_sql | text | NO |  |
| business_question_ru | text | NO |  |
| business_question_en | text | YES |  |
| operation_type | character varying | YES |  |
| quality_score | integer | YES |  |
| source_captured_id | integer | YES |  |
| created_at | timestamp without time zone | YES |  |

## text2sql_history

| column | type | nullable | description |
|---|---|---|---|
| id | bigint | NO | Primary key |
| session_id | text | NO |  |
| question | text | NO |  |
| sql | text | NO |  |
| validated_sql | text | YES |  |
| rows_preview | jsonb | YES |  |
| created_at | timestamp without time zone | YES |  |

## time_entries

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| employee_id | integer | NO |  |
| entry_type | character varying | NO |  |
| entry_time | timestamp without time zone | NO |  |
| method | character varying | NO |  |
| latitude | numeric | YES |  |
| longitude | numeric | YES |  |
| location_accuracy | numeric | YES |  |
| is_location_valid | boolean | YES |  |
| terminal_device_id | character varying | YES |  |
| face_confidence | numeric | YES |  |
| client_timestamp | timestamp without time zone | YES |  |
| synced_at | timestamp without time zone | YES |  |
| is_manual_correction | boolean | YES |  |
| corrected_by | integer | YES |  |
| correction_reason | text | YES |  |
| created_at | timestamp without time zone | YES |  |
| updated_at | timestamp without time zone | YES |  |

## warehouse_locations

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| name | character varying | NO |  |
| code | character varying | YES |  |
| is_active | boolean | NO |  |
| created_at | timestamp without time zone | NO |  |

## warehouse_stock

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| location_id | integer | NO |  |
| part_id | integer | NO |  |
| quantity | integer | NO |  |
| updated_at | timestamp without time zone | NO |  |

## work_calendar

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| date | date | NO | Unique identifier |
| first_shift_hours | integer | YES |  |
| first_shift_break | integer | YES |  |
| second_shift_hours | integer | YES |  |
| second_shift_break | integer | YES |  |
| description | text | YES |  |
| created_at | timestamp without time zone | YES |  |

## work_shifts

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Primary key |
| employee_id | integer | NO |  |
| shift_date | date | NO |  |
| check_in_time | timestamp without time zone | YES |  |
| check_out_time | timestamp without time zone | YES |  |
| total_hours | numeric | YES |  |
| status | character varying | YES |  |
| created_at | timestamp without time zone | YES |  |
| updated_at | timestamp without time zone | YES |  |

## setup_program_handover

Таблица для **гейта “программа предыдущей наладки”**.

- Логика: если для `next_setup_id` есть `prev_setup_id` и `status = 'pending'` — **нельзя** отправлять/аппрувить наладку (MLS отдаёт 409).
- Если `status in ('confirmed','skipped','not_required')` — гейт удовлетворён.

| column | type | nullable | description |
|---|---|---|---|
| id | bigint | NO | Primary key |
| next_setup_id | integer | NO | ID “новой” наладки (setup_jobs.id), уникально |
| prev_setup_id | integer | YES | ID “предыдущей” наладки на станке (setup_jobs.id) |
| status | text | NO | pending / confirmed / skipped / not_required |
| skip_reason | text | YES | Причина пропуска (если skipped) |
| decided_by_employee_id | integer | YES | Кто подтвердил/пропустил (employees.id) |
| decided_at | timestamp without time zone | YES | Когда подтвердил/пропустил |
| created_at | timestamp without time zone | NO | Когда создана запись |

## file_blobs

Файловые блобы (хранилище на Railway Volume). **Файл НЕ хранится в Postgres**, тут только метадата + ключ пути.

| column | type | nullable | description |
|---|---|---|---|
| id | bigint | NO | Primary key |
| sha256 | character varying | NO | Хеш содержимого (dedup) |
| size_bytes | bigint | NO | Размер файла в байтах |
| storage_key | text | NO | Путь на volume, например `blobs/<sha256>` |
| original_filename | text | YES | Оригинальное имя файла |
| mime_type | text | YES | MIME тип (если известен) |
| created_at | timestamp without time zone | NO | Когда blob добавлен |

## nc_programs

Логическая “программа” для детали (part) и типа станка.

| column | type | nullable | description |
|---|---|---|---|
| id | bigint | NO | Primary key |
| part_id | integer | NO | parts.id |
| machine_type | character varying | NO | **Папка/тип станка** (например SR20/SR22/...). Это ключ для профиля каналов и “проводника” программ. |
| program_kind | character varying | NO | Вид программы (служебное поле, в UI скрыто; на будущее) |
| title | text | YES | Название/метка |
| comment | text | YES | Комментарий |
| created_by_employee_id | integer | YES | employees.id |
| created_at | timestamp without time zone | NO | Когда создана |

## nc_machine_type_profiles

Профили каналов программ по `machine_type` (сколько файлов в ревизии и как они называются в UI).

Примеры:
- Swiss: `[{key:'ch1', label:'main'}, {key:'ch2', label:'sub'}]`
- Citizen (1 файл): `[{key:'nc', label:'program'}]`
- 3 канала: `[{key:'ch1', label:'ch1'}, {key:'ch2', label:'ch2'}, {key:'ch3', label:'ch3'}]`

| column | type | nullable | description |
|---|---|---|---|
| machine_type | text | NO | Primary key, совпадает с nc_programs.machine_type |
| channels | jsonb | NO | JSON array `{key,label}`; ключи: `ch1/ch2/ch3/...` или `nc` |
| created_at | timestamp without time zone | NO | Когда создано |
| updated_at | timestamp without time zone | NO | Когда обновлено |

## nc_program_revisions

Ревизии программы. **Для MVP “последняя ревизия = текущая”** (берём max(rev_number)).

| column | type | nullable | description |
|---|---|---|---|
| id | bigint | NO | Primary key |
| program_id | bigint | NO | nc_programs.id |
| rev_number | integer | NO | Номер ревизии (1..N) |
| note | text | YES | Комментарий к ревизии |
| created_by_employee_id | integer | YES | employees.id |
| created_at | timestamp without time zone | NO | Когда создана ревизия |

## nc_program_revision_files

Файлы (каналы) ревизии.

Важно:
- `role` используется как **ключ канала**: `ch1/ch2/ch3/...` или `nc` (single-file).
- Для обратной совместимости в БД могут встречаться `main/sub`, но API нормализует их в `ch1/ch2`.

| column | type | nullable | description |
|---|---|---|---|
| id | bigint | NO | Primary key |
| revision_id | bigint | NO | nc_program_revisions.id |
| file_id | bigint | NO | file_blobs.id |
| role | character varying | NO | channel_key: `ch1/ch2/ch3/...` или `nc` (legacy: main/sub) |
| created_at | timestamp without time zone | NO | Когда привязали файл |

## material_batches

| column | type | nullable | description |
|---|---|---|---|
| batch_id | text | NO |  |
| material_type | text | YES |  |
| material_group_id | integer | YES | material_groups.id |
| material_subgroup_id | integer | YES | material_subgroups.id |
| diameter | numeric(10,3) | YES |  |
| bar_length | numeric(10,3) | YES |  |
| weight_per_meter_kg | numeric(10,4) | YES |  |
| weight_kg | numeric(12,4) | YES |  |
| quantity_received | integer | YES |  |
| supplier | text | YES |  |
| supplier_doc_number | text | YES |  |
| date_received | date | YES |  |
| cert_folder | text | YES |  |
| from_customer | boolean | NO |  |
| allowed_drawings | text[] | YES |  |
| preferred_drawing | text | YES |  |
| status | text | NO |  |
| created_by | integer | YES | employees.id |
| created_at | timestamp with time zone | NO |  |

## material_groups

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| code | text | NO |  |
| name | text | NO |  |
| density_kg_m3 | numeric(10,3) | YES |  |
| is_active | boolean | NO |  |
| created_at | timestamp with time zone | NO |  |

## material_subgroups

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| group_id | integer | NO | material_groups.id |
| code | text | NO |  |
| name | text | NO |  |
| density_kg_m3 | numeric(10,3) | YES |  |
| is_active | boolean | NO |  |
| created_at | timestamp with time zone | NO |  |

## storage_locations

| column | type | nullable | description |
|---|---|---|---|
| code | text | NO |  |
| name | text | NO |  |
| type | text | NO |  |
| capacity | integer | YES |  |
| status | text | NO |  |
| created_at | timestamp with time zone | NO |  |

## storage_location_segments

| column | type | nullable | description |
|---|---|---|---|
| segment_type | text | NO |  |
| code | text | NO |  |
| name | text | NO |  |
| sort_order | integer | YES |  |
| is_active | boolean | NO |  |
| created_at | timestamp with time zone | NO |  |

## inventory_positions

| column | type | nullable | description |
|---|---|---|---|
| batch_id | text | NO | material_batches.batch_id |
| location_code | text | NO | storage_locations.code |
| quantity | integer | NO |  |
| updated_at | timestamp with time zone | NO |  |

## warehouse_movements

| column | type | nullable | description |
|---|---|---|---|
| movement_id | bigint | NO |  |
| batch_id | text | NO | material_batches.batch_id |
| movement_type | text | NO | receive/move/issue/return/writeoff |
| quantity | integer | NO |  |
| from_location | text | YES | storage_locations.code |
| to_location | text | YES | storage_locations.code |
| related_lot_id | integer | YES | lots.id |
| cut_factor | integer | YES |  |
| performed_by | integer | YES | employees.id |
| performed_at | timestamp with time zone | NO |  |
| notes | text | YES |  |
