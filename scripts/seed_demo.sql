-- ============================================
-- ISRAMAT DEMO SEED DATA
-- Демо-данные для демонстрации системы клиентам
-- ============================================

-- Очистка существующих данных (если нужно начать с чистой БД)
-- ВНИМАНИЕ: Раскомментируйте только если хотите полностью очистить БД!
/*
TRUNCATE TABLE 
  batches, batch_operations, setup_jobs, setup_defects, 
  lots, parts, machine_readings, cards,
  employees, machines, areas, roles
CASCADE;
*/

-- ============================================
-- 1. РОЛИ
-- ============================================
INSERT INTO roles (id, role_name, description, is_readonly, created_at) VALUES
  (1, 'admin', 'Администратор системы. Полный доступ ко всем функциям.', false, NOW()),
  (2, 'operator', 'Оператор станка. Создание партий, отправка на склад.', false, NOW()),
  (3, 'machinist', 'Наладчик. Настройка станков, контроль производства.', false, NOW()),
  (4, 'warehouse', 'Кладовщик. Приём партий, пересчёт, отправка на ОТК.', false, NOW()),
  (5, 'qa', 'Контролёр ОТК. Инспекция партий, фиксация брака.', false, NOW()),
  (6, 'viewer', 'Наблюдатель. Только просмотр отчётов.', true, NOW())
ON CONFLICT (id) DO UPDATE SET 
  role_name = EXCLUDED.role_name,
  description = EXCLUDED.description;

-- Сброс sequence
SELECT setval('roles_id_seq', (SELECT MAX(id) FROM roles));

-- ============================================
-- 2. УЧАСТКИ (AREAS)
-- ============================================
INSERT INTO areas (id, name, code, is_active, bot_row_size, created_at) VALUES
  (1, 'CNC', 'CNC', true, 4, NOW()),
  (2, 'Токарный', 'LATHE', true, 3, NOW())
ON CONFLICT (id) DO UPDATE SET 
  name = EXCLUDED.name,
  code = EXCLUDED.code,
  bot_row_size = EXCLUDED.bot_row_size;

SELECT setval('areas_id_seq', (SELECT MAX(id) FROM areas));

-- ============================================
-- 3. СТАНКИ (MACHINES)
-- ============================================
INSERT INTO machines (id, name, type, min_diameter, max_diameter, is_active, is_operational, display_order, location_id, created_at) VALUES
  -- CNC участок (location_id = 1)
  (1, 'SR-32', 'CNC', 3.0, 32.0, true, true, 1, 1, NOW()),
  (2, 'SR-25', 'CNC', 3.0, 25.0, true, true, 2, 1, NOW()),
  (3, 'SR-24', 'CNC', 3.0, 24.0, true, true, 3, 1, NOW()),
  (4, 'XD-20', 'CNC', 3.0, 20.0, true, true, 4, 1, NOW()),
  (5, 'XD-26', 'CNC', 3.0, 26.0, true, true, 5, 1, NOW()),
  (6, 'BT-38', 'CNC', 5.0, 38.0, true, true, 6, 1, NOW()),
  -- Токарный участок (location_id = 2)
  (7, 'MAZAK-1', 'Lathe', 10.0, 80.0, true, true, 1, 2, NOW()),
  (8, 'MAZAK-2', 'Lathe', 10.0, 80.0, true, true, 2, 2, NOW()),
  (9, 'HAAS-ST10', 'Lathe', 5.0, 50.0, true, true, 3, 2, NOW())
ON CONFLICT (id) DO UPDATE SET 
  name = EXCLUDED.name,
  type = EXCLUDED.type,
  display_order = EXCLUDED.display_order,
  location_id = EXCLUDED.location_id;

SELECT setval('machines_id_seq', (SELECT MAX(id) FROM machines));

