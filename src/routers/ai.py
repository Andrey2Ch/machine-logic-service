"""
AI Assistant API endpoints.

Эндпоинты для AI-ассистента:
- Поиск в базе знаний (vector similarity)
- Поиск SQL примеров
- Сохранение обратной связи
- Загрузка примеров SQL
"""

import os
import hashlib
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import List, Optional
import json
import httpx

from src.database import get_ai_db_session, is_ai_database_available

# Path to schema documentation
SCHEMA_DOCS_PATH = Path(__file__).parent.parent / "text2sql" / "docs" / "schema_docs.md"

# OpenAI API для embeddings
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMBEDDING_MODEL = "text-embedding-3-small"

router = APIRouter(prefix="/ai", tags=["AI Assistant"])


# ============================================================================
# Pydantic Models
# ============================================================================

class EmbeddingSearchRequest(BaseModel):
    embedding: List[float]  # 1536-dim vector
    limit: int = 5
    threshold: float = 0.6


class KnowledgeDocument(BaseModel):
    id: int
    doc_type: str
    title: str
    content: str
    similarity: float


class SqlExample(BaseModel):
    id: int
    question: str
    sql_query: str
    similarity: float


class FeedbackRequest(BaseModel):
    message_id: int
    rating: int  # 1-5
    feedback_type: Optional[str] = None
    comment: Optional[str] = None


class ConversationCreate(BaseModel):
    user_id: Optional[str] = None  # Can be 'web-user' string
    title: Optional[str] = None


class MessageCreate(BaseModel):
    conversation_id: str  # session_id (UUID string)
    role: str
    content: str
    model_used: Optional[str] = None
    tokens_used: Optional[int] = None
    metadata: Optional[dict] = None  # sql, sqlResult, sqlError


# ============================================================================
# Knowledge Search Endpoints
# ============================================================================

@router.post("/search-knowledge", response_model=List[KnowledgeDocument])
async def search_knowledge(
    request: EmbeddingSearchRequest,
    db: Session = Depends(get_ai_db_session)
):
    """
    Поиск релевантных документов в базе знаний по vector similarity.
    
    Использует pgvector для косинусного сходства.
    """
    try:
        # Преобразуем embedding в строку для PostgreSQL
        embedding_str = f"[{','.join(map(str, request.embedding))}]"
        
        query = text("""
            SELECT 
                id,
                document_type as doc_type,
                title,
                content,
                1 - (embedding <=> CAST(:embedding AS vector)) as similarity
            FROM ai_knowledge_documents
            WHERE is_active = TRUE
              AND 1 - (embedding <=> CAST(:embedding AS vector)) > :threshold
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """)
        
        result = db.execute(query, {
            "embedding": embedding_str,
            "threshold": request.threshold,
            "limit": request.limit
        })
        
        documents = []
        for row in result:
            documents.append(KnowledgeDocument(
                id=row.id,
                doc_type=row.doc_type,
                title=row.title,
                content=row.content[:2000],  # Ограничиваем размер
                similarity=float(row.similarity)
            ))
        
        return documents
        
    except Exception as e:
        # Если pgvector не установлен или таблица не существует
        print(f"Knowledge search error: {e}")
        return []


@router.post("/search-sql-examples", response_model=List[SqlExample])
async def search_sql_examples(
    request: EmbeddingSearchRequest,
    db: Session = Depends(get_ai_db_session)
):
    """
    Поиск похожих SQL примеров по vector similarity.
    """
    try:
        embedding_str = f"[{','.join(map(str, request.embedding))}]"
        
        query = text("""
            SELECT 
                id,
                question,
                sql_query,
                1 - (question_embedding <=> CAST(:embedding AS vector)) as similarity
            FROM ai_sql_examples
            WHERE question_embedding IS NOT NULL
              AND 1 - (question_embedding <=> CAST(:embedding AS vector)) > :threshold
            ORDER BY question_embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """)
        
        result = db.execute(query, {
            "embedding": embedding_str,
            "threshold": request.threshold,
            "limit": request.limit
        })
        
        examples = []
        for row in result:
            examples.append(SqlExample(
                id=row.id,
                question=row.question,
                sql_query=row.sql_query,
                similarity=float(row.similarity)
            ))
        
        return examples
        
    except Exception as e:
        print(f"SQL examples search error: {e}")
        return []


