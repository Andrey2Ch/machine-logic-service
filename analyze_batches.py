#!/usr/bin/env python3

import os
import sys
from datetime import datetime, date
sys.path.append('.')

# Добавляем переменную окружения
os.environ['DATABASE_URL'] = 'postgresql://postgres:postgres@localhost:5432/isramat_bot'

from src.database import initialize_database, get_db_session
from src.models.models import BatchDB

def main():
    # Инициализируем базу данных
    initialize_database()
    
    session = next(get_db_session())
    
    print("=== АНАЛИЗ БАТЧЕЙ В БАЗЕ ДАННЫХ ===")
    
    # Общее количество батчей
    total_batches = session.query(BatchDB).count()
    print(f"Всего батчей в базе: {total_batches}")
    
    # Батчи по статусам
    print("\n=== ПО СТАТУСАМ ===")
    statuses = session.query(BatchDB.current_location).distinct().all()
    for status_tuple in statuses:
        status = status_tuple[0]
        count = session.query(BatchDB).filter(BatchDB.current_location == status).count()
        print(f"{status}: {count}")
    
    # Батчи до сегодняшнего дня
    today = date.today()
    print(f"\n=== ДО СЕГОДНЯ ({today}) ===")
    
    old_batches = session.query(BatchDB).filter(BatchDB.created_at < today).all()
    print(f"Батчей до сегодня: {len(old_batches)}")
    
    # Группировка старых батчей по статусам
    print("\nСтарые батчи по статусам:")
    old_by_status = {}
    for batch in old_batches:
        status = batch.current_location
        if status not in old_by_status:
            old_by_status[status] = 0
        old_by_status[status] += 1
    
    for status, count in old_by_status.items():
        print(f"  {status}: {count}")
    
    # Особо важно - проверим что именно в "warehouse" статусах
    print("\n=== СКЛАДСКИЕ СТАТУСЫ ===")
    warehouse_statuses = ['warehouse', 'accepted', 'received']
    for status in warehouse_statuses:
        count = session.query(BatchDB).filter(BatchDB.current_location == status).count()
        old_count = session.query(BatchDB).filter(
            BatchDB.current_location == status,
            BatchDB.created_at < today
        ).count()
        print(f"{status}: всего {count}, старых {old_count}")
    
    # Проверим какие лоты затронуты
    print("\n=== ЗАТРОНУТЫЕ ЛОТЫ ===")
    lot_ids = session.query(BatchDB.lot_id).filter(BatchDB.created_at < today).distinct().all()
    print(f"Количество лотов с старыми батчами: {len(lot_ids)}")
    
    session.close()

if __name__ == "__main__":
    main() 