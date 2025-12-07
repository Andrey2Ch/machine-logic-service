"""
Диагностический скрипт для проверки батчей на переборку от операторов
"""
import sys
import os
from datetime import date, timedelta
from sqlalchemy import text, create_engine
from sqlalchemy.orm import sessionmaker, Session
from pydantic_settings import BaseSettings

class DatabaseSettings(BaseSettings):
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/isramat_bot"
    
    class Config:
        env_file = '.env'
        extra = 'ignore'

def main():
    db_settings = DatabaseSettings()
    engine = create_engine(db_settings.DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    try:
        print("=" * 80)
        print("ДИАГНОСТИКА: Батчи на переборку от операторов")
        print("=" * 80)
        print()
        
        # 1. Проверяем общее количество батчей с current_location IN ('sorting', 'sorting_warehouse')
        print("1. Батчи на переборку от операторов:")
        query1a = text("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN parent_batch_id IS NULL THEN 1 END) as parent_batches,
                COUNT(CASE WHEN parent_batch_id IS NOT NULL THEN 1 END) as child_batches
            FROM batches
            WHERE current_location = 'sorting'
        """)
        result1a = db.execute(query1a).fetchone()
        print(f"   Батчи с current_location = 'sorting' (не приняты на складе):")
        print(f"   - Всего: {result1a.total} (родительских: {result1a.parent_batches}, дочерних: {result1a.child_batches})")
        
        query1b = text("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN parent_batch_id IS NULL THEN 1 END) as parent_batches,
                COUNT(CASE WHEN parent_batch_id IS NOT NULL THEN 1 END) as child_batches
            FROM batches
            WHERE current_location = 'sorting_warehouse'
        """)
        result1b = db.execute(query1b).fetchone()
        print(f"   Батчи с current_location = 'sorting_warehouse' (приняты на складе):")
        print(f"   - Всего: {result1b.total} (родительских: {result1b.parent_batches}, дочерних: {result1b.child_batches})")
        
        query1c = text("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN parent_batch_id IS NULL THEN 1 END) as parent_batches
            FROM batches
            WHERE current_location IN ('sorting', 'sorting_warehouse')
        """)
        result1c = db.execute(query1c).fetchone()
        print(f"   ИТОГО батчей на переборку: {result1c.total} (родительских: {result1c.parent_batches})")
        print()
        
        # 2. Проверяем батчи с разными статусами
        print("2. Распределение батчей по current_location:")
        query2 = text("""
            SELECT 
                current_location,
                COUNT(*) as count,
                COUNT(CASE WHEN parent_batch_id IS NULL THEN 1 END) as parent_count
            FROM batches
            GROUP BY current_location
            ORDER BY count DESC
        """)
        results2 = db.execute(query2).fetchall()
        for row in results2:
            print(f"   {row.current_location}: {row.count} (родительских: {row.parent_count})")
        print()
        
        # 3. Проверяем батчи с original_location = 'sorting' или 'pending_rework'
        print("3. Батчи с original_location IN ('sorting', 'pending_rework'):")
        query3 = text("""
            SELECT 
                original_location,
                COUNT(*) as count,
                COUNT(CASE WHEN parent_batch_id IS NULL THEN 1 END) as parent_count
            FROM batches
            WHERE original_location IN ('sorting', 'pending_rework')
            GROUP BY original_location
        """)
        results3 = db.execute(query3).fetchall()
        if results3:
            for row in results3:
                print(f"   {row.original_location}: {row.count} (родительских: {row.parent_count})")
        else:
            print("   Нет батчей с original_location = 'sorting' или 'pending_rework'")
        print()
        
        # 4. Проверяем запрос из функции get_operator_rework_stats (исправленная версия)
        print("4. Результат запроса из get_operator_rework_stats (исправленная версия):")
        query4 = text("""
            WITH operator_rework AS (
                SELECT 
                    b.id,
                    b.initial_quantity,
                    b.current_location,
                    b.original_location,
                    b.batch_time,
                    b.parent_batch_id,
                    m.name as machine_name,
                    e.full_name as operator_name
                FROM batches b
                JOIN setup_jobs sj ON b.setup_job_id = sj.id
                JOIN machines m ON sj.machine_id = m.id
                JOIN employees e ON b.operator_id = e.id
                WHERE b.parent_batch_id IS NULL
                  AND b.current_location IN ('sorting', 'sorting_warehouse')
            )
            SELECT 
                COUNT(*) as total_batches,
                COALESCE(SUM(initial_quantity), 0) as total_parts
            FROM operator_rework
        """)
        result4 = db.execute(query4).fetchone()
        print(f"   Найдено батчей: {result4.total_batches}")
        print(f"   Всего деталей: {result4.total_parts}")
        print()
        
        # 5. Показываем примеры батчей на переборку
        print("5. Примеры батчей на переборку (первые 10):")
        query5 = text("""
            SELECT 
                b.id,
                b.current_location,
                b.original_location,
                b.batch_time,
                b.parent_batch_id,
                b.initial_quantity,
                b.warehouse_received_at,
                m.name as machine_name,
                e.full_name as operator_name
            FROM batches b
            LEFT JOIN setup_jobs sj ON b.setup_job_id = sj.id
            LEFT JOIN machines m ON sj.machine_id = m.id
            LEFT JOIN employees e ON b.operator_id = e.id
            WHERE b.current_location IN ('sorting', 'sorting_warehouse')
            ORDER BY b.batch_time DESC
            LIMIT 10
        """)
        results5 = db.execute(query5).fetchall()
        if results5:
            for row in results5:
                parent_info = "родительский" if row.parent_batch_id is None else f"дочерний (parent_id={row.parent_batch_id})"
                warehouse_info = f", принят на складе: {row.warehouse_received_at}" if row.warehouse_received_at else ", не принят на складе"
                print(f"   ID: {row.id}, {parent_info}, location: {row.current_location}, "
                      f"original: {row.original_location}, время: {row.batch_time}{warehouse_info}, "
                      f"детали: {row.initial_quantity}, станок: {row.machine_name}, оператор: {row.operator_name}")
        else:
            print("   Нет батчей на переборку (current_location IN ('sorting', 'sorting_warehouse'))")
        print()
        
        # 6. Проверяем батчи на переборку за последние 7 дней
        print("6. Батчи на переборку, созданные за последние 7 дней:")
        start_date = date.today() - timedelta(days=7)
        query6 = text("""
            SELECT 
                COUNT(*) as count,
                COUNT(CASE WHEN parent_batch_id IS NULL THEN 1 END) as parent_count
            FROM batches
            WHERE current_location IN ('sorting', 'sorting_warehouse')
              AND DATE(batch_time) >= :start_date
        """)
        result6 = db.execute(query6, {'start_date': start_date}).fetchone()
        print(f"   Всего: {result6.count} (родительских: {result6.parent_count})")
        print()
        
        # 7. Проверяем батчи с original_location = 'sorting' (старая логика)
        print("7. Батчи с original_location = 'sorting' (старая логика):")
        query7 = text("""
            SELECT 
                COUNT(*) as count,
                COUNT(CASE WHEN parent_batch_id IS NULL THEN 1 END) as parent_count
            FROM batches
            WHERE original_location = 'sorting'
        """)
        result7 = db.execute(query7).fetchone()
        print(f"   Всего: {result7.count} (родительских: {result7.parent_count})")
        print()
        
        print("=" * 80)
        print("ДИАГНОСТИКА ЗАВЕРШЕНА")
        print("=" * 80)
        
    except Exception as e:
        print(f"ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    main()