-- ============================================
-- 4. СОТРУДНИКИ (EMPLOYEES)
-- ============================================
-- ВАЖНО: telegram_id должен быть уникальным, для демо используем фиктивные ID
INSERT INTO employees (id, telegram_id, username, full_name, role_id, is_active, factory_number, default_area_id, created_at) VALUES
  -- Администратор
  (1, 100000001, 'demo_admin', 'Демо Админ', 1, true, 'DEMO-001', NULL, NOW()),
  -- Наладчики
  (2, 100000002, 'ivan_machinist', 'Иван Петров', 3, true, 'DEMO-002', 1, NOW()),
  (3, 100000003, 'sergey_machinist', 'Сергей Иванов', 3, true, 'DEMO-003', 2, NOW()),
  -- Операторы
  (4, 100000004, 'anton_operator', 'Антон Сидоров', 2, true, 'DEMO-004', 1, NOW()),
  (5, 100000005, 'maria_operator', 'Мария Козлова', 2, true, 'DEMO-005', 1, NOW()),
  (6, 100000006, 'dmitry_operator', 'Дмитрий Смирнов', 2, true, 'DEMO-006', 2, NOW()),
  -- Кладовщик
  (7, 100000007, 'elena_warehouse', 'Елена Волкова', 4, true, 'DEMO-007', NULL, NOW()),
  -- ОТК
  (8, 100000008, 'olga_qa', 'Ольга Новикова', 5, true, 'DEMO-008', NULL, NOW()),
  (9, 100000009, 'alexey_qa', 'Алексей Морозов', 5, true, 'DEMO-009', NULL, NOW())
ON CONFLICT (id) DO UPDATE SET 
  full_name = EXCLUDED.full_name,
  role_id = EXCLUDED.role_id,
  factory_number = EXCLUDED.factory_number;

SELECT setval('employees_id_seq', (SELECT MAX(id) FROM employees));

-- ============================================
-- 5. СТАТУСЫ НАЛАДОК (SETUP_STATUSES)
-- ============================================
INSERT INTO setup_statuses (id, status_name, description, created_at) VALUES
  (1, 'setting_up', 'Идёт наладка станка', NOW()),
  (2, 'running', 'Станок работает, производство идёт', NOW()),
  (3, 'paused', 'Производство приостановлено', NOW()),
  (4, 'completed', 'Наладка завершена', NOW()),
  (5, 'cancelled', 'Наладка отменена', NOW())
ON CONFLICT (id) DO UPDATE SET 
  status_name = EXCLUDED.status_name,
  description = EXCLUDED.description;

SELECT setval('setup_statuses_id_seq', (SELECT MAX(id) FROM setup_statuses));

-- ============================================
-- 6. ДЕТАЛИ (PARTS)
-- ============================================
INSERT INTO parts (id, drawing_number, material, recommended_diameter, profile_type, part_length, description, created_at) VALUES
  (1, 'DWG-001', 'Сталь 45', 20.0, 'round', 45.5, 'Втулка направляющая', NOW()),
  (2, 'DWG-002', 'Латунь Л63', 15.0, 'round', 30.0, 'Гайка накидная M12', NOW()),
  (3, 'DWG-003', 'Алюминий Д16Т', 25.0, 'round', 60.0, 'Корпус клапана', NOW()),
  (4, 'DWG-004', 'Сталь 40Х', 32.0, 'round', 80.0, 'Вал приводной', NOW()),
  (5, 'DWG-005', 'Бронза БрАЖ9-4', 18.0, 'round', 25.0, 'Втулка подшипника', NOW()),
  (6, 'DWG-006', 'Титан ВТ6', 12.0, 'round', 35.0, 'Штифт фиксирующий', NOW()),
  (7, 'DWG-007', 'Сталь 20', 28.0, 'hex', 50.0, 'Болт специальный M20', NOW()),
  (8, 'DWG-008', 'Нержавейка 12Х18Н10Т', 16.0, 'round', 40.0, 'Штуцер соединительный', NOW())
ON CONFLICT (id) DO UPDATE SET 
  drawing_number = EXCLUDED.drawing_number,
  material = EXCLUDED.material;

SELECT setval('parts_id_seq', (SELECT MAX(id) FROM parts));

