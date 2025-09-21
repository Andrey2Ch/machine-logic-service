# Schema documentation for schema `public`

## Бизнес-логика системы

### Станки (machines)
- **Работающий станок** = станок с активной настройкой (setup_job) где `status = 'active'` и `end_time IS NULL`
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
- **Активная настройка** = `status = 'active'` и `end_time IS NULL`
- **Завершенная настройка** = `end_time IS NOT NULL`
- **Статусы**: `active` (активна), `completed` (завершена), `cancelled` (отменена)

## Таблицы

## access_attempts

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Уникальный идентификатор попытки доступа |
| telegram_id | bigint | NO | Telegram ID пользователя |
| username | character varying | YES | Имя пользователя в Telegram |
| full_name | character varying | YES | Полное имя пользователя |
| timestamp | timestamp without time zone | NO | Время попытки доступа |
| processed | boolean | YES | Обработана ли попытка |

## areas

| column | type | nullable | description |
|---|---|---|---|
| id | bigint | NO | Уникальный идентификатор зоны |
| name | text | NO | Название зоны производства |
| code | text | YES | Код зоны |
| is_active | boolean | NO | Активна ли зона |
| created_at | timestamp without time zone | NO | Дата создания |

## batch_operations

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Уникальный идентификатор операции |
| batch_id | integer | YES | ID батча |
| new_batch_id | integer | YES | ID нового батча (при разделении) |
| operation_type | character varying | NO | Тип операции |
| quantity | integer | NO | Количество деталей |
| employee_id | integer | YES | ID сотрудника |
| created_at | timestamp without time zone | YES | Время создания операции |

## batches

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Уникальный идентификатор батча |
| setup_job_id | integer | YES | ID настройки станка |
| lot_id | integer | YES | ID партии |
| parent_batch_id | integer | YES | ID родительского батча |
| initial_quantity | integer | NO | Начальное количество деталей |
| current_quantity | integer | NO | Текущее количество деталей (0 = батч закрыт) |
| current_location | character varying | NO | Текущее местоположение |
| operator_id | integer | YES | ID оператора |
| batch_time | timestamp without time zone | NO | Время создания батча |
| created_at | timestamp without time zone | YES | Дата создания |
| recounted_quantity | integer | YES | Пересчитанное количество |
| warehouse_employee_id | integer | YES | ID складского сотрудника |
| warehouse_received_at | timestamp without time zone | YES | Время получения на складе |
| qc_inspector_id | integer | YES | ID контролера качества |
| qc_comment | text | YES | Комментарий контролера |
| qc_end_time | timestamp without time zone | YES | Время окончания контроля |
| qa_date | timestamp without time zone | YES | Дата QA |
| discrepancy_percentage | real | YES | Процент расхождения |
| admin_acknowledged_discrepancy | boolean | NO | Подтверждено ли расхождение админом |
| updated_at | timestamp without time zone | YES | Время обновления |
| operator_reported_quantity | integer | YES | Количество, сообщенное оператором |
| discrepancy_absolute | integer | YES | Абсолютное расхождение |
| qc_start_time | timestamp without time zone | YES | Время начала контроля |

## cards

| column | type | nullable | description |
|---|---|---|---|
| card_number | integer | NO | Номер карточки |
| machine_id | bigint | NO | ID станка |
| status | character varying | NO | Статус карточки: 'free' (свободна), 'in_use' (используется), 'defective' (брак) |
| batch_id | bigint | YES | ID батча (если используется) |
| last_event | timestamp without time zone | NO | Время последнего события |

## employee_area_roles

| column | type | nullable | description |
|---|---|---|---|
| id | bigint | NO | Уникальный идентификатор |
| employee_id | bigint | NO | ID сотрудника |
| area_id | bigint | NO | ID зоны |
| role | text | NO | Роль в зоне |

## employee_machine_subscriptions

| column | type | nullable | description |
|---|---|---|---|
| employee_id | bigint | NO | ID сотрудника |
| machine_id | bigint | NO | ID станка |
| subscribed | boolean | NO | Подписан ли на уведомления |
| active | boolean | NO | Активна ли подписка |
| notify_on | ARRAY | NO | События для уведомлений |
| last_activated_at | timestamp with time zone | YES | Время последней активации |
| updated_at | timestamp with time zone | NO | Время обновления |

## employees

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Уникальный идентификатор сотрудника |
| telegram_id | bigint | YES | Telegram ID |
| username | character varying | YES | Имя пользователя в Telegram |
| full_name | character varying | YES | Полное имя |
| role_id | integer | YES | ID роли |
| created_at | timestamp without time zone | YES | Дата создания |
| added_by | bigint | YES | Добавлен кем |
| is_active | boolean | NO | Активен ли сотрудник |
| factory_number | character varying | YES | Заводской номер |
| default_area_id | bigint | YES | Зона по умолчанию |