# ============================================================================
# Conversation Management
# ============================================================================

@router.get("/conversations")
async def list_conversations(
    limit: int = 50,
    db: Session = Depends(get_ai_db_session)
):
    """Получить список бесед."""
    query = text("""
        SELECT 
            session_id::text as session_id,
            title,
            message_count,
            created_at,
            updated_at
        FROM ai_conversations
        WHERE is_archived = FALSE
        ORDER BY updated_at DESC
        LIMIT :limit
    """)
    
    result = db.execute(query, {"limit": limit})
    
    conversations = []
    for row in result:
        conversations.append({
            "session_id": row.session_id,
            "title": row.title or "Новая беседа",
            "message_count": row.message_count,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat()
        })
    
    return conversations


@router.post("/conversations")
async def create_conversation(
    request: ConversationCreate,
    db: Session = Depends(get_ai_db_session)
):
    """Создать новую беседу."""
    try:
        # user_id может быть строкой 'web-user', храним NULL
        user_id = None
        if request.user_id and request.user_id.isdigit():
            user_id = int(request.user_id)
        
        query = text("""
            INSERT INTO ai_conversations (user_id, title)
            VALUES (:user_id, :title)
            RETURNING session_id::text as session_id, created_at
        """)
        
        result = db.execute(query, {
            "user_id": user_id,
            "title": request.title or "Новая беседа"
        })
        db.commit()
        
        row = result.fetchone()
        return {
            "session_id": row.session_id,
            "created_at": row.created_at.isoformat()
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/conversations/{session_id}")
async def delete_conversation(
    session_id: str,
    db: Session = Depends(get_ai_db_session)
):
    """Удалить беседу и все её сообщения."""
    try:
        # Удаляем сообщения (каскадно удалятся через FK, но на всякий случай)
        db.execute(text("""
            DELETE FROM ai_messages 
            WHERE conversation_id IN (
                SELECT id FROM ai_conversations WHERE session_id = CAST(:session_id AS uuid)
            )
        """), {"session_id": session_id})
        
        # Удаляем беседу
        result = db.execute(text("""
            DELETE FROM ai_conversations 
            WHERE session_id = CAST(:session_id AS uuid)
            RETURNING id
        """), {"session_id": session_id})
        db.commit()
        
        deleted = result.fetchone()
        if not deleted:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        return {"status": "ok", "deleted_id": deleted.id}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/conversations/{session_id}/messages")
async def get_conversation_messages(
    session_id: str,
    db: Session = Depends(get_ai_db_session)
):
    """Получить все сообщения беседы по session_id."""
    query = text("""
        SELECT 
            m.id, m.role, m.content, m.model_used, m.tokens_used,
            m.executed_sql, m.sql_result, m.sql_error, m.created_at
        FROM ai_messages m
        JOIN ai_conversations c ON c.id = m.conversation_id
        WHERE c.session_id = CAST(:session_id AS uuid)
        ORDER BY m.created_at ASC
    """)
    
    result = db.execute(query, {"session_id": session_id})
    
    messages = []
    for row in result:
        msg = {
            "id": row.id,
            "role": row.role,
            "content": row.content,
            "model_used": row.model_used,
            "tokens_used": row.tokens_used,
            "created_at": row.created_at.isoformat()
        }
        # Добавляем metadata с SQL если есть
        if row.executed_sql or row.sql_result or row.sql_error:
            msg["metadata"] = {
                "sql": row.executed_sql,
                "sqlResult": row.sql_result,
                "sqlError": row.sql_error
            }
        messages.append(msg)
    
    return messages


@router.post("/messages")
async def create_message(
    request: MessageCreate,
    db: Session = Depends(get_ai_db_session)
):
    """Сохранить сообщение в беседу."""
    try:
        # Извлекаем SQL данные из metadata
        executed_sql = None
        sql_result = None
        sql_error = None
        
        if request.metadata:
            executed_sql = request.metadata.get("sql")
            sql_result = request.metadata.get("sqlResult")
            sql_error = request.metadata.get("sqlError")
        
        # Сначала получаем conversation_id по session_id
        conv_query = text("""
            SELECT id FROM ai_conversations WHERE session_id = CAST(:session_id AS uuid)
        """)
        conv_result = db.execute(conv_query, {"session_id": request.conversation_id})
        conv_row = conv_result.fetchone()
        
        if not conv_row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        conversation_id = conv_row.id
        
        query = text("""
            INSERT INTO ai_messages 
                (conversation_id, role, content, model_used, tokens_used, 
                 executed_sql, sql_result, sql_error)
            VALUES 
                (:conversation_id, :role, :content, :model_used, :tokens_used,
                 :executed_sql, :sql_result, :sql_error)
            RETURNING id, created_at
        """)
        
        result = db.execute(query, {
            "conversation_id": conversation_id,
            "role": request.role,
            "content": request.content,
            "model_used": request.model_used,
            "tokens_used": request.tokens_used,
            "executed_sql": executed_sql,
            "sql_result": json.dumps(sql_result) if sql_result else None,
            "sql_error": sql_error
        })
        db.commit()
        
        # Обновляем счётчик сообщений в беседе
        db.execute(text("""
            UPDATE ai_conversations 
            SET message_count = message_count + 1, updated_at = NOW()
            WHERE id = :conversation_id
        """), {"conversation_id": conversation_id})
        db.commit()
        
        row = result.fetchone()
        return {"id": row.id, "created_at": row.created_at.isoformat()}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Feedback
# ============================================================================

@router.post("/feedback")
async def submit_feedback(
    request: FeedbackRequest,
    db: Session = Depends(get_ai_db_session)
):
    """Сохранить обратную связь о сообщении."""
    try:
        query = text("""
            INSERT INTO ai_feedback (message_id, rating, feedback_type, comment)
            VALUES (:message_id, :rating, :feedback_type, :comment)
            RETURNING id
        """)
        
        result = db.execute(query, {
            "message_id": request.message_id,
            "rating": request.rating,
            "feedback_type": request.feedback_type,
            "comment": request.comment
        })
        db.commit()
        
        return {"id": result.fetchone().id, "status": "ok"}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Memory Management
# ============================================================================

@router.post("/memory")
async def save_memory(
    memory_type: str,
    content: str,
    conversation_id: Optional[str] = None,
    importance: int = 5,
    db: Session = Depends(get_ai_db_session)
):
    """Сохранить что-то в долгосрочную память AI."""
    try:
        query = text("""
            INSERT INTO ai_memory (conversation_id, memory_type, content, importance)
            VALUES (:conversation_id, :memory_type, :content, :importance)
            RETURNING id
        """)
        
        result = db.execute(query, {
            "conversation_id": conversation_id,
            "memory_type": memory_type,
            "content": content,
            "importance": importance
        })
        db.commit()
        
        return {"id": result.fetchone().id, "status": "ok"}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory")
async def get_memories(
    conversation_id: Optional[str] = None,
    memory_type: Optional[str] = None,
    limit: int = 20,
    db: Session = Depends(get_ai_db_session)
):
    """Получить сохранённые воспоминания."""
    conditions = ["(expires_at IS NULL OR expires_at > NOW())"]
    params = {"limit": limit}
    
    if conversation_id:
        conditions.append("conversation_id = :conversation_id")
        params["conversation_id"] = conversation_id
    
    if memory_type:
        conditions.append("memory_type = :memory_type")
        params["memory_type"] = memory_type
    
    query = text(f"""
        SELECT id, memory_type, content, importance, created_at
        FROM ai_memory
        WHERE {' AND '.join(conditions)}
        ORDER BY importance DESC, created_at DESC
        LIMIT :limit
    """)
    
    result = db.execute(query, params)
    
    memories = []
    for row in result:
        memories.append({
            "id": row.id,
            "memory_type": row.memory_type,
            "content": row.content,
            "importance": row.importance,
            "created_at": row.created_at.isoformat()
        })
    
    return memories


# ============================================================================
# Knowledge Loading Endpoints
# ============================================================================

class SqlExampleInput(BaseModel):
    question: str
    sql_query: str
    tables_used: Optional[List[str]] = None
    difficulty: Optional[str] = "medium"
    tags: Optional[List[str]] = None


async def get_embedding(text: str) -> List[float]:
    """Получает embedding от OpenAI API."""
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "input": text[:8000],
                "model": EMBEDDING_MODEL
            },
            timeout=30.0
        )
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail=f"OpenAI API error: {response.text}")
        data = response.json()
        return data["data"][0]["embedding"]