-- ============================================
-- 7. ЛОТЫ (LOTS) - разные статусы
-- ============================================
INSERT INTO lots (id, part_id, lot_number, total_planned_quantity, initial_planned_quantity, status, actual_diameter, created_at) VALUES
  -- Активные в производстве
  (1, 1, 'LOT-2024-001', 500, 500, 'in_production', 20.0, NOW() - INTERVAL '5 days'),
  (2, 2, 'LOT-2024-002', 1000, 1000, 'in_production', 15.0, NOW() - INTERVAL '4 days'),
  (3, 3, 'LOT-2024-003', 300, 300, 'in_production', 25.0, NOW() - INTERVAL '3 days'),
  -- На контроле ОТК
  (4, 4, 'LOT-2024-004', 200, 200, 'pending_qc', 32.0, NOW() - INTERVAL '7 days'),
  (5, 5, 'LOT-2024-005', 800, 800, 'pending_qc', 18.0, NOW() - INTERVAL '6 days'),
  -- Завершённые
  (6, 6, 'LOT-2024-006', 1500, 1500, 'completed', 12.0, NOW() - INTERVAL '14 days'),
  (7, 7, 'LOT-2024-007', 400, 400, 'completed', 28.0, NOW() - INTERVAL '10 days'),
  -- Новый (ожидает)
  (8, 8, 'LOT-2024-008', 600, 600, 'pending', 16.0, NOW() - INTERVAL '1 day')
ON CONFLICT (id) DO UPDATE SET 
  lot_number = EXCLUDED.lot_number,
  status = EXCLUDED.status;

SELECT setval('lots_id_seq', (SELECT MAX(id) FROM lots));

-- ============================================
-- 8. НАЛАДКИ (SETUP_JOBS)
-- ============================================
INSERT INTO setup_jobs (id, employee_id, machine_id, lot_id, part_id, planned_quantity, status, start_time, end_time, cycle_time, created_at) VALUES
  -- Активные наладки
  (1, 2, 1, 1, 1, 500, 'running', NOW() - INTERVAL '4 hours', NULL, 45, NOW() - INTERVAL '5 days'),
  (2, 2, 2, 2, 2, 1000, 'running', NOW() - INTERVAL '3 hours', NULL, 30, NOW() - INTERVAL '4 days'),
  (3, 3, 7, 3, 3, 300, 'running', NOW() - INTERVAL '2 hours', NULL, 90, NOW() - INTERVAL '3 days'),
  -- На ОТК
  (4, 2, 3, 4, 4, 200, 'completed', NOW() - INTERVAL '6 days', NOW() - INTERVAL '5 days 20 hours', 120, NOW() - INTERVAL '7 days'),
  (5, 3, 8, 5, 5, 800, 'completed', NOW() - INTERVAL '5 days', NOW() - INTERVAL '4 days 18 hours', 35, NOW() - INTERVAL '6 days'),
  -- Завершённые
  (6, 2, 4, 6, 6, 1500, 'completed', NOW() - INTERVAL '13 days', NOW() - INTERVAL '12 days', 20, NOW() - INTERVAL '14 days'),
  (7, 3, 9, 7, 7, 400, 'completed', NOW() - INTERVAL '9 days', NOW() - INTERVAL '8 days 12 hours', 60, NOW() - INTERVAL '10 days')
ON CONFLICT (id) DO UPDATE SET 
  status = EXCLUDED.status;

SELECT setval('setup_jobs_id_seq', (SELECT MAX(id) FROM setup_jobs));