## lots

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Уникальный идентификатор партии |
| part_id | integer | YES | ID детали |
| lot_number | character varying | NO | Номер партии |
| total_planned_quantity | integer | YES | Общее запланированное количество |
| status | character varying | NO | Статус партии |
| created_at | timestamp without time zone | YES | Дата создания |
| order_manager_id | bigint | YES | ID менеджера заказа |
| created_by_order_manager_at | timestamp with time zone | YES | Время создания менеджером |
| due_date | timestamp with time zone | YES | Срок выполнения |
| initial_planned_quantity | integer | YES | Начальное запланированное количество |

## machine_readings

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Уникальный идентификатор показания |
| employee_id | integer | YES | ID сотрудника |
| machine_id | integer | YES | ID станка |
| reading | integer | YES | Показание счетчика |
| created_at | timestamp without time zone | YES | Время показания |
| setup_job_id | integer | YES | ID настройки |

## machines

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Уникальный идентификатор станка |
| name | character varying | YES | Название станка |
| type | character varying | NO | Тип станка |
| created_at | timestamp without time zone | YES | Дата создания |
| is_active | boolean | NO | Активен ли станок |
| location_id | bigint | NO | ID местоположения |
| serial_number | text | YES | Серийный номер |
| notes | text | YES | Заметки |
| display_order | integer | YES | Порядок отображения |

## operator_mapping

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Уникальный идентификатор |
| telegram_id | bigint | NO | Telegram ID |
| username | character varying | YES | Имя пользователя |
| full_name | character varying | NO | Полное имя |
| operator_name | character varying | NO | Имя оператора |

## parts

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Уникальный идентификатор детали |
| drawing_number | character varying | NO | Номер чертежа |
| material | text | YES | Материал детали |
| created_at | timestamp without time zone | YES | Дата создания |

## roles

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Уникальный идентификатор роли |
| role_name | character varying | NO | Название роли |
| description | text | NO | Описание роли |
| created_at | timestamp without time zone | YES | Дата создания |

## setup_defects

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Уникальный идентификатор дефекта |
| setup_job_id | integer | YES | ID настройки |
| defect_quantity | integer | YES | Количество дефектных деталей |
| defect_reason | text | YES | Причина дефекта |
| employee_id | integer | YES | ID сотрудника |
| created_at | timestamp without time zone | YES | Время создания записи |

## setup_jobs

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Уникальный идентификатор настройки |
| employee_id | integer | YES | ID сотрудника |
| machine_id | integer | YES | ID станка |
| lot_id | integer | YES | ID партии |
| part_id | integer | YES | ID детали |
| planned_quantity | integer | NO | Запланированное количество |
| status | character varying | YES | Статус: 'active' (активна), 'completed' (завершена), 'cancelled' (отменена) |
| start_time | timestamp without time zone | YES | Время начала |
| end_time | timestamp without time zone | YES | Время окончания (NULL = активна) |
| created_at | timestamp without time zone | YES | Дата создания |
| cycle_time | integer | YES | Время цикла в секундах |
| qa_date | timestamp with time zone | YES | Дата QA |
| qa_id | integer | YES | ID QA |
| additional_quantity | integer | YES | Дополнительное количество |

## setup_quantity_adjustments

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Уникальный идентификатор корректировки |
| setup_job_id | integer | YES | ID настройки |
| created_at | timestamp without time zone | YES | Время создания |
| created_by | integer | YES | Создано кем |
| auto_adjustment | integer | YES | Автоматическая корректировка |
| manual_adjustment | integer | YES | Ручная корректировка |
| defect_adjustment | integer | YES | Корректировка по дефектам |
| total_adjustment | integer | YES | Общая корректировка |

## setup_statuses

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO | Уникальный идентификатор статуса |
| status_name | character varying | NO | Название статуса |
| description | character varying | YES | Описание статуса |
| created_at | timestamp without time zone | YES | Дата создания |

## Примеры запросов

### Работающие станки
```sql
SELECT m.id, m.name, sj.status, sj.start_time
FROM machines m
JOIN setup_jobs sj ON m.id = sj.machine_id
WHERE sj.status = 'active' AND sj.end_time IS NULL
```

### Свободные станки
```sql
SELECT m.id, m.name
FROM machines m
LEFT JOIN setup_jobs sj ON m.id = sj.machine_id AND sj.status = 'active' AND sj.end_time IS NULL
WHERE sj.id IS NULL AND m.is_active = true
```

### Открытые батчи
```sql
SELECT id, current_quantity, created_at
FROM batches
WHERE current_quantity > 0
```

### Свободные карточки
```sql
SELECT card_number, machine_id, last_event
FROM cards
WHERE status = 'free'
```