@router.post("/load-sql-example")
async def load_sql_example(
    example: SqlExampleInput,
    db: Session = Depends(get_ai_db_session)
):
    """
    Загрузить SQL пример с автоматической генерацией embedding.
    """
    try:
        # Проверяем, есть ли уже такой пример
        existing = db.execute(
            text("SELECT id FROM ai_sql_examples WHERE question = :question"),
            {"question": example.question}
        ).fetchone()
        
        if existing:
            return {"status": "exists", "id": existing.id, "message": "Example already exists"}
        
        # Генерируем embedding для вопроса
        embedding = await get_embedding(example.question)
        embedding_str = f"[{','.join(map(str, embedding))}]"
        
        # Вставляем
        result = db.execute(
            text("""
                INSERT INTO ai_sql_examples 
                (question, question_embedding, sql_query, tables_used, difficulty, tags, is_verified)
                VALUES (:question, CAST(:embedding AS vector), :sql_query, :tables_used, :difficulty, :tags, TRUE)
                RETURNING id
            """),
            {
                "question": example.question,
                "embedding": embedding_str,
                "sql_query": example.sql_query,
                "tables_used": example.tables_used or [],
                "difficulty": example.difficulty,
                "tags": example.tags or []
            }
        )
        db.commit()
        new_id = result.fetchone().id
        
        return {"status": "created", "id": new_id, "message": "Example created with embedding"}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/load-sql-examples-batch")
