-- 037: Stoppage reasons reference table (רשימת תקלות / Коды остановок)
-- Parsed from factory floor standard fault code sheet.
-- Categories: machine (1-17), part (30-49), work_and_material (70-75)

BEGIN;

CREATE TABLE IF NOT EXISTS stoppage_reasons (
    code        INTEGER PRIMARY KEY,
    category    TEXT NOT NULL CHECK (category IN ('machine', 'part', 'work_and_material')),
    name_he     TEXT NOT NULL,
    name_ru     TEXT NOT NULL,
    name_en     TEXT NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE stoppage_reasons IS 'Machine stoppage/fault reason codes (רשימת תקלות). Used in production reporting.';

INSERT INTO stoppage_reasons (code, category, name_he, name_ru, name_en) VALUES
-- ═══════════════════════════════════════════════
-- Category: machine (станок / מכונה)  codes 1-16
-- ═══════════════════════════════════════════════
( 1, 'machine', 'כיוון מכונה',             'Настройка станка на новую деталь',   'Machine setup for new part'),
( 2, 'machine', 'כיוון מוביל',             'Подстройка люнета',                  'Steady rest adjustment'),
( 3, 'machine', 'תקלה במכונה',             'Неполадка в станке',                 'Machine malfunction'),
( 4, 'machine', 'אחזקת מכונה',             'Тех. уход за станком',               'Machine maintenance'),
( 5, 'machine', 'ניקוי מכונה',             'Уборка станка',                      'Machine cleaning'),
( 6, 'machine', 'תקלה באלקטרוניקה',        'Неполадки в электронной части',       'Electronics malfunction'),
( 7, 'machine', 'תקלה בהזנת חומר',         'Неполадки в подаче прутка',           'Bar feeder malfunction'),
( 8, 'machine', 'נשבר סכין',               'Сломался резец',                     'Broken cutting tool'),
( 9, 'machine', 'נשבר מקדח',               'Сломалось сверло',                   'Broken drill'),
(10, 'machine', 'נשבר מברז',               'Сломался метчик',                    'Broken tap'),
(11, 'machine', 'נשבר כרסום',              'Сломалась фреза',                    'Broken milling cutter'),
(12, 'machine', 'החלפת לוחית חריטה',       'Замена пластинки',                   'Insert replacement'),
(13, 'machine', 'נשבר קרנר',               'Сломался кернер',                    'Broken center punch'),
(14, 'machine', 'השחזת כלי',               'Заточка инструмента',                'Tool sharpening'),
(15, 'machine', 'נשבר כלי',                'Сломался инструмент',                'Broken tool (generic)'),
(16, 'machine', 'נשברה מחרוקת',            'Сломалась плашка',                   'Broken die'),

-- ═══════════════════════════════════════════════
-- Category: part (детали / חלקים)  codes 30-49
-- ═══════════════════════════════════════════════
(30, 'part', 'בעיית עוקץ',                         'Проблемы с отрезкой (чупчик)',                      'Cut-off burr problem'),
(31, 'part', 'בעיית גרד',                          'Проблемы с заусенцем',                              'Burr / deburring issue'),
(32, 'part', 'חריגה בגימור שטח',                    'Отклонение от требований по чистоте поверхности',    'Surface finish deviation'),
(33, 'part', 'בעיית חיספוס',                        'Проблемы с накаткой',                               'Knurling problem'),
(34, 'part', 'חריץ לא במרכז',                       'Канавка не в центре',                               'Groove not centered'),
(35, 'part', 'חריגה באורך +/-',                     'Отклонение от линейных размеров',                   'Length deviation +/-'),
(36, 'part', 'חריגה בקוטר +/-',                     'Отклонение от диаметра',                            'Diameter deviation +/-'),
(37, 'part', 'חריגה במרכוז',                        'Отклонение от центровки',                           'Centering deviation'),
(38, 'part', 'חריגה בפאזה',                         'Отклонение от фаски',                               'Chamfer deviation'),
(39, 'part', 'בעיית מקבילות',                       'Проблема с параллельностью',                        'Parallelism problem'),
(40, 'part', 'מדיד לקדה GO לא עובר',                'Калибр "пробка" GO не проходит',                    'Plug gauge GO does not pass'),
(41, 'part', 'מדיד לקדה NO-GO עובר',                'Калибр "пробка" NO-GO проходит',                    'Plug gauge NO-GO passes'),
(42, 'part', 'נשברים חלקים',                        'Ломаются детали',                                   'Parts breaking'),
(43, 'part', 'מדיד הברגה פנימית GO לא עובר',        'Резьбовой внутренний калибр GO не проходит',         'Internal thread gauge GO fails'),
(44, 'part', 'מדיד הברגה פנימית NOGO עובר',         'Резьбовой внутренний калибр NO-GO проходит',         'Internal thread gauge NO-GO passes'),
(45, 'part', 'מדיד הברגה חיצונית GO לא עובר',       'Калибр резьбовой наружный GO не проходит',           'External thread gauge GO fails'),
(46, 'part', 'מדיד הברגה חיצונית NOGO עובר',        'Калибр резьбовой наружный NO-GO проходит',           'External thread gauge NO-GO passes'),
(47, 'part', 'אין הברגה',                           'Нет резьбы',                                        'No thread produced'),
(48, 'part', 'אורך ההברגה מעל הדרישה',              'Длина резьбы не в размере (больше)',                 'Thread length above spec'),
(49, 'part', 'אורך ההברגה מתחת הדרישה',             'Длина резьбы не в размере (меньше)',                 'Thread length below spec'),

-- ═══════════════════════════════════════════════
-- Category: work_and_material (работа и материал / עבודה וחומר)  codes 70-74
-- ═══════════════════════════════════════════════
(70, 'work_and_material', 'תחילת הזמנה',              'Начало производства (работы)',      'Production start (job start)'),
(71, 'work_and_material', 'סוף הזמנה',                'Конец производства (работы)',       'Production end (job end)'),
(72, 'work_and_material', 'גמר החומר',                'Закончился материал',              'Material depleted'),
(73, 'work_and_material', 'חוסר חומר',                'Нет материала',                    'Material unavailable'),
(74, 'work_and_material', 'חומר לא מגיע מהמחסון',     'Склад не поставил материал',        'Warehouse did not deliver material')

ON CONFLICT (code) DO UPDATE SET
    category = EXCLUDED.category,
    name_he  = EXCLUDED.name_he,
    name_ru  = EXCLUDED.name_ru,
    name_en  = EXCLUDED.name_en;

INSERT INTO schema_migrations (version, applied_at)
VALUES ('037_stoppage_reasons', NOW())
ON CONFLICT (version) DO NOTHING;

COMMIT;
