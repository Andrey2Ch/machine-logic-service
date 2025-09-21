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


class SuggestQuestionRequest(BaseModel):
    sql: str
    language: str | None = "ru"  # 'ru' | 'en' | 'he'
    includeEnHe: bool | None = True
    style: str | None = "business"  # 'business' | 'casual' | 'technical'
    maxLen: int | None = 200
    timezone: str | None = "Asia/Jerusalem"
    dateFormat: str | None = None
    redactLiterals: bool | None = True
    stripSecrets: bool | None = True
    domain: str | None = None
    glossary: list[str] | None = None


def _sanitize_sql_literals(sql: str) -> str:
    # Грубое скрытие литералов: числа и строки → плейсхолдеры
    s = re.sub(r"'(?:''|[^'])*'", "'<значение>'", sql)
    s = re.sub(r"\b\d+\b", "<N>", s)
    return s


def _detect_kind(sql: str) -> str:
    s = sql.strip().lower()
    if s.startswith("insert"):
        return "insert"
    elif s.startswith("update"):
        return "update"
    elif s.startswith("delete"):
        return "delete"
    elif s.startswith("merge"):
        return "merge"
    return "select"


def _dml_question(sql: str, kind: str) -> tuple[str, list[str]]:
    """Генерирует вопросы для DML-запросов."""
    hints = [f"DML: {kind.upper()}"]
    
    if kind == "insert":
        # INSERT INTO table (cols) VALUES ... или INSERT INTO table SELECT ...
        m_table = re.search(r"insert\s+into\s+(\w+)", sql)
        table = m_table.group(1) if m_table else "таблицу"
        return (f"Добавить записи в {table}?", hints)
    
    elif kind == "update":
        # UPDATE table SET col=val WHERE ...
        m_table = re.search(r"update\s+(\w+)", sql)
        table = m_table.group(1) if m_table else "таблицу"
        
        # SET поля
        m_set = re.search(r"set\s+(.+?)(\s+where|$)", sql)
        if m_set:
            set_expr = m_set.group(1).strip()
            # ищем поля: col = value
            cols = re.findall(r"(\w+)\s*=", set_expr)
            if cols:
                cols_text = ", ".join(cols[:3]) + (" и др." if len(cols) > 3 else "")
                hints.append(f"SET: {cols_text}")
        
        # WHERE условие
        m_where = re.search(r"where\s+(.+)$", sql)
        if m_where:
            where_expr = m_where.group(1).strip()
            # упрощённый парсинг
            conds = []
            for match in re.finditer(r"(\w+)\s*=\s*%\([^)]+\)s", where_expr):
                conds.append(f"{match.group(1)} = <значение>")
            if conds:
                hints.append(f"WHERE: {'; '.join(conds[:2])}")
        
        return (f"Обновить {table}?", hints)
    
    elif kind == "delete":
        # DELETE FROM table WHERE ...
        m_table = re.search(r"delete\s+from\s+(\w+)", sql)
        table = m_table.group(1) if m_table else "таблицы"
        return (f"Удалить записи из {table}?", hints)
    
    else:  # merge, etc.
        return ("Что делает этот DML-запрос?", hints)