async def load_sql_examples_batch(
    examples: List[SqlExampleInput],
    db: Session = Depends(get_ai_db_session)
):
    """
    Загрузить несколько SQL примеров за раз.
    """
    results = []
    for ex in examples:
        try:
            result = await load_sql_example(ex, db)
            results.append({"question": ex.question[:50], **result})
        except Exception as e:
            results.append({"question": ex.question[:50], "status": "error", "error": str(e)})
    
    return {
        "total": len(examples),
        "created": sum(1 for r in results if r.get("status") == "created"),
        "exists": sum(1 for r in results if r.get("status") == "exists"),
        "errors": sum(1 for r in results if r.get("status") == "error"),
        "results": results
    }


@router.get("/sql-examples-count")
async def get_sql_examples_count(
    db: Session = Depends(get_ai_db_session)
):
    """Get SQL examples count."""
    result = db.execute(text("SELECT COUNT(*) as count FROM ai_sql_examples")).fetchone()
    return {"count": result.count}


# Pre-defined SQL examples (no Cyrillic in code)
SEED_SQL_EXAMPLES = [
    {
        "question": "How many machines are currently working?",
        "sql_query": """SELECT count(*) over() as total_working, m.name as machine_name, l.lot_number, e.full_name as operator
FROM setup_jobs sj 
JOIN machines m ON sj.machine_id = m.id 
JOIN lots l ON sj.lot_id = l.id 
LEFT JOIN employees e ON sj.employee_id = e.id 
JOIN parts p ON l.part_id = p.id 
WHERE sj.status = 'started' AND sj.end_time IS NULL AND m.is_active = true 
ORDER BY m.display_order""",
        "tables_used": ["setup_jobs", "machines", "lots", "employees", "parts"],
        "difficulty": "simple",
        "tags": ["machines", "status", "working"]
    },
    {
        "question": "Machine hours by operator for the month",
        "sql_query": """SELECT e.full_name, 
    SUM(b.recounted_quantity) AS parts, 
    ROUND(SUM(b.recounted_quantity * sj.cycle_time / 3600.0), 1) AS machine_hours
FROM batches b 
JOIN setup_jobs sj ON b.setup_job_id = sj.id 
JOIN employees e ON b.operator_id = e.id 
WHERE b.batch_time >= DATE_TRUNC('month', CURRENT_DATE) 
GROUP BY e.full_name 
ORDER BY machine_hours DESC""",
        "tables_used": ["batches", "setup_jobs", "employees"],
        "difficulty": "medium",
        "tags": ["machine-hours", "operators", "report"]
    },
    {
        "question": "Which lots are currently in production?",
        "sql_query": """SELECT l.lot_number, p.drawing_number, p.name as part_name, l.total_planned_quantity, l.created_at
FROM lots l 
JOIN parts p ON l.part_id = p.id 
WHERE l.status = 'in_production' 
ORDER BY l.created_at DESC""",
        "tables_used": ["lots", "parts"],
        "difficulty": "simple",
        "tags": ["lots", "production", "status"]
    },
    {
        "question": "Top 5 operators by parts count",
        "sql_query": """SELECT e.full_name, SUM(b.recounted_quantity) as total_parts, COUNT(b.id) as batch_count
FROM batches b 
JOIN employees e ON b.operator_id = e.id 
WHERE b.batch_time >= NOW() - INTERVAL '30 days' 
GROUP BY e.id, e.full_name 
ORDER BY total_parts DESC 
LIMIT 5""",
        "tables_used": ["batches", "employees"],
        "difficulty": "simple",
        "tags": ["top", "operators", "parts", "rating"]
    },
    {
        "question": "Production output by shift today",
        "sql_query": """SELECT 
    CASE 
        WHEN EXTRACT(HOUR FROM b.batch_time) BETWEEN 6 AND 17 THEN 'Shift 1 (day)' 
        ELSE 'Shift 2 (night)' 
    END as shift,
    COUNT(DISTINCT b.operator_id) as operators, 
    SUM(b.recounted_quantity) as parts, 
    ROUND(SUM(b.recounted_quantity * sj.cycle_time / 3600.0), 1) as machine_hours
FROM batches b 
JOIN setup_jobs sj ON b.setup_job_id = sj.id 
WHERE b.batch_time >= CURRENT_DATE 
GROUP BY 1 
ORDER BY 1""",
        "tables_used": ["batches", "setup_jobs"],
        "difficulty": "medium",
        "tags": ["shifts", "output", "today"]
    },
    {
        "question": "Discrepancies over 10% between operator and warehouse",
        "sql_query": """SELECT b.batch_time::date as date, 
    op.full_name as operator, 
    wh.full_name as warehouse_employee, 
    b.operator_reported_quantity, 
    b.recounted_quantity, 
    b.discrepancy_absolute, 
    ROUND(b.discrepancy_percentage, 1) as discrepancy_pct
FROM batches b 
JOIN employees op ON b.operator_id = op.id 
LEFT JOIN employees wh ON b.warehouse_employee_id = wh.id 
WHERE ABS(b.discrepancy_percentage) > 10 AND b.batch_time >= NOW() - INTERVAL '30 days' 
ORDER BY ABS(b.discrepancy_percentage) DESC""",
        "tables_used": ["batches", "employees"],
        "difficulty": "medium",
        "tags": ["discrepancy", "control", "warehouse"]
    },
    {
        "question": "Machine statistics for a specific machine",
        "sql_query": """SELECT m.name as machine, 
    COUNT(DISTINCT sj.id) as setups, 
    COUNT(DISTINCT sj.lot_id) as lots, 
    SUM(b.recounted_quantity) as total_parts, 
    ROUND(SUM(b.recounted_quantity * sj.cycle_time / 3600.0), 1) as machine_hours
FROM machines m 
LEFT JOIN setup_jobs sj ON sj.machine_id = m.id AND sj.start_time >= NOW() - INTERVAL '30 days' 
LEFT JOIN batches b ON b.setup_job_id = sj.id 
WHERE m.name = 'SR-32' 
GROUP BY m.id, m.name""",
        "tables_used": ["machines", "setup_jobs", "batches"],
        "difficulty": "medium",
        "tags": ["machine", "statistics"]
    },
    {
        "question": "Defect batches in the last month",
        "sql_query": """SELECT b.batch_time::date as date, 
    m.name as machine, 
    l.lot_number, 
    p.drawing_number, 
    b.current_quantity as defect_quantity, 
    e.full_name as operator
FROM batches b 
JOIN setup_jobs sj ON b.setup_job_id = sj.id 
JOIN machines m ON sj.machine_id = m.id 
JOIN lots l ON sj.lot_id = l.id 
JOIN parts p ON l.part_id = p.id 
LEFT JOIN employees e ON b.operator_id = e.id 
WHERE b.current_location = 'defect' AND b.batch_time >= NOW() - INTERVAL '30 days' 
ORDER BY b.batch_time DESC""",
        "tables_used": ["batches", "setup_jobs", "machines", "lots", "parts", "employees"],
        "difficulty": "medium",
        "tags": ["defects", "quality"]
    },
    {
        "question": "Defect count by operator for the month",
        "sql_query": """SELECT 
    e.full_name as operator,
    COUNT(b.id) as defect_batches,
    SUM(b.current_quantity) as total_defects
FROM batches b 
JOIN employees e ON b.operator_id = e.id 
WHERE b.current_location = 'defect' 
    AND b.batch_time >= DATE_TRUNC('month', CURRENT_DATE)
GROUP BY e.full_name 
ORDER BY total_defects DESC""",
        "tables_used": ["batches", "employees"],
        "difficulty": "simple",
        "tags": ["defects", "operators", "quality", "report"]
    },
    {
        "question": "Production vs defects comparison",
        "sql_query": """SELECT 
    e.full_name as operator,
    SUM(CASE WHEN b.current_location != 'defect' THEN b.recounted_quantity ELSE 0 END) as produced,
    SUM(CASE WHEN b.current_location = 'defect' THEN b.current_quantity ELSE 0 END) as defects,
    ROUND(100.0 * SUM(CASE WHEN b.current_location = 'defect' THEN b.current_quantity ELSE 0 END) / 
          NULLIF(SUM(CASE WHEN b.current_location != 'defect' THEN b.recounted_quantity ELSE 0 END), 0), 2) as defect_rate_pct
FROM batches b 
JOIN employees e ON b.operator_id = e.id 
WHERE b.batch_time >= DATE_TRUNC('month', CURRENT_DATE)
GROUP BY e.full_name 
ORDER BY defect_rate_pct DESC NULLS LAST""",
        "tables_used": ["batches", "employees"],
        "difficulty": "medium",
        "tags": ["defects", "production", "comparison", "rate"]
    },
]


