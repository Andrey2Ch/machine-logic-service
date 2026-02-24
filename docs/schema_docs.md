# Schema documentation for schema `public`

## access_attempts

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| telegram_id | bigint | NO |  |
| username | character varying | YES |  |
| full_name | character varying | YES |  |
| timestamp | timestamp without time zone | NO |  |
| processed | boolean | YES |  |

## areas

| column | type | nullable | description |
|---|---|---|---|
| id | bigint | NO |  |
| name | text | NO |  |
| code | text | YES |  |
| is_active | boolean | NO |  |
| created_at | timestamp without time zone | NO |  |

## batch_operations

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| batch_id | integer | YES |  |
| new_batch_id | integer | YES |  |
| operation_type | character varying | NO |  |
| quantity | integer | NO |  |
| employee_id | integer | YES |  |
| created_at | timestamp without time zone | YES |  |

## batches

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| setup_job_id | integer | YES |  |
| lot_id | integer | YES |  |
| parent_batch_id | integer | YES |  |
| initial_quantity | integer | NO |  |
| current_quantity | integer | NO |  |
| current_location | character varying | NO |  |
| operator_id | integer | YES |  |
| batch_time | timestamp without time zone | NO |  |
| created_at | timestamp without time zone | YES |  |
| recounted_quantity | integer | YES |  |
| warehouse_employee_id | integer | YES |  |
| warehouse_received_at | timestamp without time zone | YES |  |
| qc_inspector_id | integer | YES |  |
| qc_comment | text | YES |  |
| qc_end_time | timestamp without time zone | YES |  |
| qa_date | timestamp without time zone | YES |  |
| discrepancy_percentage | real | YES |  |
| admin_acknowledged_discrepancy | boolean | NO |  |
| updated_at | timestamp without time zone | YES |  |
| operator_reported_quantity | integer | YES |  |
| discrepancy_absolute | integer | YES |  |
| qc_start_time | timestamp without time zone | YES |  |

## cards

| column | type | nullable | description |
|---|---|---|---|
| card_number | integer | NO |  |
| machine_id | bigint | NO |  |
| status | character varying | NO |  |
| batch_id | bigint | YES |  |
| last_event | timestamp without time zone | NO |  |

## employee_area_roles

| column | type | nullable | description |
|---|---|---|---|
| id | bigint | NO |  |
| employee_id | bigint | NO |  |
| area_id | bigint | NO |  |
| role | text | NO |  |

## employee_machine_subscriptions

| column | type | nullable | description |
|---|---|---|---|
| employee_id | bigint | NO |  |
| machine_id | bigint | NO |  |
| subscribed | boolean | NO |  |
| active | boolean | NO |  |
| notify_on | ARRAY | NO |  |
| last_activated_at | timestamp with time zone | YES |  |
| updated_at | timestamp with time zone | NO |  |

## employees

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| telegram_id | bigint | YES |  |
| username | character varying | YES |  |
| full_name | character varying | YES |  |
| role_id | integer | YES |  |
| created_at | timestamp without time zone | YES |  |
| added_by | bigint | YES |  |
| is_active | boolean | NO |  |
| factory_number | character varying | YES | ?????????? ?????????? ????????? ????? ?????????? |
| default_area_id | bigint | YES |  |

## lots

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| part_id | integer | YES |  |
| lot_number | character varying | NO |  |
| total_planned_quantity | integer | YES |  |
| status | character varying | NO |  |
| created_at | timestamp without time zone | YES |  |
| order_manager_id | bigint | YES | ID менеджера, назначенного к лоту |
| assigned_machine_id | bigint | YES | ID станка, назначенного к лоту |
| assigned_order | integer | YES | Порядок в очереди станка |
| actual_diameter | numeric(10,3) | YES | Фактический диаметр материала |
| actual_profile_type | character varying | YES | Фактический тип профиля материала |
| material_status | character varying | YES | Статус управления материалом |
| reserved_batch_id | character varying(255) | YES | ID зарезервированной партии со склада |

## lot_materials

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| lot_id | integer | YES |  |
| material_type | character varying | YES |  |
| diameter | numeric | YES |  |
| quantity | integer | YES |  |
| shape | character varying(20) | YES | Форма профиля материала (round/hexagon/square) |
| created_at | timestamp without time zone | YES |  |

