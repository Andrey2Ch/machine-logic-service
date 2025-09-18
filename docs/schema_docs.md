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
| order_manager_id | bigint | YES | ID ?????????? (????????? ???????), ?????????? ? ???? ?????. |
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

