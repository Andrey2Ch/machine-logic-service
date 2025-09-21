#!/usr/bin/env python3
"""
Скрипт для анализа и дедупликации captured SQL запросов
"""
import sys
import os
from pathlib import Path

# Добавляем путь к проекту
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.text2sql.utils.sql_normalizer import (
    normalize_sql, 
    extract_table_names, 
    get_operation_type,
    is_good_question,
    calculate_quality_score,
    suggest_business_question
)
from src.database import get_db_connection
import psycopg2
from collections import defaultdict


def analyze_captured_queries():
    """Анализирует captured запросы и показывает статистику"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    print("🔍 Анализ captured SQL запросов...")
    
    # Общая статистика
    cur.execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(question_ru) as with_questions,
            COUNT(*) - COUNT(question_ru) as without_questions
        FROM text2sql_captured
    """)
    total, with_questions, without_questions = cur.fetchone()
    
    print(f"📊 Всего запросов: {total}")
    print(f"✅ С вопросами: {with_questions}")
    print(f"❌ Без вопросов: {without_questions}")
    
    # Анализ качества вопросов
    cur.execute("""
        SELECT sql, question_ru 
        FROM text2sql_captured 
        WHERE question_ru IS NOT NULL
        LIMIT 100
    """)
    
    good_questions = 0
    bad_questions = 0
    examples = []
    
    for sql, question in cur.fetchall():
        if is_good_question(question):
            good_questions += 1
            examples.append((sql, question))
        else:
            bad_questions += 1
    
    print(f"\n📈 Качество вопросов (из 100):")
    print(f"✅ Хорошие: {good_questions}")
    print(f"❌ Плохие: {bad_questions}")
    
    # Примеры плохих вопросов
    print(f"\n❌ Примеры плохих вопросов:")
    cur.execute("""
        SELECT sql, question_ru 
        FROM text2sql_captured 
        WHERE question_ru IS NOT NULL 
        AND (question_ru LIKE 'Покажи из%' OR question_ru LIKE 'Какие данные%')
        LIMIT 5
    """)
    
    for sql, question in cur.fetchall():
        print(f"  SQL: {sql[:100]}...")
        print(f"  Вопрос: {question}")
        print()
    
    # Дедупликация по нормализованному SQL
    print(f"\n🔄 Дедупликация...")
    cur.execute("""
        SELECT DISTINCT sql, question_ru
        FROM text2sql_captured 
        WHERE question_ru IS NOT NULL
    """)
    
    unique_pairs = {}
    for sql, question in cur.fetchall():
        normalized = normalize_sql(sql)
        if normalized not in unique_pairs:
            unique_pairs[normalized] = (sql, question)
    
    print(f"📊 Уникальных SQL паттернов: {len(unique_pairs)}")
    
    # Анализ по типам операций
    operation_stats = defaultdict(int)
    for normalized_sql in unique_pairs.keys():
        op_type = get_operation_type(normalized_sql)
        operation_stats[op_type] += 1
    
    print(f"\n📋 По типам операций:")
    for op_type, count in operation_stats.items():
        print(f"  {op_type}: {count}")
    
    # Анализ по таблицам
    table_stats = defaultdict(int)
    for sql, _ in unique_pairs.values():
        tables = extract_table_names(sql)
        for table in tables:
            table_stats[table] += 1
    
    print(f"\n🗂️ По таблицам (топ-10):")
    for table, count in sorted(table_stats.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  {table}: {count}")
    
    cur.close()
    conn.close()
    
    return unique_pairs


def create_quality_examples(unique_pairs):
    """Создает качественные примеры для few-shot обучения"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    print(f"\n🎯 Создание качественных примеров...")
    
    # Создаем таблицу если не существует
    cur.execute("""
        CREATE TABLE IF NOT EXISTS text2sql_examples (
            id SERIAL PRIMARY KEY,
            normalized_sql TEXT NOT NULL,
            business_question_ru TEXT NOT NULL,
            business_question_en TEXT,
            table_names TEXT[],
            operation_type VARCHAR(10),
            quality_score INTEGER DEFAULT 0,
            source_captured_id INTEGER,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    
    # Очищаем старые примеры
    cur.execute("DELETE FROM text2sql_examples")
    
    examples_added = 0
    
    for normalized_sql, (original_sql, question) in unique_pairs.items():
        # Проверяем качество вопроса
        if not is_good_question(question):
            # Предлагаем улучшенный вопрос
            table_names = extract_table_names(original_sql)
            operation_type = get_operation_type(original_sql)
            suggested_question = suggest_business_question(original_sql, table_names, operation_type)
            
            if not is_good_question(suggested_question):
                continue  # пропускаем если и предложенный плохой
            
            question = suggested_question
        
        # Рассчитываем качество
        quality_score = calculate_quality_score(question, original_sql)
        
        if quality_score >= 5:  # только качественные примеры
            table_names = extract_table_names(original_sql)
            operation_type = get_operation_type(original_sql)
            
            cur.execute("""
                INSERT INTO text2sql_examples 
                (normalized_sql, business_question_ru, table_names, operation_type, quality_score)
                VALUES (%s, %s, %s, %s, %s)
            """, (normalized_sql, question, table_names, operation_type, quality_score))
            
            examples_added += 1
    
    conn.commit()
    
    print(f"✅ Добавлено качественных примеров: {examples_added}")
    
    # Статистика по качеству
    cur.execute("""
        SELECT 
            COUNT(*) as total,
            AVG(quality_score) as avg_quality,
            COUNT(CASE WHEN quality_score >= 8 THEN 1 END) as excellent,
            COUNT(CASE WHEN quality_score >= 6 THEN 1 END) as good
        FROM text2sql_examples
    """)
    
    total, avg_quality, excellent, good = cur.fetchone()
    print(f"📊 Статистика примеров:")
    print(f"  Всего: {total}")
    print(f"  Средняя оценка: {avg_quality:.1f}")
    print(f"  Отличные (8+): {excellent}")
    print(f"  Хорошие (6+): {good}")
    
    cur.close()
    conn.close()


if __name__ == "__main__":
    print("🚀 Анализ и дедупликация captured SQL запросов")
    print("=" * 50)
    
    try:
        # Анализируем captured запросы
        unique_pairs = analyze_captured_queries()
        
        # Создаем качественные примеры
        create_quality_examples(unique_pairs)
        
        print("\n✅ Анализ завершен!")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
