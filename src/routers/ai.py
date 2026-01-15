"""
AI Assistant API endpoints.

Эндпоинты для AI-ассистента:
- Поиск в базе знаний (vector similarity)
- Поиск SQL примеров
- Сохранение обратной связи
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import List, Optional
import json

from src.database import get_ai_db_session, is_ai_database_available

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
