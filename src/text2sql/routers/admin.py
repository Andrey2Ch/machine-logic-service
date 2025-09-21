"""
Админские эндпоинты для Text2SQL.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from src.database import get_db_session
import logging
import psycopg2.extras
from .text2sql import _ru_from_sql

router = APIRouter(prefix="/api/text2sql/admin", tags=["Text2SQL Admin"])
logger = logging.getLogger(__name__)


@router.post("/generate_questions")
async def generate_questions_for_captured(
    limit: int = 100,
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
        """), {"limit": limit})
        
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
