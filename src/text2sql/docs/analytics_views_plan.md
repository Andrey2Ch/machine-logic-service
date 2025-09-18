# План аналитических VIEW (MVP)

Цель: стабильные источники для Text2SQL без сложных JOIN-ов.

## 1) analytics_batches
- Источник: `batches`
- Поля: `batch_id`, `part_name`, `status`, `created_at`, `updated_at`, `qty_planned`, `qty_done`
- Примеры вопросов:
  - "how many batches are open?"
  - "count batches by status"

## 2) analytics_batch_operations
- Источник: `batch_operations` (+ возможно денормализация имени станка)
- Поля: `batch_id`, `operation_no`, `machine_id`, `machine_name`, `status`, `planned_time_sec`, `actual_time_sec`
- Примеры вопросов:
  - "total planned_time by machine"
  - "avg actual_time for operation 10"

## 3) analytics_machines
- Источник: `Machine` / `MachineReading`
- Поля: `machine_id`, `machine_name`, `area_name`, `last_reading_at`, `last_status`
- Примеры вопросов:
  - "how many machines per area"
  - "machines without readings in last day"

## 4) analytics_daily_production
- Источник: агрегаты по суткам из `MachineReading` / производственных таблиц
- Поля: `date`, `machine_name`, `qty_done`, `downtime_sec`
- Примеры вопросов:
  - "qty_done by date"
  - "top 5 machines by downtime"

## 5) analytics_qc
- Источник: `batches` + `qc`/инспекции
- Поля: `batch_id`, `qc_status`, `defects_count`, `last_inspected_at`
- Примеры вопросов:
  - "defects_count by qc_status"
  - "recently inspected batches"

Примечания:
- Имена view фиксировать в `public.analytics_*`.
- Добавить `comment on column` для описаний (используется автодок-скриптом).
- Стараться хранить времена в секундах.