-- ============================================
-- 9. ПАРТИИ (BATCHES) - история движения
-- ============================================
INSERT INTO batches (id, setup_job_id, lot_id, initial_quantity, current_quantity, current_location, operator_id, batch_time, warehouse_received_at, recounted_quantity, created_at) VALUES
  -- Лот 1: партии в процессе
  (1, 1, 1, 100, 100, 'warehouse_counted', 4, NOW() - INTERVAL '4 hours', NOW() - INTERVAL '3 hours', 98, NOW() - INTERVAL '4 hours'),
  (2, 1, 1, 100, 100, 'warehouse_counted', 4, NOW() - INTERVAL '3 hours', NOW() - INTERVAL '2 hours', 100, NOW() - INTERVAL '3 hours'),
  (3, 1, 1, 100, 100, 'machine', 4, NOW() - INTERVAL '1 hour', NULL, NULL, NOW() - INTERVAL '1 hour'),
  
  -- Лот 2: большой объём
  (4, 2, 2, 200, 200, 'warehouse_counted', 5, NOW() - INTERVAL '3 hours', NOW() - INTERVAL '2.5 hours', 198, NOW() - INTERVAL '3 hours'),
  (5, 2, 2, 200, 200, 'warehouse_counted', 5, NOW() - INTERVAL '2 hours', NOW() - INTERVAL '1.5 hours', 200, NOW() - INTERVAL '2 hours'),
  (6, 2, 2, 150, 150, 'machine', 5, NOW() - INTERVAL '30 minutes', NULL, NULL, NOW() - INTERVAL '30 minutes'),
  
  -- Лот 4: на ОТК (с результатами инспекции)
  (7, 4, 4, 200, 200, 'inspection', 4, NOW() - INTERVAL '5 days 22 hours', NOW() - INTERVAL '5 days 21 hours', 200, NOW() - INTERVAL '5 days 22 hours'),
  
  -- Лот 5: инспекция завершена, есть брак
  (8, 5, 5, 400, 400, 'good', 6, NOW() - INTERVAL '4 days 20 hours', NOW() - INTERVAL '4 days 19 hours', 400, NOW() - INTERVAL '4 days 20 hours'),
  (9, 5, 5, 400, 385, 'good', 6, NOW() - INTERVAL '4 days 18 hours', NOW() - INTERVAL '4 days 17 hours', 400, NOW() - INTERVAL '4 days 18 hours'),
  (10, 5, 5, 0, 15, 'defect', 6, NOW() - INTERVAL '4 days 17 hours', NOW() - INTERVAL '4 days 17 hours', NULL, NOW() - INTERVAL '4 days 17 hours'),
  
  -- Лот 6: полностью завершён
  (11, 6, 6, 500, 495, 'good', 4, NOW() - INTERVAL '12 days', NOW() - INTERVAL '11 days 22 hours', 500, NOW() - INTERVAL '12 days'),
  (12, 6, 6, 500, 500, 'good', 4, NOW() - INTERVAL '11 days 20 hours', NOW() - INTERVAL '11 days 18 hours', 500, NOW() - INTERVAL '11 days 20 hours'),
  (13, 6, 6, 500, 498, 'good', 5, NOW() - INTERVAL '11 days 16 hours', NOW() - INTERVAL '11 days 14 hours', 500, NOW() - INTERVAL '11 days 16 hours'),
  (14, 6, 6, 0, 7, 'defect', 5, NOW() - INTERVAL '11 days 14 hours', NOW() - INTERVAL '11 days 14 hours', NULL, NOW() - INTERVAL '11 days 14 hours')
ON CONFLICT (id) DO UPDATE SET 
  current_location = EXCLUDED.current_location;

SELECT setval('batches_id_seq', (SELECT MAX(id) FROM batches));

-- ============================================
-- 10. ДЕФЕКТЫ (SETUP_DEFECTS)
-- ============================================
INSERT INTO setup_defects (id, setup_job_id, defect_quantity, defect_reason, employee_id, created_at) VALUES
  (1, 5, 15, 'Царапины на поверхности', 8, NOW() - INTERVAL '4 days 17 hours'),
  (2, 6, 5, 'Отклонение диаметра', 8, NOW() - INTERVAL '11 days 22 hours'),
  (3, 6, 2, 'Сколы на резьбе', 9, NOW() - INTERVAL '11 days 14 hours')
ON CONFLICT (id) DO NOTHING;

SELECT setval('setup_defects_id_seq', (SELECT MAX(id) FROM setup_defects));

-- ============================================
-- 11. ТИПЫ МАТЕРИАЛОВ (MATERIAL_TYPES)
-- ============================================
INSERT INTO material_types (id, material_name, density_kg_per_m3, description, created_at) VALUES
  (1, 'Сталь 45', 7850, 'Конструкционная углеродистая сталь', NOW()),
  (2, 'Сталь 40Х', 7850, 'Легированная хромистая сталь', NOW()),
  (3, 'Сталь 20', 7850, 'Низкоуглеродистая сталь', NOW()),
  (4, 'Латунь Л63', 8500, 'Медно-цинковый сплав', NOW()),
  (5, 'Алюминий Д16Т', 2780, 'Дюралюминий термообработанный', NOW()),
  (6, 'Бронза БрАЖ9-4', 7600, 'Алюминиево-железная бронза', NOW()),
  (7, 'Титан ВТ6', 4430, 'Титановый сплав', NOW()),
  (8, 'Нержавейка 12Х18Н10Т', 7900, 'Аустенитная нержавеющая сталь', NOW())
ON CONFLICT (id) DO UPDATE SET 
  material_name = EXCLUDED.material_name;

