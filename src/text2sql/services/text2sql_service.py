from typing import Any, Dict, List, Tuple
import re
from sqlalchemy import text
from sqlalchemy.orm import Session
from .sql_validator import SQLValidator, ValidationLevel

SAFE_DENY = re.compile(r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|copy)\b", re.I)
SQL_COMMENT = re.compile(r"(--|/\*)")
LEADING_SELECT = re.compile(r"^\s*select\b", re.I)

class Text2SQLService:
    def __init__(self, db: Session, default_limit: int = 100, timeout_ms: int = 5000, 
                 validation_level: ValidationLevel = ValidationLevel.MODERATE):
        self.db = db
        self.default_limit = default_limit
        self.timeout_ms = timeout_ms
        self.validator = SQLValidator(validation_level, max_result_rows=default_limit)

    def generate_sql(self, question: str) -> str:
        # MVP с few-shot примерами
        question_lower = question.lower()
        print(f"DEBUG: question='{question}', lower='{question_lower}'")
        
        # Few-shot примеры для улучшения качества
        if any(word in question_lower for word in ["сколько", "skolko", "how many", "count", "many"]):
            if "батч" in question_lower or "batch" in question_lower:
                if "открыт" in question_lower or "open" in question_lower:
                    return "SELECT COUNT(*) as open_batches FROM batches WHERE status = 'open'"
                elif "всего" in question_lower or "total" in question_lower:
                    return "SELECT COUNT(*) as total_batches FROM batches"
                else:
                    return "SELECT COUNT(*) as count FROM batches"
            elif "операц" in question_lower or "operation" in question_lower:
                return "SELECT COUNT(*) as operation_count FROM batch_operations"
            elif "станк" in question_lower or "machine" in question_lower:
                return "SELECT COUNT(*) as machine_count FROM machines"
            else:
                return "SELECT COUNT(*) as count FROM batches"
        
        # Время
        if any(word in question_lower for word in ["время", "когда", "time", "when", "now"]):
            if "сейчас" in question_lower or "now" in question_lower:
                return "SELECT NOW() as current_time"
            else:
                return "SELECT NOW() as current_time"
        
        # Статусы
        if "статус" in question_lower or "status" in question_lower:
            if "батч" in question_lower or "batch" in question_lower:
                return "SELECT status, COUNT(*) as count FROM batches GROUP BY status"
            elif "операц" in question_lower or "operation" in question_lower:
                return "SELECT status, COUNT(*) as count FROM batch_operations GROUP BY status"
            else:
                return "SELECT status, COUNT(*) as count FROM batches GROUP BY status"
        
        # Последние записи
        if any(word in question_lower for word in ["последн", "last", "recent"]):
            if "батч" in question_lower or "batch" in question_lower:
                return "SELECT * FROM batches ORDER BY created_at DESC LIMIT 10"
            elif "операц" in question_lower or "operation" in question_lower:
                return "SELECT * FROM batch_operations ORDER BY created_at DESC LIMIT 10"
            else:
                return "SELECT * FROM batches ORDER BY created_at DESC LIMIT 10"
        
        # Станки
        if "станк" in question_lower or "machine" in question_lower:
            if "все" in question_lower or "all" in question_lower:
                return "SELECT machine_id, machine_name, area_name FROM machines"
            elif "зона" in question_lower or "area" in question_lower:
                return "SELECT area_name, COUNT(*) as machine_count FROM machines GROUP BY area_name"
            else:
                return "SELECT machine_id, machine_name, area_name FROM machines"
        
        # По умолчанию - общая статистика
        return "SELECT 'batches' as table_name, COUNT(*) as count FROM batches UNION ALL SELECT 'operations', COUNT(*) FROM batch_operations UNION ALL SELECT 'machines', COUNT(*) FROM machines"

    def _enforce_safe(self, sql: str) -> str:
        """Старый метод валидации (deprecated) - использует новый валидатор"""
        validation_result = self.validator.validate(sql)
        
        if not validation_result['valid']:
            errors = '; '.join(validation_result['errors'])
            raise ValueError(f"SQL validation failed: {errors}")
        
        # Логируем предупреждения
        if validation_result['warnings']:
            print(f"SQL warnings: {'; '.join(validation_result['warnings'])}")
        
        return validation_result['sanitized_sql']

    def execute(self, sql: str) -> Tuple[List[str], List[Dict[str, Any]]]:
        safe_sql = self._enforce_safe(sql)
        with self.db.begin():
            try:
                bind = getattr(self.db, "bind", None)
                if bind is not None and hasattr(bind, "dialect") and "postgres" in bind.dialect.name:
                    self.db.execute(text(f"set local statement_timeout = {self.timeout_ms}"))
            except Exception:
                pass
            result = self.db.execute(text(safe_sql))
            cols = list(result.keys())
            rows = [dict(zip(cols, r)) for r in result.fetchall()]
        return cols, rows

    def answer(self, question: str) -> Dict[str, Any]:
        sql = self.generate_sql(question)
        cols, rows = self.execute(sql)
        return {"sql": sql, "columns": cols, "rows": rows}