## material_operations

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| lot_id | integer | YES |  |
| operation_type | character varying | YES |  |
| quantity | integer | YES |  |
| diameter | numeric | YES |  |
| shape | character varying(20) | YES | Форма профиля материала |
| operator_id | integer | YES |  |
| machine_id | integer | YES |  |
| created_at | timestamp without time zone | YES |  |

## material_batches

| column | type | nullable | description |
|---|---|---|---|
| batch_id | text | NO |  |
| material_type | text | YES |  |
| profile_type | character varying | YES |  |
| material_group_id | integer | YES |  |
| material_subgroup_id | integer | YES |  |
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
| created_by | integer | YES |  |
| created_at | timestamp with time zone | NO |  |
| parent_batch_id | text | YES | ID родительской партии (для нарезанного материала) |

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
| group_id | integer | NO |  |
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
| batch_id | text | NO |  |
| location_code | text | NO |  |
| quantity | integer | NO |  |
| updated_at | timestamp with time zone | NO |  |

## warehouse_movements

| column | type | nullable | description |
|---|---|---|---|
| movement_id | bigint | NO |  |
| batch_id | text | NO |  |
| movement_type | text | NO |  |
| quantity | integer | NO |  |
| from_location | text | YES |  |
| to_location | text | YES |  |
| related_lot_id | integer | YES |  |
| related_machine_id | integer | YES |  |
| cut_factor | integer | YES |  |
| performed_by | integer | YES |  |
| performed_at | timestamp with time zone | NO |  |
| notes | text | YES |  |
| created_by_order_manager_at | timestamp with time zone | YES | ????? ???????? ???? ?????????? ???????. |
| due_date | timestamp with time zone | YES | ??????????? ???? ???????? ????. |
| initial_planned_quantity | integer | YES | ?????????????? ???????? ??????????, ????????? ?????????? ???????. |

## machine_readings

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| employee_id | integer | YES |  |
| machine_id | integer | YES |  |
| reading | integer | YES |  |
| created_at | timestamp without time zone | YES |  |
| setup_job_id | integer | YES |  |

## machines

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| name | character varying | YES |  |
| type | character varying | NO |  |
| created_at | timestamp without time zone | YES |  |
| is_active | boolean | NO |  |
| location_id | bigint | NO |  |
| serial_number | text | YES |  |
| notes | text | YES |  |
| display_order | integer | YES |  |

## operator_mapping

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| telegram_id | bigint | NO |  |
| username | character varying | YES |  |
| full_name | character varying | NO |  |
| operator_name | character varying | NO |  |

## parts

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| drawing_number | character varying | NO |  |
| material | text | YES | ????????, ?? ???????? ??????????? ??????. ????? ?????????? description. |
| created_at | timestamp without time zone | YES |  |

## roles

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| role_name | character varying | NO |  |
| description | text | NO |  |
| created_at | timestamp without time zone | YES |  |

## setup_defects

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| setup_job_id | integer | YES |  |
| defect_quantity | integer | YES |  |
| defect_reason | text | YES |  |
| employee_id | integer | YES |  |
| created_at | timestamp without time zone | YES |  |

## setup_jobs

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
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
| qa_date | timestamp with time zone | YES |  |
| qa_id | integer | YES |  |
| additional_quantity | integer | YES |  |

## setup_quantity_adjustments

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| setup_job_id | integer | YES |  |
| created_at | timestamp without time zone | YES |  |
| created_by | integer | YES |  |
| auto_adjustment | integer | YES |  |
| manual_adjustment | integer | YES |  |
| defect_adjustment | integer | YES |  |
| total_adjustment | integer | YES |  |

## setup_statuses

| column | type | nullable | description |
|---|---|---|---|
| id | integer | NO |  |
| status_name | character varying | NO |  |
| description | character varying | YES |  |
| created_at | timestamp without time zone | YES |  |

## setup_program_handover

Таблица для **гейта “программа предыдущей наладки”**.

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
| machine_type | character varying | NO | Тип станка/контроллера (MVP: строка) |
| program_kind | character varying | NO | Вид программы (MVP: строка) |
| title | text | YES | Название/метка |
| comment | text | YES | Комментарий |
| created_by_employee_id | integer | YES | employees.id |
| created_at | timestamp without time zone | NO | Когда создана |

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

Файлы ревизии. **Swiss-type требование:** на одну ревизию 2 файла: `role=main` и `role=sub` (без ZIP).

| column | type | nullable | description |
|---|---|---|---|
| id | bigint | NO | Primary key |
| revision_id | bigint | NO | nc_program_revisions.id |
| file_id | bigint | NO | file_blobs.id |
| role | character varying | NO | main / sub |
| created_at | timestamp without time zone | NO | Когда привязали файл |

