#!/usr/bin/env python3
"""
Тест для отладки функции автоматического закрытия лотов
"""

import asyncio
import sys
import os
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Добавляем путь к src для импорта
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

async def test_auto_close_debug():
    """Тест для отладки автоматического закрытия"""
    
    # Создаем подключение к БД
    DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/isramat_bot"
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    db = SessionLocal()
    
    try:
        # Проверяем статус лота 987987
        result = db.execute(text("SELECT id, lot_number, status FROM lots WHERE lot_number = '987987'"))
        lot = result.fetchone()
        print(f"Лот: {lot}")
        
        # Проверяем батчи лота
        result = db.execute(text("SELECT current_location, COUNT(*) FROM batches WHERE lot_id = 40 GROUP BY current_location"))
        batches = result.fetchall()
        print(f"Батчи: {batches}")
        
        # Проверяем, что все батчи в финальных статусах
        final_statuses = ['good', 'defect', 'archived']
        all_final = all(batch[0] in final_statuses for batch in batches)
        print(f"Все батчи в финальных статусах: {all_final}")
        
        # Проверяем, что лот в статусе post_production
        lot_in_post_production = lot[2] == 'post_production'
        print(f"Лот в статусе post_production: {lot_in_post_production}")
        
        if all_final and lot_in_post_production:
            print("Условия для автоматического закрытия выполнены!")
            
            # Проверяем, что происходит при обновлении статуса
            try:
                db.execute(text("UPDATE lots SET status = 'closed' WHERE id = 40"))
                db.commit()
                print("Статус успешно обновлен!")
                
                # Проверяем результат
                result = db.execute(text("SELECT id, lot_number, status FROM lots WHERE lot_number = '987987'"))
                lot_after = result.fetchone()
                print(f"Лот после обновления: {lot_after}")
                
            except Exception as e:
                print(f"Ошибка при обновлении статуса: {e}")
                db.rollback()
        else:
            print("Условия для автоматического закрытия НЕ выполнены!")
            
    except Exception as e:
        print(f"Ошибка: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(test_auto_close_debug()) 