SELECT setval('material_types_id_seq', (SELECT MAX(id) FROM material_types));

-- ============================================
-- 12. НАСТРОЙКИ УВЕДОМЛЕНИЙ
-- ============================================
INSERT INTO notification_settings (id, notification_type, display_name, description, category, enabled_telegram, created_at) VALUES
  (1, 'batch_created', 'Новая партия', 'Уведомление о создании новой партии', 'production', true, NOW()),
  (2, 'batch_to_warehouse', 'Партия на складе', 'Партия отправлена на склад', 'warehouse', true, NOW()),
  (3, 'batch_inspected', 'Инспекция завершена', 'ОТК завершил проверку партии', 'quality', true, NOW()),
  (4, 'defect_found', 'Обнаружен брак', 'При инспекции обнаружен брак', 'quality', true, NOW()),
  (5, 'machine_idle', 'Станок простаивает', 'Станок перешёл в режим ожидания', 'machines', true, NOW()),
  (6, 'lot_completed', 'Лот завершён', 'Производство лота полностью завершено', 'production', true, NOW())
ON CONFLICT (id) DO UPDATE SET 
  display_name = EXCLUDED.display_name;

SELECT setval('notification_settings_id_seq', (SELECT MAX(id) FROM notification_settings));

-- ============================================
-- 13. ПРАВА ДОСТУПА (ROLE_PERMISSIONS)
-- ============================================
-- Admin - полный доступ
INSERT INTO role_permissions (role_id, resource_type, resource_path, action, allowed) VALUES
  (1, 'page', '/admin', 'view', true),
  (1, 'page', '/admin/*', 'view', true),
  (1, 'page', '/batches', 'view', true),
  (1, 'page', '/quality-analytics', 'view', true),
  (1, 'api', '/api/admin/*', 'all', true)
ON CONFLICT (role_id, resource_type, resource_path, action) DO NOTHING;

-- Operator - базовый доступ
INSERT INTO role_permissions (role_id, resource_type, resource_path, action, allowed) VALUES
  (2, 'page', '/batches', 'view', true),
  (2, 'page', '/dashboard', 'view', true)
ON CONFLICT (role_id, resource_type, resource_path, action) DO NOTHING;

-- QA - доступ к инспекции и аналитике
INSERT INTO role_permissions (role_id, resource_type, resource_path, action, allowed) VALUES
  (5, 'page', '/batches', 'view', true),
  (5, 'page', '/quality-analytics', 'view', true),
  (5, 'page', '/dashboard', 'view', true)
ON CONFLICT (role_id, resource_type, resource_path, action) DO NOTHING;

-- ============================================
-- 14. ТИПЫ ЗАЯВОК КАЛЕНДАРЯ
-- ============================================
INSERT INTO calendar_request_types (id, name, name_en, description, color, is_active, is_system, created_at) VALUES
  (1, 'Отпуск', 'Vacation', 'Ежегодный оплачиваемый отпуск', '#4CAF50', true, true, NOW()),
  (2, 'Больничный', 'Sick Leave', 'Больничный лист', '#F44336', true, true, NOW()),
  (3, 'Отгул', 'Day Off', 'Отгул за переработку', '#2196F3', true, false, NOW()),
  (4, 'Командировка', 'Business Trip', 'Служебная командировка', '#FF9800', true, false, NOW())
ON CONFLICT (id) DO UPDATE SET 
  name = EXCLUDED.name;

SELECT setval('calendar_request_types_id_seq', (SELECT MAX(id) FROM calendar_request_types));

-- ============================================
-- ГОТОВО!
-- ============================================
-- Демо-данные успешно загружены.
-- 
-- Демо-аккаунты:
-- Admin:    telegram_id = 100000001, factory_number = DEMO-001
-- Operator: telegram_id = 100000004, factory_number = DEMO-004
-- QA:       telegram_id = 100000008, factory_number = DEMO-008
--
-- Для входа в Telegram бот используйте /start и введите
-- factory_number соответствующего сотрудника.
-- ============================================

SELECT 'Demo seed completed!' as status;
SELECT 'Employees: ' || COUNT(*) FROM employees;
SELECT 'Machines: ' || COUNT(*) FROM machines;
SELECT 'Lots: ' || COUNT(*) FROM lots;
SELECT 'Batches: ' || COUNT(*) FROM batches;