## ai_conversations

Секция добавлена по миграциям (без подключения к БД). Типы уточнить при следующем refresh.

| column | type | nullable | description |
|---|---|---|---|
| id | unknown | ? |  |

## ai_feedback

Секция добавлена по миграциям (без подключения к БД). Типы уточнить при следующем refresh.

| column | type | nullable | description |
|---|---|---|---|
| id | unknown | ? |  |

## ai_knowledge_documents

Секция добавлена по миграциям (без подключения к БД). Типы уточнить при следующем refresh.

| column | type | nullable | description |
|---|---|---|---|
| id | unknown | ? |  |

## ai_memory

Секция добавлена по миграциям (без подключения к БД). Типы уточнить при следующем refresh.

| column | type | nullable | description |
|---|---|---|---|
| id | unknown | ? |  |

## ai_messages

Секция добавлена по миграциям (без подключения к БД). Типы уточнить при следующем refresh.

| column | type | nullable | description |
|---|---|---|---|
| id | unknown | ? |  |

## ai_sql_examples

Секция добавлена по миграциям (без подключения к БД). Типы уточнить при следующем refresh.

| column | type | nullable | description |
|---|---|---|---|
| id | unknown | ? |  |

## app_settings

Секция добавлена по миграциям (без подключения к БД). Типы уточнить при следующем refresh.

| column | type | nullable | description |
|---|---|---|---|
| id | unknown | ? |  |

## nc_machine_type_profiles

Секция добавлена по миграциям (без подключения к БД). Типы уточнить при следующем refresh.

| column | type | nullable | description |
|---|---|---|---|
| id | unknown | ? |  |

## notification_settings

Секция добавлена по миграциям (без подключения к БД). Типы уточнить при следующем refresh.

| column | type | nullable | description |
|---|---|---|---|
| id | unknown | ? |  |

## print_jobs

Секция добавлена по миграциям (без подключения к БД). Типы уточнить при следующем refresh.

| column | type | nullable | description |
|---|---|---|---|
| id | unknown | ? |  |
| idempotency_key | unknown | ? |  |
| kind | unknown | ? |  |
| payload | unknown | ? |  |
| copies | unknown | ? |  |
| priority | unknown | ? |  |
| status | unknown | ? |  |
| assigned_station_name | unknown | ? |  |
| lease_token | unknown | ? |  |
| lease_expires_at | unknown | ? |  |
| attempt_count | unknown | ? |  |
| last_error | unknown | ? |  |
| started_at | unknown | ? |  |
| completed_at | unknown | ? |  |
| failed_at | unknown | ? |  |
| cancelled_at | unknown | ? |  |
| created_at | unknown | ? |  |
| updated_at | unknown | ? |  |
| created_by_employee_id | unknown | ? |  |

## user_sessions

Секция добавлена по миграциям (без подключения к БД). Типы уточнить при следующем refresh.

| column | type | nullable | description |
|---|---|---|---|
| id | unknown | ? |  |

## migration_pending_columns

Колонки из миграций (без подключения к БД). Типы уточнить при следующем refresh.

| column | type | nullable | description |
|---|---|---|---|
| assigned_machine_id | unknown | ? |  |
| assigned_order | unknown | ? |  |
| avg_cycle_time | unknown | ? |  |
| closed_at | unknown | ? |  |
| closed_by | unknown | ? |  |
| defect_bars | unknown | ? |  |
| enabled_viewer | unknown | ? |  |
| is_operational | unknown | ? |  |
| language_admin | unknown | ? |  |
| language_machinists | unknown | ? |  |
| language_operators | unknown | ? |  |
| language_qa | unknown | ? |  |
| language_viewer | unknown | ? |  |
| pinned_machine_id | unknown | ? |  |
| price_per_kg_ils | unknown | ? |  |
| price_per_meter_ils | unknown | ? |  |

## stoppage_reasons

Machine stoppage/fault reason codes (רשימת תקלות). Categories: machine (1-16), part (30-49), work_and_material (70-74).

| column | type | nullable | description |
|---|---|---|---|
| code | integer | NO | PK. Fault code number |
| category | text | NO | machine / part / work_and_material |
| name_he | text | NO | Hebrew name |
| name_ru | text | NO | Russian name |
| name_en | text | NO | English name |
| is_active | boolean | NO | Soft-delete flag |
| created_at | timestamp with time zone | NO |  |

