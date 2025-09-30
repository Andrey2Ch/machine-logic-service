from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from src.database import get_db_session
from src.text2sql.services.text2sql_service import Text2SQLService
from src.text2sql.services.text2sql_metrics import Text2SQLMetrics
from src.text2sql.services.sql_validator import SQLValidator, ValidationLevel
from src.text2sql.services.llm_provider_claude import ClaudeText2SQL
from src.text2sql.services.plan_compiler import compile_plan_to_sql, PlanCompileError
import psycopg2.extras
import os
import re
import json

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
    session_id: str | None = None

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
        m_q = re.match(r"^\s*Q:\s*[\"']?(.+?)[\"']?\s*$", lines[i], re.IGNORECASE)
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


def _load_knowledge_examples(base_dir: str) -> list[tuple[str, str]]:
    """Загрузка примеров из knowledge/harvest/*/pairs/select/*examples-with-questions*.jsonl"""
    examples: list[tuple[str, str]] = []
    try:
        root = os.path.join(base_dir, "knowledge", "harvest")
        if not os.path.isdir(root):
            return examples
        for vendor in os.listdir(root):
            vdir = os.path.join(root, vendor, "pairs", "select")
            if not os.path.isdir(vdir):
                continue
            for fname in os.listdir(vdir):
                if not fname.endswith(".jsonl"):
                    continue
                if "examples-with-questions" not in fname:
                    continue
                fpath = os.path.join(vdir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                                q = obj.get("question_ru") or obj.get("question_en") or obj.get("question_he")
                                sql = obj.get("sql")
                                if q and sql and isinstance(sql, str):
                                    examples.append((str(q), sql))
                            except Exception:
                                continue
                except Exception:
                    continue
    except Exception:
        pass
    return examples


def _derive_time_hints(db: Session, session_id: str) -> list[str]:
    """Из последних 10 записей пытаемся извлечь времовой контекст (вчера/сегодня/последние N часов/дней/неделя/месяц)."""
    hints: list[str] = []
    try:
        rows = db.execute(text(
            """
            SELECT question, COALESCE(validated_sql, sql) AS sql
            FROM text2sql_history
            WHERE session_id = :sid
            ORDER BY created_at DESC
            LIMIT 10
            """
        ), {"sid": session_id}).fetchall()
    except Exception:
        rows = []

    def add_once(h: str):
        if h not in hints:
            hints.append(h)

    import re as _re
    for r in rows:
        q = (r.question or "").lower()
        s = (r.sql or "").lower()
        # Russian/English keywords
        if "вчера" in q or "yesterday" in q or "interval '1 day'" in s or "current_date - 1" in s:
            add_once("yesterday (timezone Asia/Jerusalem)")
        if "сегодня" in q or "today" in q or "current_date" in s and "- 1" not in s:
            add_once("today (timezone Asia/Jerusalem)")
        if "на этой неделе" in q or "this week" in q or "date_trunc('week'" in s:
            add_once("this week (timezone Asia/Jerusalem)")
        if "на прошлой неделе" in q or "last week" in q:
            add_once("last week (timezone Asia/Jerusalem)")
        if "в этом месяце" in q or "this month" in q or "date_trunc('month'" in s:
            add_once("this month (timezone Asia/Jerusalem)")
        if "в прошлом месяце" in q or "last month" in q:
            add_once("last month (timezone Asia/Jerusalem)")
        # квартал/год
        if "в этом квартале" in q or "this quarter" in q or "date_trunc('quarter'" in s:
            add_once("this quarter (timezone Asia/Jerusalem)")
        if "в прошлом квартале" in q or "last quarter" in q:
            add_once("last quarter (timezone Asia/Jerusalem)")
        if "в этом году" in q or "this year" in q or "date_trunc('year'" in s:
            add_once("this year (timezone Asia/Jerusalem)")
        if "в прошлом году" in q or "last year" in q:
            add_once("last year (timezone Asia/Jerusalem)")
        # "за последние N часов/дней"
        m = _re.search(r"за\s+последние\s+(\d+)\s+(час|часа|часов|день|дня|дней)", q)
        if m:
            n = m.group(1)
            unit = m.group(2)
            unit_en = {
                "час": "hours", "часа": "hours", "часов": "hours",
                "день": "days", "дня": "days", "дней": "days",
            }[unit]
            add_once(f"last {n} {unit_en} (timezone Asia/Jerusalem)")
        m2 = _re.search(r"last\s+(\d+)\s+(hours|days)", q)
        if m2:
            add_once(f"last {m2.group(1)} {m2.group(2)} (timezone Asia/Jerusalem)")
        # минуты/секунды
        m3 = _re.search(r"за\s+последние\s+(\d+)\s+(минут|минуты|минута|секунд|секунды|секунда)", q)
        if m3:
            n = m3.group(1)
            unit = m3.group(2)
            unit_en = {
                "минут": "minutes", "минуты": "minutes", "минута": "minutes",
                "секунд": "seconds", "секунды": "seconds", "секунда": "seconds",
            }[unit]
            add_once(f"last {n} {unit_en} (timezone Asia/Jerusalem)")
        m4 = _re.search(r"last\s+(\d+)\s+(minutes|minute|secs|seconds|second)", q)
        if m4:
            unit = m4.group(2)
            unit_std = "seconds" if unit.startswith("sec") else "minutes"
            add_once(f"last {m4.group(1)} {unit_std} (timezone Asia/Jerusalem)")
        # SQL-based generic patterns
        if "now() - interval '" in s:
            add_once("use the same relative now()-interval window as previous")
    return hints


@router.post("/llm_query")
async def llm_query(payload: LLMQuery,
                    db: Session = Depends(get_db_session),
                    x_user_role: str | None = Header(default=None),
                    x_session_id: str | None = Header(default=None)):
    """LLM‑режим: мульти-языковые вопросы → SQL (Claude), валидация и опциональное выполнение."""
    # Подготовка контекста
    base_dir = os.path.dirname(os.path.dirname(__file__))  # src/text2sql
    docs_dir = os.path.join(base_dir, "docs")
    schema_path = os.path.join(docs_dir, "schema_docs.md")
    examples_path = os.path.join(docs_dir, "few_shot_examples.md")
    views_plan_path = os.path.join(docs_dir, "analytics_views_plan.md")
    base_instructions_path = os.path.join(docs_dir, "Text2SQL_R77_AI.txt")

    schema_docs = _read_file_utf8(schema_path)
    few_shot_md = _read_file_utf8(examples_path)
    examples = _parse_few_shot_examples(few_shot_md)
    # knowledge examples
    examples = _load_knowledge_examples(base_dir) + examples
    print(f"DEBUG: Loaded {len(examples)} examples")
    for i, (q, sql) in enumerate(examples[:3]):  # покажем первые 3
        print(f"  {i+1}. Q: {q}")
        print(f"     SQL: {sql[:100]}...")
    
    # Проверим, есть ли правильный пример
    for i, (q, sql) in enumerate(examples):
        if "сколько станков сейчас работает" in q.lower():
            print(f"DEBUG: НАЙДЕН ПРАВИЛЬНЫЙ ПРИМЕР #{i+1}: {q}")
            print(f"DEBUG: SQL: {sql}")
            break

    # Сессионный контекст
    def _ensure_history_table() -> None:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS text2sql_history (
              id BIGSERIAL PRIMARY KEY,
              session_id TEXT NOT NULL,
              question TEXT NOT NULL,
              sql TEXT NOT NULL,
              validated_sql TEXT,
              rows_preview JSONB,
              created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        db.commit()

    def _load_session_examples(sid: str, limit: int = 5):
        try:
            rows = db.execute(text("""
                SELECT question, COALESCE(validated_sql, sql) AS sql
                FROM text2sql_history
                WHERE session_id = :sid
                ORDER BY created_at DESC
                LIMIT :lim
            """), {"sid": sid, "lim": limit}).fetchall()
            return [(r.question, r.sql) for r in rows]
        except Exception:
            return []

    # Определяем session_id
    session_id = (payload.session_id or x_session_id or "anon").strip() or "anon"

    # Обогащаем примеры историей
    try:
        _ensure_history_table()
        hist_examples = _load_session_examples(session_id, limit=5)
        if hist_examples:
            # Помещаем в начало, чтобы повысить шанс отбора
            examples = hist_examples + examples
            print(f"DEBUG: Added {len(hist_examples)} session examples for sid={session_id}")
    except Exception as e:
        print(f"DEBUG: history session examples failed: {e}")

    # Исправляем кодировку вопроса
    question = payload.question
    print(f"DEBUG: Original question: {repr(question)}")
    
    # Пытаемся исправить кодировку кракозябр
    if '?' in question and len(question.split()) == 4:
        try:
            # Если это кракозябры, пытаемся разные варианты исправления
            if '?' in question and '?' in question:
                # Пытаемся декодировать как cp1251 и перекодировать в utf-8
                question_bytes = question.encode('cp1251')
                question = question_bytes.decode('utf-8')
                print(f"DEBUG: Исправлена кодировка (cp1251->utf8): {question}")
        except:
            try:
                # Пытаемся декодировать как latin-1 и перекодировать в utf-8
                question_bytes = question.encode('latin-1')
                question = question_bytes.decode('utf-8')
                print(f"DEBUG: Исправлена кодировка (latin1->utf8): {question}")
            except:
                print(f"DEBUG: Не удалось исправить кодировку")
    
    print(f"DEBUG: Final question: {question}")

    # LIVE-SCHEMA: прочитаем актуальные таблицы/колонки для LLM и валидатора
    tbl_cols: dict[str, set[str]] = {}
    schema_text = ""
    try:
        res = db.execute(text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
            """
        ))
        for table_name, column_name in res.fetchall():
            tbl_cols.setdefault(table_name, set()).add(column_name)
        # Сформируем компактное описание схемы
        lines: list[str] = []
        for t in sorted(tbl_cols.keys()):
            cols = ", ".join(sorted(tbl_cols[t]))
            lines.append(f"- {t}({cols})")
        schema_text = "\n".join(lines)
    except Exception:
        # В случае ошибки просто продолжаем с файловой схемой
        schema_text = ""

    # Перенос временного контекста из последнего запроса сессии (e.g., "вчера")
    # Временные подсказки из последних запросов
    hints = _derive_time_hints(db, session_id)
    time_hint = None
    if hints:
        time_hint = "; ".join(hints[:2])  # не перегружать промпт

    # Генерация SQL через Claude
    llm = ClaudeText2SQL()
    try:
        combined_schema = schema_docs
        if schema_text:
            combined_schema = f"{schema_docs}\n\n# LIVE SCHEMA (auto-generated)\n{schema_text}"
        # Добавим план аналитических вьюх как подсказку, если есть
        views_plan = _read_file_utf8(views_plan_path)
        if views_plan:
            combined_schema = f"{combined_schema}\n\n# ANALYTICS VIEWS PLAN\n{views_plan[:2000]}"
        # Добавим BASE INSTRUCTIONS (теория/правила) из R77, коротко
        base_instructions = _read_file_utf8(base_instructions_path)
        if base_instructions:
            combined_schema = f"{combined_schema}\n\n# BASE INSTRUCTIONS (R77)\n{base_instructions[:2000]}\n\n- Use ONLY tables/columns listed in SCHEMA/LIVE SCHEMA.\n- Prefer semantic views if available.\n- Add LIMIT if missing.\n- Avoid hallucinations; do not invent tables/columns.\n- Timezone: Asia/Jerusalem."

        # Попытка 1: структурированный план с жёстким списком допустимых колонок
        allowed_schema = {t: sorted(list(cols)) for t, cols in (tbl_cols or {}).items()}
        try:
            plan_json = await llm.generate_structured_plan(
                question=(f"[CONTEXT] {time_hint}\n\n{question}" if time_hint else question),
                schema_docs=combined_schema,
                examples=examples,
                allowed_schema_json=json.dumps(allowed_schema)[:8000]
            )
            plan_obj = json.loads(plan_json)
            raw_sql = compile_plan_to_sql(plan_obj, allowed_schema)
        except Exception:
            # Fallback: текстовый режим
            user_q = question
            if time_hint:
                user_q = f"[CONTEXT] {time_hint}\n\n{question}"
            raw_sql = await llm.generate_sql(user_q, combined_schema, examples)
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

    # Подготовим schema-aware валидатор
    validator = SQLValidator(chosen_level)
    try:
        if tbl_cols:
            validator.set_table_columns(tbl_cols)
    except Exception:
        pass
    result = validator.validate(raw_sql)
    # Простейшая коррекция по синонимам таблиц (например, setups -> setup_jobs)
    if not result["valid"]:
        try:
            unknown_table_errors = [e for e in result.get("errors", []) if e.startswith("Unknown table:")]
            if unknown_table_errors:
                synonyms = {
                    "setups": "setup_jobs",
                    "setup": "setup_jobs",
                }
                fixed_sql = raw_sql
                import re as _re
                for wrong, correct in synonyms.items():
                    fixed_sql = _re.sub(rf"\b{_re.escape(wrong)}\b", correct, fixed_sql, flags=_re.IGNORECASE)
                if fixed_sql != raw_sql:
                    result2 = validator.validate(fixed_sql)
                    if result2["valid"]:
                        raw_sql = fixed_sql
                        result = result2
        except Exception:
            pass
    if not result["valid"]:
        # Пишем историю даже при невалидном SQL
        try:
            db.execute(text("""
                INSERT INTO text2sql_history(session_id, question, sql, validated_sql, rows_preview)
                VALUES (:sid, :q, :raw, NULL, NULL)
            """), {"sid": session_id, "q": question, "raw": raw_sql})
            db.commit()
        except Exception:
            db.rollback()
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
        # Префлайт EXPLAIN для раннего отлова ошибок колонок/таблиц
        try:
            db.execute(text(f"EXPLAIN {result['sanitized_sql']}"))
        except Exception as ex:
            # Один авто-ретрай с подсказкой для LLM
            hint = str(ex)
            llm = ClaudeText2SQL()
            repair_prompt = (
                f"Исправь SQL с учётом ошибки СУБД. Ошибка: {hint}. "
                f"Используй только реально существующие таблицы/колонки из схемы. Вопрос: {question}"
            )
            try:
                raw_sql2 = await llm.generate_sql(repair_prompt, schema_docs, examples)
                result2 = validator.validate(raw_sql2)
                if result2["valid"]:
                    db.execute(text(f"EXPLAIN {result2['sanitized_sql']}"))
                    result = result2
                    raw_sql = raw_sql2
                else:
                    return {"sql": raw_sql2, "validation": result2, "error": "SQL validation failed (after repair)"}
            except Exception:
                return {"sql": raw_sql, "validation": result, "error": f"EXPLAIN failed: {hint}"}

        cols, rows = svc.execute(result["sanitized_sql"]) 

        # Сохраняем историю (превью первых строк)
        try:
            preview_rows = []
            for r in rows[:3]:
                if isinstance(r, dict):
                    preview_rows.append(r)
                else:
                    # если возвращается список/кортеж, спроецируем в словарь по колонкам
                    preview_rows.append({cols[i]: r[i] for i in range(min(len(cols), len(r)))})
            db.execute(text("""
                INSERT INTO text2sql_history(session_id, question, sql, validated_sql, rows_preview)
                VALUES (:sid, :q, :raw, :val, :rows)
            """), {
                "sid": session_id,
                "q": question,
                "raw": raw_sql,
                "val": result["sanitized_sql"],
                "rows": psycopg2.extras.Json(preview_rows) if 'psycopg2' in globals() else None
            })
            db.commit()
        except Exception:
            db.rollback()

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
