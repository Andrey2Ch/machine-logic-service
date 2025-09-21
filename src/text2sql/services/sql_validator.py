"""
SQL Validator для Text2SQL
=========================

Расширенный валидатор SQL запросов с whitelist/denylist подходами.
"""

import re
from typing import List, Set, Dict, Any
from enum import Enum

class ValidationLevel(Enum):
    """Уровни валидации SQL"""
    STRICT = "strict"      # Только whitelist операции
    MODERATE = "moderate"  # Denylist + базовые проверки
    PERMISSIVE = "permissive"  # Минимальные ограничения

class SQLValidator:
    """Валидатор SQL запросов для Text2SQL"""
    
    def __init__(self, 
                 validation_level: ValidationLevel = ValidationLevel.MODERATE,
                 max_query_length: int = 1000,
                 max_result_rows: int = 100):
        self.validation_level = validation_level
        self.max_query_length = max_query_length
        self.max_result_rows = max_result_rows
        
        # Denylist - запрещенные операции
        self.denylist_patterns = [
            r'\b(insert|update|delete|drop|alter|truncate|grant|revoke|copy)\b',
            r'\b(create|replace|modify|rename)\b',
            r'\b(exec|execute|sp_|xp_)\b',
            r'\b(union\s+all\s+select.*from\s+information_schema)\b',  # SQL injection
            r'--|\/\*|\*\/',  # Комментарии
            r'\b(load_file|into\s+outfile|into\s+dumpfile)\b',
            r'\b(lock|unlock)\s+tables\b',
            r'\b(show\s+grants|show\s+processlist)\b',
        ]
        
        # Whitelist - разрешенные операции (для STRICT режима)
        self.whitelist_patterns = [
            r'^\s*select\b',
            r'^\s*with\b',
            r'\bfrom\b',
            r'\bwhere\b',
            r'\bgroup\s+by\b',
            r'\border\s+by\b',
            r'\blimit\b',
            r'\bcount\b',
            r'\bsum\b',
            r'\bavg\b',
            r'\bmax\b',
            r'\bmin\b',
            r'\bnow\(\)\b',
            r'\bcoalesce\b',
            r'\bcase\s+when\b',
        ]
        
        # Разрешенные таблицы (можно настроить и сузить до вьюх семантического слоя)
        self.allowed_tables = {
            'batches', 'batch_operations', 'machines',
            'employees', 'access_attempts', 'cards', 'setup_jobs',
            # семантические вьюхи (если есть)
            'batches_with_shifts'
        }
        
        # Разрешенные функции
        self.allowed_functions = {
            'count', 'sum', 'avg', 'max', 'min', 'now', 'coalesce',
            'date_trunc', 'extract', 'to_char', 'to_date'
        }

        # Кэш схемы: table -> set(columns)
        self.table_columns: Dict[str, Set[str]] = {}

    def set_table_columns(self, table_columns: Dict[str, Set[str]]) -> None:
        """Задать кэш схемы (таблица -> множество колонок)."""
        self.table_columns = {t.lower(): set(c.lower() for c in cols) for t, cols in table_columns.items()}

    def validate(self, sql: str) -> Dict[str, Any]:
        """
        Валидирует SQL запрос
        
        Returns:
            Dict с результатами валидации:
            - valid: bool
            - errors: List[str]
            - warnings: List[str]
            - sanitized_sql: str
        """
        errors = []
        warnings = []
        sanitized_sql = sql.strip()
        
        # Базовая проверка длины
        if len(sanitized_sql) > self.max_query_length:
            errors.append(f"Query too long: {len(sanitized_sql)} > {self.max_query_length}")
        
        # Проверка на пустой запрос
        if not sanitized_sql:
            errors.append("Empty query")
            return {"valid": False, "errors": errors, "warnings": warnings, "sanitized_sql": ""}
        
        # Проверка комментариев
        if re.search(r'--|\/\*', sanitized_sql, re.IGNORECASE):
            errors.append("SQL comments are forbidden")
        
        # Denylist проверки
        for pattern in self.denylist_patterns:
            if re.search(pattern, sanitized_sql, re.IGNORECASE):
                errors.append(f"Forbidden operation detected: {pattern}")
        
        # Whitelist проверки (для STRICT режима)
        if self.validation_level == ValidationLevel.STRICT:
            if not any(re.search(pattern, sanitized_sql, re.IGNORECASE) 
                      for pattern in self.whitelist_patterns):
                errors.append("Query does not match whitelist patterns")
        
        # Проверка на SELECT/WITH CTE (только для STRICT/MODERATE)
        if self.validation_level in {ValidationLevel.STRICT, ValidationLevel.MODERATE}:
            if not re.match(r'^\s*(select|with)\b', sanitized_sql, re.IGNORECASE):
                errors.append("Query must start with SELECT or WITH")
        
        # Проверка таблиц (если указаны)
        table_matches = re.findall(r'\bfrom\s+(\w+)|\bjoin\s+(\w+)', sanitized_sql, re.IGNORECASE)
        used_tables: Set[str] = set()
        for t1, t2 in table_matches:
            table = (t1 or t2) or ''
            t_low = table.lower()
            if not t_low:
                continue
            used_tables.add(t_low)
            if t_low not in self.allowed_tables and t_low not in self.table_columns:
                warnings.append(f"Unknown table: {table}")
        
        # Проверка функций
        function_matches = re.findall(r'\b(\w+)\s*\(', sanitized_sql, re.IGNORECASE)
        for func in function_matches:
            if func.lower() not in self.allowed_functions and not re.match(r'^[a-z_]+$', func.lower()):
                warnings.append(f"Unknown function: {func}")

        # Schema-aware проверка квалифицированных колонок (alias/table.column)
        try:
            if self.table_columns:
                qual_cols = re.findall(r'\b([a-z_][a-z0-9_]*)\s*\.\s*([a-z_][a-z0-9_]*)\b', sanitized_sql, re.IGNORECASE)
                known_cols_global: Set[str] = set()
                for cols in self.table_columns.values():
                    known_cols_global.update(cols)
                for tbl_or_alias, col in qual_cols:
                    t_low = tbl_or_alias.lower()
                    c_low = col.lower()
                    if t_low in self.table_columns:
                        if c_low not in self.table_columns[t_low]:
                            errors.append(f"Unknown column: {tbl_or_alias}.{col}")
                    else:
                        # alias неизвестен — проверим, что такая колонка вообще существует в схеме
                        if c_low not in known_cols_global:
                            errors.append(f"Unknown column: {tbl_or_alias}.{col}")
        except Exception:
            # не ломаем валидацию, если эвристика не сработала
            pass
        
        # Автоматическое добавление LIMIT если нет
        if not re.search(r'\blimit\b', sanitized_sql, re.IGNORECASE):
            sanitized_sql = f"{sanitized_sql.rstrip(';')} LIMIT {self.max_result_rows}"
            warnings.append(f"Added LIMIT {self.max_result_rows}")
        
        # Проверка на потенциальные SQL injection
        suspicious_patterns = [
            # 'union all select' допустим для аналитики — больше не считаем это обязательной инъекцией
            r';\s*drop\s+',
            r';\s*delete\s+',
            r';\s*update\s+',
            r'0x[0-9a-f]+',  # Hex strings
        ]
        
        for pattern in suspicious_patterns:
            if re.search(pattern, sanitized_sql, re.IGNORECASE):
                errors.append(f"Potential SQL injection detected: {pattern}")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "sanitized_sql": sanitized_sql
        }

    def add_allowed_table(self, table_name: str) -> None:
        """Добавляет таблицу в whitelist"""
        self.allowed_tables.add(table_name.lower())
    
    def remove_allowed_table(self, table_name: str) -> None:
        """Удаляет таблицу из whitelist"""
        self.allowed_tables.discard(table_name.lower())
    
    def set_validation_level(self, level: ValidationLevel) -> None:
        """Устанавливает уровень валидации"""
        self.validation_level = level

# Глобальный экземпляр валидатора
default_validator = SQLValidator(ValidationLevel.MODERATE)
