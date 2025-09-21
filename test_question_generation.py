#!/usr/bin/env python3
"""
Тест генерации вопросов локально.
"""
import sys
import os

# Добавляем путь к src
sys.path.append('src')

# Импортируем функцию
from text2sql.routers.text2sql import _ru_from_sql

# Тестовые SQL запросы
test_queries = [
    # SELECT с WHERE и LIMIT
    """SELECT parts.id AS parts_id, parts.drawing_number AS parts_drawing_number, parts.material AS parts_material, parts.created_at AS parts_created_at 
FROM parts 
WHERE parts.drawing_number = %(drawing_number_1)s 
LIMIT %(param_1)s""",
    
    # SELECT с JOIN и IS NULL
    """SELECT setup_jobs.id AS setup_jobs_id, setup_jobs.employee_id AS setup_jobs_employee_id, setup_jobs.machine_id AS setup_jobs_machine_id 
FROM setup_jobs 
WHERE setup_jobs.part_id = %(part_id_1)s AND setup_jobs.end_time IS NULL""",
    
    # UPDATE
    """UPDATE setup_jobs SET cycle_time=%(cycle_time)s WHERE setup_jobs.id = %(setup_jobs_id)s""",
    
    # INSERT
    """INSERT INTO batches (lot_id, quantity, status) VALUES (%(lot_id)s, %(qty)s, 'production')""",
    
    # COUNT
    """SELECT COUNT(*) as total_batches FROM batches WHERE current_location = 'production'""",
    
    # DELETE
    """DELETE FROM cards WHERE status = 'expired' AND last_updated < %(cutoff_date)s"""
]

def main():
    print("🧪 Тестирование генерации вопросов из SQL\n")
    
    for i, sql in enumerate(test_queries, 1):
        print(f"--- Тест {i} ---")
        print(f"SQL: {sql[:60]}...")
        
        try:
            question, hints = _ru_from_sql(sql)
            print(f"✅ Вопрос: {question}")
            print(f"💡 Подсказки: {hints}")
        except Exception as e:
            print(f"❌ Ошибка: {e}")
        
        print()

if __name__ == "__main__":
    main()
