from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session
from src.database import get_db_session
from src.text2sql.services.text2sql_service import Text2SQLService
from src.text2sql.services.text2sql_metrics import Text2SQLMetrics
from src.text2sql.services.sql_validator import SQLValidator, ValidationLevel
from src.text2sql.services.llm_provider_claude import ClaudeText2SQL
import os
import re

router = APIRouter(prefix="/api/text2sql", tags=["text2sql"])

class NLQuery(BaseModel):
    question: str

class SQLValidationRequest(BaseModel):
    sql: str
    validation_level: str = "moderate"  # strict, moderate, permissive

class LLMQuery(BaseModel):
    question: str
    validation_level: str | None = None  # strict, moderate, permissive (если не задано, выберем по роли)
    return_sql_only: bool = False

class FeedbackItem(BaseModel):
    question: str
    sql: str

@router.post("/direct_query")
def direct_query(payload: NLQuery, db: Session = Depends(get_db_session)):
    svc = Text2SQLService(db)
    try:
        return svc.answer(payload.question)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка выполнения запроса: {e}")


@router.post("/feedback")
def add_feedback(item: FeedbackItem):
    """Сохраняет пару вопрос→SQL в few_shot_examples.md (для RAG-lite)."""
    try:
        base_dir = os.path.dirname(os.path.dirname(__file__))  # src/text2sql
        docs_dir = os.path.join(base_dir, "docs")
        examples_path = os.path.join(docs_dir, "few_shot_examples.md")

        block = (
            f"\nQ: {item.question.strip()}\n"
            f"SQL:\n```sql\n{item.sql.strip()}\n```\n"
        )
        os.makedirs(docs_dir, exist_ok=True)
        with open(examples_path, "a", encoding="utf-8") as f:
            f.write(block)

        return {"saved": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Feedback save failed: {e}")


@router.get("/evaluate")
def evaluate_quality(db: Session = Depends(get_db_session)):
    """Оценка качества Text2SQL на тестовых примерах"""
    metrics = Text2SQLMetrics(db)
    svc = Text2SQLService(db)
    
    # Создаем тестовые случаи
    test_cases = metrics.create_test_cases()
    
    # Генерируем предсказания для каждого случая
    for case in test_cases:
        try:
            result = svc.answer(case['question'])
            case['predicted'] = result['sql']
        except Exception as e:
            case['predicted'] = f"ERROR: {e}"
    
    # Оцениваем качество
    evaluation = metrics.evaluate_batch(test_cases)
    
    return {
        "evaluation": evaluation,
        "test_cases": test_cases
    }

@router.post("/validate_sql")
def validate_sql(payload: SQLValidationRequest):
    """Валидация SQL запроса"""
    try:
        # Преобразуем строку в ValidationLevel
        level_map = {
            "strict": ValidationLevel.STRICT,
            "moderate": ValidationLevel.MODERATE,
            "permissive": ValidationLevel.PERMISSIVE
        }
        validation_level = level_map.get(payload.validation_level, ValidationLevel.MODERATE)
        
        # Создаем валидатор
        validator = SQLValidator(validation_level)
        
        # Валидируем SQL
        result = validator.validate(payload.sql)
        
        return {
            "sql": payload.sql,
            "validation_level": payload.validation_level,
            "result": result
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка валидации: {e}")

@router.get("/security_info")
def get_security_info():
    """Информация о настройках безопасности Text2SQL"""
    return {
        "security_features": [
            "SQL validation with denylist/whitelist",
            "Automatic LIMIT enforcement",
            "Statement timeouts",
            "Readonly database user support",
            "Row Level Security (RLS)",
            "SQL injection protection"
        ],
        "validation_levels": ["strict", "moderate", "permissive"],
        "max_query_length": 1000,
        "default_limit": 100,
        "timeout_ms": 5000
    }


# --- RAG-lite загрузка контекста ---
def _read_file_utf8(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _parse_few_shot_examples(md_text: str):
    examples = []
    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        m_q = re.match(r"^\s*Q:\s*(.+)$", lines[i], re.IGNORECASE)
        if m_q:
            question = m_q.group(1).strip()
            j = i + 1
            # найти начало SQL блока
            while j < len(lines) and not re.match(r"^\s*SQL\s*:\s*$", lines[j], re.IGNORECASE):
                j += 1
            # найти fenced code ```
            if j < len(lines):
                k = j + 1
                # ожидаем ```sql (или просто ```)
                if k < len(lines) and re.match(r"^\s*```", lines[k]):
                    k += 1
                    sql_lines = []
                    while k < len(lines) and not re.match(r"^\s*```\s*$", lines[k]):
                        sql_lines.append(lines[k])
                        k += 1
                    examples.append((question, "\n".join(sql_lines).strip()))
                    i = k  # продолжим после закрывающей ```
                else:
                    # без fenced блока — соберём до пустой строки
                    sql_lines = []
                    while k < len(lines) and lines[k].strip():
                        sql_lines.append(lines[k])
                        k += 1
                    if sql_lines:
                        examples.append((question, "\n".join(sql_lines).strip()))
                        i = k
            else:
                i = j
        i += 1
    return examples


@router.post("/llm_query")
async def llm_query(payload: LLMQuery,
                    db: Session = Depends(get_db_session),
                    x_user_role: str | None = Header(default=None)):
    """LLM‑режим: мульти-языковые вопросы → SQL (Claude), валидация и опциональное выполнение."""
    # Подготовка контекста
    base_dir = os.path.dirname(os.path.dirname(__file__))  # src/text2sql
    docs_dir = os.path.join(base_dir, "docs")
    schema_path = os.path.join(docs_dir, "schema_docs.md")
    examples_path = os.path.join(docs_dir, "few_shot_examples.md")

    schema_docs = _read_file_utf8(schema_path)
    few_shot_md = _read_file_utf8(examples_path)
    examples = _parse_few_shot_examples(few_shot_md)

    # Генерация SQL через Claude
    llm = ClaudeText2SQL()
    try:
        raw_sql = await llm.generate_sql(payload.question, schema_docs, examples)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM error: {e}")

    # Определяем уровень валидации с учётом роли
    level_map = {
        "strict": ValidationLevel.STRICT,
        "moderate": ValidationLevel.MODERATE,
        "permissive": ValidationLevel.PERMISSIVE,
    }
    role = (x_user_role or "").strip().lower()
    if payload.validation_level is None:
        # По умолчанию: admin → permissive, остальные → strict
        chosen_level = ValidationLevel.PERMISSIVE if role == "admin" else ValidationLevel.STRICT
    else:
        chosen_level = level_map.get(payload.validation_level, ValidationLevel.MODERATE)

    validator = SQLValidator(chosen_level)
    result = validator.validate(raw_sql)
    if not result["valid"]:
        return {
            "sql": raw_sql,
            "validation": result,
            "error": "SQL validation failed",
        }

    if payload.return_sql_only:
        return {
            "sql": raw_sql,
            "validated_sql": result["sanitized_sql"],
            "validation": result,
        }

    # Выполнение
    try:
        svc = Text2SQLService(db)
        cols, rows = svc.execute(result["sanitized_sql"]) 
        return {
            "sql": raw_sql,
            "validated_sql": result["sanitized_sql"],
            "validation": result,
            "columns": cols,
            "rows": rows,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка выполнения запроса: {e}")