@router.get("/seed-examples")
@router.post("/seed-examples")
async def seed_sql_examples(
    db: Session = Depends(get_ai_db_session)
):
    """
    Load pre-defined SQL examples with auto-generated embeddings.
    Call this once to populate the ai_sql_examples table.
    """
    results = []
    created = 0
    skipped = 0
    errors = 0
    
    for ex in SEED_SQL_EXAMPLES:
        try:
            # Check if exists
            existing = db.execute(
                text("SELECT id FROM ai_sql_examples WHERE question = :question"),
                {"question": ex["question"]}
            ).fetchone()
            
            if existing:
                results.append({"question": ex["question"][:40], "status": "exists"})
                skipped += 1
                continue
            
            # Generate embedding
            embedding = await get_embedding(ex["question"])
            embedding_str = f"[{','.join(map(str, embedding))}]"
            
            # Insert
            result = db.execute(
                text("""
                    INSERT INTO ai_sql_examples 
                    (question, question_embedding, sql_query, tables_used, difficulty, tags, is_verified)
                    VALUES (:question, CAST(:embedding AS vector), :sql_query, :tables_used, :difficulty, :tags, TRUE)
                    RETURNING id
                """),
                {
                    "question": ex["question"],
                    "embedding": embedding_str,
                    "sql_query": ex["sql_query"],
                    "tables_used": ex.get("tables_used", []),
                    "difficulty": ex.get("difficulty", "medium"),
                    "tags": ex.get("tags", [])
                }
            )
            db.commit()
            
            results.append({"question": ex["question"][:40], "status": "created"})
            created += 1
            
        except Exception as e:
            db.rollback()
            results.append({"question": ex["question"][:40], "status": "error", "error": str(e)})
            errors += 1
    
    return {
        "total": len(SEED_SQL_EXAMPLES),
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "results": results
    }