def _ru_from_sql(sql: str) -> tuple[str, list[str]]:
    """Очень простой эвристический генератор вопроса на русском из SQL."""
    hints: list[str] = []
    s = re.sub(r"\s+", " ", sql.strip(), flags=re.MULTILINE)
    low = s.lower()

    kind = _detect_kind(low)
    if kind != "select":
        return _dml_question(low, kind) 

    # SELECT list
    m_select = re.search(r"select (.+?) from ", low)
    select_expr = (m_select.group(1).strip() if m_select else "*")
    agg = None
    if re.search(r"\bcount\s*\(", select_expr):
        agg = "count"
    elif re.search(r"\bsum\s*\(", select_expr):
        agg = "sum"
    elif re.search(r"\bavg\s*\(", select_expr):
        agg = "avg"
    elif re.search(r"\bmin\s*\(", select_expr):
        agg = "min"
    elif re.search(r"\bmax\s*\(", select_expr):
        agg = "max"

    if agg == "count":
        base_q = "Сколько записей?"
    elif agg == "sum":
        base_q = "Какова сумма?"
    elif agg == "avg":
        base_q = "Каково среднее значение?"
    elif agg == "min":
        base_q = "Каково минимальное значение?"
    elif agg == "max":
        base_q = "Каково максимальное значение?"
    else:
        base_q = "Покажи"

    # FROM target(s)
    m_from = re.search(r" from (.+?)( where | group by | order by | limit |$)", low)
    from_expr = (m_from.group(1).strip() if m_from else "таблиц")
    from_expr = re.sub(r"\s+join\s+", ", ", from_expr)
    hints.append(f"FROM: {from_expr}")

    # WHERE filters
    m_where = re.search(r" where (.+?)( group by | order by | limit |$)", low)
    filters = m_where.group(1).strip() if m_where else ""
    filt_pretty = ""
    if filters:
        conds: list[str] = []
        # a = %(param)s
        for match in re.finditer(r"(\b[a-z_][a-z0-9_]*\b)\s*=\s*%\([^)]+\)s", filters):
            col = match.group(1)
            conds.append(f"{col} = <значение>")
        # IS (NOT) NULL
        for match in re.finditer(r"(\b[a-z_][a-z0-9_]*\b)\s+is\s+(not\s+)?null", filters):
            col = match.group(1)
            not_kw = match.group(2)
            conds.append(f"{col} {'не ' if not_kw else ''}пусто")
        # IN (...)
        for col in re.findall(r"(\b[a-z_][a-z0-9_]*\b)\s+in\s*\(", filters):
            conds.append(f"{col} в списке")
        if not conds:
            # fallback упрощение
            filt_pretty = re.sub(r"\s+and\s+", "; ", filters)
            filt_pretty = re.sub(r"\s+or\s+", "; ", filt_pretty)
        else:
            filt_pretty = "; ".join(conds)
        if filt_pretty:
            hints.append(f"Фильтр: {filt_pretty}")

    # GROUP BY
    if re.search(r" group by ", low):
        hints.append("Группировка: есть GROUP BY")

    # ORDER/LIMIT → ТОП-N
    m_limit = re.search(r" limit\s+(\d+)", low)
    if m_limit:
        n = m_limit.group(1)
        hints.append(f"LIMIT: {n}")
        if re.search(r" order by ", low):
            base_q = f"Топ-{n}: {base_q[0].lower() + base_q[1:]}"

    # Колонки для вывода
    cols_text = "все поля"
    if select_expr != "*":
        raw_cols = [c.strip() for c in select_expr.split(',')]
        cols = []
        for c in raw_cols[:6]:
            m_as = re.search(r"\bas\s+([a-z_][a-z0-9_]*)", c)
            if m_as:
                cols.append(m_as.group(1))
            else:
                cols.append(c.split('.')[-1])
        if cols:
            cols_text = ", ".join(cols[:4]) + (" и др." if len(cols) > 4 else "")

    # Итоговая формулировка
    from_short = from_expr.split(',')[0].strip()
    where_part = f" (где {filt_pretty})" if filt_pretty else ""
    lim_part = f" (топ-{m_limit.group(1)})" if m_limit else ""
    if agg:
        question = f"{base_q} из {from_short}{where_part}{lim_part}"
    else:
        question = f"{base_q} из {from_short} поля {cols_text}{where_part}{lim_part}"
    question = re.sub(r"\s+", " ", question).strip()
    return (question, hints)


@router.post("/suggest_question")
def suggest_question(payload: SuggestQuestionRequest):
    try:
        sql = payload.sql or ""
        if payload.stripSecrets:
            sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
            sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
        if payload.redactLiterals:
            sql = _sanitize_sql_literals(sql)

        q_ru, hints = _ru_from_sql(sql)
        # Ограничение длины
        max_len = payload.maxLen or 200
        if len(q_ru) > max_len:
            q_ru = q_ru[: max_len - 1] + "…"

        result = { "question_ru": q_ru, "hints": hints }

        if payload.includeEnHe:
            # Простые заглушки; можно позже заменить на LLM‑перевод
            result["question_en"] = "Auto-suggested question (EN) for provided SQL"
            result["question_he"] = "שאלה מוצעת אוטומטית (HE) עבור ה-SQL"

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"suggest_question failed: {e}")


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
