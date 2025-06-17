#!/usr/bin/env python3
"""
Скрипт для диагностики проблемы с дублированием батчей
"""

import sqlite3
import pandas as pd

def debug_sr26_batches():
    # Подключаемся к базе
    conn = sqlite3.connect('production.db')
    
    try:
        # Смотрим последние батчи для станка SR-26 (machine_id = 5)
        query = '''
        SELECT 
            b.id,
            b.lot_id,
            b.setup_job_id,
            b.current_location,
            b.current_quantity,
            b.batch_time,
            b.operator_id,
            c.card_number,
            l.lot_number,
            m.name as machine_name
        FROM batches b
        LEFT JOIN cards c ON c.batch_id = b.id
        LEFT JOIN lots l ON l.id = b.lot_id
        LEFT JOIN setup_jobs sj ON sj.id = b.setup_job_id
        LEFT JOIN machines m ON m.id = sj.machine_id
        WHERE m.name = "SR-26" OR b.id IN (
            SELECT DISTINCT batch_id FROM cards WHERE machine_id = 5
        )
        ORDER BY b.batch_time DESC
        LIMIT 10
        '''
        
        df = pd.read_sql_query(query, conn)
        print('=== Последние батчи для SR-26 ===')
        print(df.to_string(index=False))
        print()
        
        # Проверим наладки для SR-26
        query2 = '''
        SELECT 
            sj.id,
            sj.status,
            sj.machine_id,
            m.name as machine_name,
            p.drawing_number,
            l.lot_number,
            sj.created_at,
            sj.start_time
        FROM setup_jobs sj
        JOIN machines m ON m.id = sj.machine_id  
        JOIN parts p ON p.id = sj.part_id
        JOIN lots l ON l.id = sj.lot_id
        WHERE m.name = "SR-26"
        ORDER BY sj.created_at DESC
        LIMIT 5
        '''
        
        df2 = pd.read_sql_query(query2, conn)
        print('=== Наладки для SR-26 ===')
        print(df2.to_string(index=False))
        print()
        
        # Проверим карточки SR-26
        query3 = '''
        SELECT 
            c.card_number,
            c.status,
            c.batch_id,
            c.machine_id,
            c.last_event,
            m.name as machine_name
        FROM cards c
        JOIN machines m ON m.id = c.machine_id
        WHERE m.name = "SR-26"
        ORDER BY c.last_event DESC
        '''
        
        df3 = pd.read_sql_query(query3, conn)
        print('=== Карточки SR-26 ===')
        print(df3.to_string(index=False))
        
    finally:
        conn.close()

if __name__ == "__main__":
    debug_sr26_batches() 