@router.get("/reseed-examples")
async def reseed_sql_examples(db: Session = Depends(get_ai_db_session)):
    """
    Clear all SQL examples and re-seed from scratch.
    Use this when examples need to be updated.
    """
    # 1. Clear all existing examples
    db.execute(text("DELETE FROM ai_sql_examples"))
    db.commit()
    
    # 2. Re-seed
    results = []
    created = 0
    errors = 0
    
    for ex in SEED_SQL_EXAMPLES:
        try:
            # Generate embedding
            embedding = await get_embedding(ex["question"])
            embedding_str = f"[{','.join(map(str, embedding))}]"
            
            # Insert
            db.execute(
                text("""
                    INSERT INTO ai_sql_examples 
                    (question, question_embedding, sql_query, tables_used, difficulty, tags, is_verified)
                    VALUES (:question, CAST(:embedding AS vector), :sql_query, :tables_used, :difficulty, :tags, TRUE)
                """),
                {
                    "question": ex["question"],
                    "embedding": embedding_str,
                    "sql_query": ex["sql_query"],
                    "tables_used": ex.get("tables_used", []),
                    "difficulty": ex.get("difficulty", "medium"),
                    "tags": ex.get("tags", [])
                }
            )
            db.commit()
            
            results.append({"question": ex["question"][:40], "status": "created"})
            created += 1
            
        except Exception as e:
            db.rollback()
            results.append({"question": ex["question"][:40], "status": "error", "error": str(e)})
            errors += 1
    
    return {
        "message": "Cleared and re-seeded",
        "total": len(SEED_SQL_EXAMPLES),
        "created": created,
        "errors": errors,
        "results": results
    }


# ============================================================================
# Schema Documentation Endpoint
# ============================================================================

@router.get("/schema-docs", response_class=PlainTextResponse)
async def get_schema_docs():
    """
    Return full database schema documentation.
    This is used by AI assistant to have complete knowledge of the DB structure.
    """
    if not SCHEMA_DOCS_PATH.exists():
        raise HTTPException(status_code=404, detail="Schema docs not found")
    
    return SCHEMA_DOCS_PATH.read_text(encoding="utf-8")


@router.get("/schema-docs-info")
async def get_schema_docs_info():
    """
    Return metadata about schema docs including content hash.
    Dashboard uses hash to detect changes and reload only when needed.
    """
    if not SCHEMA_DOCS_PATH.exists():
        raise HTTPException(status_code=404, detail="Schema docs not found")
    
    stat = SCHEMA_DOCS_PATH.stat()
    content = SCHEMA_DOCS_PATH.read_text(encoding="utf-8")
    content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()
    
    return {
        "hash": content_hash,  # Used by dashboard to detect changes
        "size_bytes": stat.st_size,
        "lines": len(content.splitlines()),
        "chars": len(content),
        "approx_tokens": len(content) // 4,
        "last_modified": stat.st_mtime
    }
