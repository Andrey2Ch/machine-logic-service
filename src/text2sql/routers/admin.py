"""
Админские эндпоинты для Text2SQL.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from src.database import get_db_session
import logging
import psycopg2.extras
from pydantic import BaseModel
from .text2sql import _ru_from_sql

router = APIRouter(prefix="/api/text2sql/admin", tags=["Text2SQL Admin"])
logger = logging.getLogger(__name__)


class GenerateQuestionsRequest(BaseModel):
    limit: int = 100

@router.post("/generate_questions")
async def generate_questions_for_captured(
    request: GenerateQuestionsRequest = GenerateQuestionsRequest(),
    db: Session = Depends(get_db_session)
):
    """
    Генерирует вопросы для captured SQL queries без question_ru.
    """
    try:
        # Проверяем наличие колонок
        result = db.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'text2sql_captured' 
              AND column_name IN ('question_ru', 'question_hints', 'question_generated_at')
        """))
        existing_cols = [row[0] for row in result.fetchall()]
        
        # Добавляем недостающие колонки
        if 'question_ru' not in existing_cols:
            db.execute(text("ALTER TABLE text2sql_captured ADD COLUMN question_ru TEXT"))
        
        if 'question_hints' not in existing_cols:
            db.execute(text("ALTER TABLE text2sql_captured ADD COLUMN question_hints JSONB"))
            
        if 'question_generated_at' not in existing_cols:
            db.execute(text("ALTER TABLE text2sql_captured ADD COLUMN question_generated_at TIMESTAMP"))
        
        db.commit()
        
        # Получаем записи без вопросов
        result = db.execute(text("""
            SELECT id, sql
            FROM text2sql_captured 
            WHERE question_ru IS NULL OR question_ru = ''
            ORDER BY captured_at DESC
            LIMIT :limit
        """), {"limit": request.limit})
        
        records = result.fetchall()
        
        if not records:
            return {
                "success": True,
                "message": "Все записи уже имеют сгенерированные вопросы",
                "processed": 0,
                "errors": 0
            }
        
        processed = 0
        errors = 0
        
        for record in records:
            try:
                # Генерируем вопрос
                question_ru, hints = _ru_from_sql(record.sql)
                
                # Обновляем запись
                db.execute(text("""
                    UPDATE text2sql_captured 
                    SET question_ru = :question_ru, 
                        question_hints = :hints,
                        question_generated_at = NOW()
                    WHERE id = :record_id
                """), {
                    "question_ru": question_ru,
                    "hints": psycopg2.extras.Json(hints),
                    "record_id": record.id
                })
                processed += 1
                
            except Exception as e:
                logger.error(f"Ошибка обработки записи {record.id}: {e}")
                errors += 1
                continue
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Генерация завершена. Обработано: {processed}, ошибок: {errors}",
            "processed": processed,
            "errors": errors
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка генерации вопросов: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка генерации: {str(e)}")


@router.get("/captured_stats")
async def get_captured_stats(db: Session = Depends(get_db_session)):
    """
    Статистика по captured SQL queries.
    """
    try:
        result = db.execute(text("""
            SELECT 
                COUNT(*) as total,
                COUNT(question_ru) as with_questions,
                COUNT(*) - COUNT(question_ru) as without_questions
            FROM text2sql_captured
        """))
        
        stats = result.fetchone()
        
        return {
            "total_queries": stats.total,
            "with_questions": stats.with_questions,
            "without_questions": stats.without_questions,
            "completion_rate": round((stats.with_questions / stats.total * 100) if stats.total > 0 else 0, 1)
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка статистики: {str(e)}")


@router.post("/analyze_and_deduplicate")
async def analyze_and_deduplicate(
    db: Session = Depends(get_db_session)
):
    """Анализ и дедупликация captured SQL запросов с созданием качественных примеров"""
    try:
        from src.text2sql.utils.sql_normalizer import (
            normalize_sql, 
            extract_table_names, 
            get_operation_type,
            is_good_question,
            calculate_quality_score,
            suggest_business_question
        )
        from collections import defaultdict
        
        logger.info("🔍 Начинаем анализ captured SQL запросов...")
        
        # 1. Создаем таблицу examples если не существует
        db.execute(text("""
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
        """))
        
        # 2. Анализируем captured запросы
        stats = db.execute(text("""
            SELECT 
                COUNT(*) as total,
                COUNT(question_ru) as with_questions,
                COUNT(*) - COUNT(question_ru) as without_questions
            FROM text2sql_captured
        """)).fetchone()
        
        logger.info(f"📊 Всего запросов: {stats.total}, с вопросами: {stats.with_questions}")
        
        # 3. Получаем уникальные пары SQL-вопрос
        unique_pairs = {}
        captured_queries = db.execute(text("""
            SELECT DISTINCT sql, question_ru, id
            FROM text2sql_captured 
            WHERE question_ru IS NOT NULL
        """)).fetchall()
        
        for sql, question, captured_id in captured_queries:
            normalized = normalize_sql(sql)
            if normalized not in unique_pairs:
                unique_pairs[normalized] = (sql, question, captured_id)
        
        logger.info(f"🔄 Уникальных SQL паттернов: {len(unique_pairs)}")
        
        # 4. Очищаем старые примеры
        db.execute(text("DELETE FROM text2sql_examples"))
        
        # 5. Создаем качественные примеры
        examples_added = 0
        operation_stats = defaultdict(int)
        
        for normalized_sql, (original_sql, question, captured_id) in unique_pairs.items():
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
                
                db.execute(text("""
                    INSERT INTO text2sql_examples 
                    (normalized_sql, business_question_ru, table_names, operation_type, quality_score, source_captured_id)
                    VALUES (:normalized_sql, :question, :table_names, :operation_type, :quality_score, :captured_id)
                """), {
                    "normalized_sql": normalized_sql,
                    "question": question,
                    "table_names": table_names,
                    "operation_type": operation_type,
                    "quality_score": quality_score,
                    "captured_id": captured_id
                })
                
                examples_added += 1
                operation_stats[operation_type] += 1
        
        db.commit()
        
        # 6. Финальная статистика
        final_stats = db.execute(text("""
            SELECT 
                COUNT(*) as total,
                AVG(quality_score) as avg_quality,
                COUNT(CASE WHEN quality_score >= 8 THEN 1 END) as excellent,
                COUNT(CASE WHEN quality_score >= 6 THEN 1 END) as good
            FROM text2sql_examples
        """)).fetchone()
        
        logger.info(f"✅ Анализ завершен! Добавлено примеров: {examples_added}")
        
        return {
            "success": True,
            "message": "Анализ и дедупликация завершены",
            "captured_stats": {
                "total_queries": stats.total,
                "with_questions": stats.with_questions,
                "unique_patterns": len(unique_pairs)
            },
            "examples_stats": {
                "total": examples_added,
                "avg_quality": round(final_stats.avg_quality, 1) if final_stats.avg_quality else 0,
                "excellent": final_stats.excellent,
                "good": final_stats.good,
                "by_operation": dict(operation_stats)
            }
        }
        
    except Exception as e:
        logger.error(f"Ошибка анализа: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Ошибка анализа: {str(e)}")
