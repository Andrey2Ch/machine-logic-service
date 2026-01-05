#!/usr/bin/env python3
"""
Генерирует schema_docs.md из Prisma схемы без подключения к БД.
Использование: python generate_schema_docs_from_prisma.py
"""

import os
import re
import sys

# Путь к Prisma schema
PRISMA_SCHEMA_PATH = os.environ.get('PRISMA_SCHEMA_PATH') or r'C:\Projects\isramat-dashboard\prisma\schema.prisma'
OUTPUT_PATH = os.environ.get('SCHEMA_MD_OUT') or os.path.join(os.path.dirname(__file__), '..', 'docs', 'schema_docs.md')

# Маппинг типов Prisma -> PostgreSQL
PRISMA_TO_PG_TYPE = {
    'Int': 'integer',
    'BigInt': 'bigint',
    'Float': 'double precision',
    'Decimal': 'numeric',
    'Boolean': 'boolean',
    'String': 'character varying',
    'DateTime': 'timestamp without time zone',
    'Json': 'jsonb',
    'Bytes': 'bytea',
}

def parse_prisma_schema(schema_path: str) -> dict:
    """Парсит Prisma схему и возвращает структуру таблиц."""
    with open(schema_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    tables = {}
    
    # Разбиваем на блоки model
    model_pattern = re.compile(r'model\s+(\w+)\s*\{([^}]+)\}', re.MULTILINE | re.DOTALL)
    
    for match in model_pattern.finditer(content):
        model_name = match.group(1)
        model_body = match.group(2)
        
        # Ищем @@map для определения имени таблицы
        map_match = re.search(r'@@map\(["\']([^"\']+)["\']\)', model_body)
        table_name = map_match.group(1) if map_match else model_name.lower()
        
        columns = []
        
        # Парсим поля модели
        for line in model_body.split('\n'):
            line = line.strip()
            if not line or line.startswith('//') or line.startswith('@@'):
                continue
            
            # Пропускаем связи (содержат [] или @relation)
            if '[]' in line or '@relation' in line:
                continue
            
            # Парсим поле: field_name Type? @attributes
            field_match = re.match(r'^(\w+)\s+(\w+)(\?)?(.*)$', line)
            if not field_match:
                continue
            
            field_name = field_match.group(1)
            prisma_type = field_match.group(2)
            is_optional = field_match.group(3) == '?'
            attributes = field_match.group(4) or ''
            
            # Определяем PostgreSQL тип
            pg_type = PRISMA_TO_PG_TYPE.get(prisma_type, 'text')
            
            # Проверяем специфичные атрибуты типа
            if '@db.VarChar' in attributes:
                pg_type = 'character varying'
            elif '@db.Text' in attributes:
                pg_type = 'text'
            elif '@db.Timestamp' in attributes:
                pg_type = 'timestamp without time zone'
            elif '@db.Timestamptz' in attributes:
                pg_type = 'timestamp with time zone'
            elif '@db.Date' in attributes:
                pg_type = 'date'
            elif '@db.ByteA' in attributes:
                pg_type = 'bytea'
            elif '@db.Decimal' in attributes:
                pg_type = 'numeric'
            elif 'String[]' in line:
                pg_type = 'ARRAY'
            
            # Определяем nullable
            nullable = 'YES' if is_optional else 'NO'
            
            # Генерируем описание
            description = ''
            if '@id' in attributes:
                description = 'Primary key'
            elif '@unique' in attributes:
                description = 'Unique identifier'
            
            columns.append({
                'name': field_name,
                'type': pg_type,
                'nullable': nullable,
                'description': description
            })
        
        if columns:
            tables[table_name] = columns
    
    return tables

def generate_schema_docs(tables: dict, output_path: str):
    """Генерирует schema_docs.md из структуры таблиц."""
    
    # Читаем бизнес-логику из существующего файла, если есть
    business_logic = """## Бизнес-логика системы

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

"""
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("# Schema documentation for schema `public`\n\n")
        f.write(business_logic)
        f.write("## Таблицы\n\n")
        
        for table_name in sorted(tables.keys()):
            columns = tables[table_name]
            f.write(f"## {table_name}\n\n")
            f.write("| column | type | nullable | description |\n")
            f.write("|---|---|---|---|\n")
            
            for col in columns:
                desc = col['description'].replace('|', '\\|') if col['description'] else ''
                f.write(f"| {col['name']} | {col['type']} | {col['nullable']} | {desc} |\n")
            
            f.write("\n")
    
    print(f"Generated schema_docs.md with {len(tables)} tables at: {output_path}")

def main():
    print(f"Reading Prisma schema from: {PRISMA_SCHEMA_PATH}")
    
    if not os.path.exists(PRISMA_SCHEMA_PATH):
        print(f"Error: Prisma schema not found at {PRISMA_SCHEMA_PATH}")
        return 1
    
    tables = parse_prisma_schema(PRISMA_SCHEMA_PATH)
    print(f"Parsed {len(tables)} tables from Prisma schema")
    
    generate_schema_docs(tables, OUTPUT_PATH)
    return 0

if __name__ == '__main__':
    sys.exit(main())
