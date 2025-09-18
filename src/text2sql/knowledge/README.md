Структура knowledge/

knowledge/
- harvest/
  - machine-logic-service/
    - sql_raw/
    - pairs/
      - select/
      - dml/
  - isramat-dashboard/
    - sql_raw/
    - pairs/
      - select/
      - dml/
  - TG_bot/
    - sql_raw/
    - pairs/
      - select/
      - dml/
  - MTConnect/
    - sql_raw/
    - pairs/
      - select/
      - dml/
- glossary.md
- views/
- index/

Формат pairs/*.jsonl: одна строка = объект с ключами
{ question_ru, question_en, sql, language, tags, source_project, source_path, is_dml, checksum }


