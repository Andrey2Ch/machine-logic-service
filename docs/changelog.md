# Журнал изменений

## [2026-01-21] - Движения склада: фильтр по лоту и станку
### Изменено
- `/warehouse-materials/movements` поддерживает фильтры `related_lot_id` и `related_machine_id`.

## [2026-01-21] - Operator view: лот в данных станка
### Добавлено
- В `/machines/operator-view` возвращаются `lot_id` и `lot_number`.

## [2026-01-21] - Складские движения
### Добавлено
- Поле `related_machine_id` в `warehouse_movements` для связи выдачи со станком.
