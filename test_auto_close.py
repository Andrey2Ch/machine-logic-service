#!/usr/bin/env python3
"""
Простой тест для проверки автоматического закрытия лотов
"""

import asyncio
import sys
import os
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Добавляем путь к src для импорта
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.main import check_lot_auto_completion

async def test_auto_close():
    """Тестирует автоматическое закрытие лота 159"""
    
    # Создаем подключение к БД
    DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/isramat_bot"
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    db = SessionLocal()
    
    try:
        # Проверяем статус лота 159 до теста
        result = db.execute(text("SELECT id, lot_number, status FROM lots WHERE lot_number = '159'"))
        lot = result.fetchone()
        print(f"До теста - Лот {lot[1]}: статус {lot[2]}")
        
        # Вызываем функцию автоматического закрытия
        await check_lot_auto_completion(lot[0], db)
        
        # Проверяем статус после теста
        result = db.execute(text("SELECT id, lot_number, status FROM lots WHERE lot_number = '159'"))
        lot = result.fetchone()
        print(f"После теста - Лот {lot[1]}: статус {lot[2]}")
        
        if lot[2] == 'closed':
            print("✅ Автоматическое закрытие работает!")
        else:
            print("❌ Автоматическое закрытие не сработало")
            
    except Exception as e:
        print(f"Ошибка: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(test_auto_close()) 