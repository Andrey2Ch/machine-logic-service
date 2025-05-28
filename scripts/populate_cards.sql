-- Заполнение таблицы cards начальными данными
-- 20 карточек для каждого активного станка
INSERT INTO cards (card_number, machine_id, status, last_event)
SELECT 
    generate_series(1, 20) as card_number,
    m.id as machine_id,
    'free' as status,
    NOW() as last_event
FROM machines m
WHERE m.is_active = true
ON CONFLICT (card_number, machine_id) DO NOTHING;

-- Проверка результата
SELECT 
    m.name as machine_name,
    COUNT(c.card_number) as cards_count,
    COUNT(CASE WHEN c.status = 'free' THEN 1 END) as free_cards
FROM machines m
LEFT JOIN cards c ON m.id = c.machine_id
WHERE m.is_active = true
GROUP BY m.id, m.name
ORDER BY m.name; 