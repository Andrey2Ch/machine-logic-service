"""
SQL Execution API endpoint.
Безопасное выполнение SELECT запросов для AI ассистента.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from src.database import get_db_session

router = APIRouter(prefix="/sql", tags=["SQL"])


class SQLExecuteRequest(BaseModel):
    query: str
    limit: int = 100


class SQLExecuteResponse(BaseModel):
    rows: List[Dict[str, Any]]
    row_count: int
    columns: List[str]


# Запрещённые команды (только SELECT разрешён)
FORBIDDEN_KEYWORDS = [
    'INSERT', 'UPDATE', 'DELETE', 'DROP', 'CREATE', 'ALTER', 'TRUNCATE',
    'GRANT', 'REVOKE', 'EXEC', 'EXECUTE', 'MERGE', 'CALL', 'COPY',
]


def validate_query(query: str) -> bool:
    """Проверить что запрос безопасен (только SELECT)."""
    query_upper = query.upper().strip()
    
    # Должен начинаться с SELECT или WITH (CTE)
    if not (query_upper.startswith('SELECT') or query_upper.startswith('WITH')):
        return False
    
    # Не должен содержать запрещённые команды
    for keyword in FORBIDDEN_KEYWORDS:
        # Проверяем как отдельное слово
        if f' {keyword} ' in f' {query_upper} ':
            return False
    
    return True


@router.post("/execute", response_model=SQLExecuteResponse)
async def execute_sql(
    request: SQLExecuteRequest,
    db: Session = Depends(get_db_session)
):
    """
    Выполнить SELECT SQL запрос.
    Только SELECT запросы разрешены для безопасности.
    """
    query = request.query.strip()
    
    # Валидация
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    
    if not validate_query(query):
        raise HTTPException(
            status_code=400, 
            detail="Only SELECT queries are allowed"
        )
    
    # Ограничиваем результат
    limit = min(request.limit, 1000)
    
    # Добавляем LIMIT если его нет
    query_upper = query.upper()
    if 'LIMIT' not in query_upper:
        # Убираем ; в конце если есть
        query = query.rstrip(';')
        query = f"{query} LIMIT {limit}"
    
    try:
        result = db.execute(text(query))
        
        # Получаем названия колонок
        columns = list(result.keys()) if result.keys() else []
        
        # Получаем строки
        rows = []
        for row in result:
            row_dict = {}
            for i, col in enumerate(columns):
                value = row[i]
                # Конвертируем в JSON-совместимый тип
                if hasattr(value, 'isoformat'):
                    value = value.isoformat()
                elif isinstance(value, bytes):
                    value = value.decode('utf-8', errors='replace')
                row_dict[col] = value
            rows.append(row_dict)
        
        return SQLExecuteResponse(
            rows=rows,
            row_count=len(rows),
            columns=columns
        )
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"SQL Error: {str(e)}")
