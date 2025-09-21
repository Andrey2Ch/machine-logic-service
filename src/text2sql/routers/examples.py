"""
API для управления качественными примерами Text2SQL
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from pydantic import BaseModel
from src.database import get_db_session
from src.text2sql.utils.sql_normalizer import (
    normalize_sql, 
    extract_table_names, 
    get_operation_type,
    is_good_question,
    calculate_quality_score,
    suggest_business_question
)

router = APIRouter(prefix="/api/text2sql/examples", tags=["text2sql-examples"])


class ExampleResponse(BaseModel):
    id: int
    normalized_sql: str
    business_question_ru: str
    business_question_en: Optional[str]
    table_names: List[str]
    operation_type: str
    quality_score: int
    created_at: str


class CreateExampleRequest(BaseModel):
    sql: str
    question_ru: str
    question_en: Optional[str] = None


class UpdateExampleRequest(BaseModel):
    question_ru: Optional[str] = None
    question_en: Optional[str] = None
    quality_score: Optional[int] = None


@router.get("/", response_model=List[ExampleResponse])
async def get_examples(
    operation_type: Optional[str] = Query(None, description="Фильтр по типу операции"),
    min_quality: int = Query(0, description="Минимальная оценка качества"),
    limit: int = Query(50, description="Лимит записей"),
    db: Session = Depends(get_db_session)
):
    """Получить список качественных примеров"""
    
    where_conditions = ["quality_score >= :min_quality"]
    params = {"min_quality": min_quality, "limit": limit}
    
    if operation_type:
        where_conditions.append("operation_type = :operation_type")
        params["operation_type"] = operation_type
    
    where_sql = " AND ".join(where_conditions)
    
    result = db.execute(text(f"""
        SELECT id, normalized_sql, business_question_ru, business_question_en,
               table_names, operation_type, quality_score, created_at
        FROM text2sql_examples
        WHERE {where_sql}
        ORDER BY quality_score DESC, created_at DESC
        LIMIT :limit
    """), params)
    
    examples = []
    for row in result:
        examples.append(ExampleResponse(
            id=row.id,
            normalized_sql=row.normalized_sql,
            business_question_ru=row.business_question_ru,
            business_question_en=row.business_question_en,
            table_names=row.table_names or [],
            operation_type=row.operation_type,
            quality_score=row.quality_score,
            created_at=row.created_at.isoformat()
        ))
    
    return examples


@router.post("/", response_model=ExampleResponse)
async def create_example(
    request: CreateExampleRequest,
    db: Session = Depends(get_db_session)
):
    """Создать новый качественный пример"""
    
    # Нормализуем SQL
    normalized_sql = normalize_sql(request.sql)
    table_names = extract_table_names(request.sql)
    operation_type = get_operation_type(request.sql)
    
    # Проверяем качество вопроса
    if not is_good_question(request.question_ru):
        raise HTTPException(
            status_code=400, 
            detail="Вопрос не соответствует бизнес-критериям качества"
        )
    
    # Рассчитываем оценку качества
    quality_score = calculate_quality_score(request.question_ru, request.sql)
    
    # Проверяем на дубликаты
    existing = db.execute(text("""
        SELECT id FROM text2sql_examples 
        WHERE normalized_sql = :normalized_sql
    """), {"normalized_sql": normalized_sql}).fetchone()
    
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Пример с таким SQL уже существует"
        )
    
    # Создаем пример
    result = db.execute(text("""
        INSERT INTO text2sql_examples 
        (normalized_sql, business_question_ru, business_question_en, 
         table_names, operation_type, quality_score)
        VALUES (:normalized_sql, :question_ru, :question_en, 
                :table_names, :operation_type, :quality_score)
        RETURNING id, created_at
    """), {
        "normalized_sql": normalized_sql,
        "question_ru": request.question_ru,
        "question_en": request.question_en,
        "table_names": table_names,
        "operation_type": operation_type,
        "quality_score": quality_score
    })
    
    row = result.fetchone()
    db.commit()
    
    return ExampleResponse(
        id=row.id,
        normalized_sql=normalized_sql,
        business_question_ru=request.question_ru,
        business_question_en=request.question_en,
        table_names=table_names,
        operation_type=operation_type,
        quality_score=quality_score,
        created_at=row.created_at.isoformat()
    )


@router.put("/{example_id}", response_model=ExampleResponse)
async def update_example(
    example_id: int,
    request: UpdateExampleRequest,
    db: Session = Depends(get_db_session)
):
    """Обновить пример"""
    
    # Получаем текущий пример
    current = db.execute(text("""
        SELECT * FROM text2sql_examples WHERE id = :id
    """), {"id": example_id}).fetchone()
    
    if not current:
        raise HTTPException(status_code=404, detail="Пример не найден")
    
    # Обновляем поля
    update_fields = {}
    if request.question_ru is not None:
        if not is_good_question(request.question_ru):
            raise HTTPException(
                status_code=400,
                detail="Вопрос не соответствует бизнес-критериям качества"
            )
        update_fields["business_question_ru"] = request.question_ru
    
    if request.question_en is not None:
        update_fields["business_question_en"] = request.question_en
    
    if request.quality_score is not None:
        if not (0 <= request.quality_score <= 10):
            raise HTTPException(
                status_code=400,
                detail="Оценка качества должна быть от 0 до 10"
            )
        update_fields["quality_score"] = request.quality_score
    
    if not update_fields:
        raise HTTPException(status_code=400, detail="Нет полей для обновления")
    
    # Выполняем обновление
    set_clause = ", ".join([f"{k} = :{k}" for k in update_fields.keys()])
    update_fields["id"] = example_id
    
    db.execute(text(f"""
        UPDATE text2sql_examples 
        SET {set_clause}, updated_at = NOW()
        WHERE id = :id
    """), update_fields)
    
    db.commit()
    
    # Возвращаем обновленный пример
    updated = db.execute(text("""
        SELECT * FROM text2sql_examples WHERE id = :id
    """), {"id": example_id}).fetchone()
    
    return ExampleResponse(
        id=updated.id,
        normalized_sql=updated.normalized_sql,
        business_question_ru=updated.business_question_ru,
        business_question_en=updated.business_question_en,
        table_names=updated.table_names or [],
        operation_type=updated.operation_type,
        quality_score=updated.quality_score,
        created_at=updated.created_at.isoformat()
    )


@router.delete("/{example_id}")
async def delete_example(
    example_id: int,
    db: Session = Depends(get_db_session)
):
    """Удалить пример"""
    
    result = db.execute(text("""
        DELETE FROM text2sql_examples WHERE id = :id
    """), {"id": example_id})
    
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Пример не найден")
    
    db.commit()
    return {"message": "Пример удален"}


@router.get("/stats")
async def get_examples_stats(db: Session = Depends(get_db_session)):
    """Получить статистику по примерам"""
    
    stats = db.execute(text("""
        SELECT 
            COUNT(*) as total,
            AVG(quality_score) as avg_quality,
            COUNT(CASE WHEN quality_score >= 8 THEN 1 END) as excellent,
            COUNT(CASE WHEN quality_score >= 6 THEN 1 END) as good,
            COUNT(CASE WHEN operation_type = 'SELECT' THEN 1 END) as selects,
            COUNT(CASE WHEN operation_type = 'UPDATE' THEN 1 END) as updates,
            COUNT(CASE WHEN operation_type = 'INSERT' THEN 1 END) as inserts,
            COUNT(CASE WHEN operation_type = 'DELETE' THEN 1 END) as deletes
        FROM text2sql_examples
    """)).fetchone()
    
    return {
        "total": stats.total,
        "avg_quality": round(stats.avg_quality, 1) if stats.avg_quality else 0,
        "excellent": stats.excellent,
        "good": stats.good,
        "by_operation": {
            "SELECT": stats.selects,
            "UPDATE": stats.updates,
            "INSERT": stats.inserts,
            "DELETE": stats.deletes
        }
    }


@router.post("/suggest")
async def suggest_question(
    sql: str,
    db: Session = Depends(get_db_session)
):
    """Предложить улучшенный бизнес-вопрос для SQL"""
    
    table_names = extract_table_names(sql)
    operation_type = get_operation_type(sql)
    suggested_question = suggest_business_question(sql, table_names, operation_type)
    
    return {
        "original_sql": sql,
        "normalized_sql": normalize_sql(sql),
        "suggested_question": suggested_question,
        "table_names": table_names,
        "operation_type": operation_type,
        "is_good_question": is_good_question(suggested_question)
    }
