"""
Утилиты для нормализации SQL запросов и оценки качества вопросов
"""
import re
from typing import List, Tuple, Optional
from sqlalchemy import text


def normalize_sql(sql: str) -> str:
    """
    Нормализует SQL запрос для дедупликации:
    - Заменяет все параметры на %(param)s
    - Убирает лишние пробелы
    - Приводит к единому регистру ключевых слов
    """
    if not sql:
        return ""
    
    # Заменяем все параметры на плейсхолдер
    sql = re.sub(r'%\([^)]+\)s', '%(param)s', sql)
    
    # Убираем лишние пробелы и переносы строк
    sql = re.sub(r'\s+', ' ', sql).strip()
    
    # Приводим ключевые слова к верхнему регистру
    keywords = ['SELECT', 'FROM', 'WHERE', 'INSERT', 'UPDATE', 'DELETE', 'SET', 'VALUES', 'ORDER BY', 'GROUP BY', 'HAVING', 'LIMIT', 'JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'OUTER JOIN']
    for keyword in keywords:
        sql = re.sub(rf'\b{keyword.lower()}\b', keyword, sql, flags=re.IGNORECASE)
    
    return sql


def extract_table_names(sql: str) -> List[str]:
    """Извлекает имена таблиц из SQL запроса"""
    tables = []
    
    # FROM clause
    from_match = re.search(r'FROM\s+(\w+)', sql, re.IGNORECASE)
    if from_match:
        tables.append(from_match.group(1))
    
    # JOIN clauses
    join_matches = re.findall(r'JOIN\s+(\w+)', sql, re.IGNORECASE)
    tables.extend(join_matches)
    
    # UPDATE clause
    update_match = re.search(r'UPDATE\s+(\w+)', sql, re.IGNORECASE)
    if update_match:
        tables.append(update_match.group(1))
    
    # INSERT INTO clause
    insert_match = re.search(r'INSERT\s+INTO\s+(\w+)', sql, re.IGNORECASE)
    if insert_match:
        tables.append(insert_match.group(1))
    
    return list(set(tables))  # убираем дубликаты


def get_operation_type(sql: str) -> str:
    """Определяет тип операции SQL"""
    sql_upper = sql.upper().strip()
    
    if sql_upper.startswith('SELECT'):
        return 'SELECT'
    elif sql_upper.startswith('INSERT'):
        return 'INSERT'
    elif sql_upper.startswith('UPDATE'):
        return 'UPDATE'
    elif sql_upper.startswith('DELETE'):
        return 'DELETE'
    else:
        return 'UNKNOWN'


def is_good_question(question: str) -> bool:
    """
    Проверяет, является ли вопрос качественным бизнес-вопросом
    """
    if not question or len(question.strip()) < 10:
        return False
    
    # Плохие паттерны (технические описания)
    bad_patterns = [
        r'Покажи из \w+ поля',
        r'Какие данные.*из \w+',
        r'Что делает этот DML',
        r'Выполняет изменение данных',
        r'Покажи поля \w+',
        r'SELECT.*FROM',
        r'UPDATE.*SET',
        r'INSERT INTO'
    ]
    
    for pattern in bad_patterns:
        if re.search(pattern, question, re.IGNORECASE):
            return False
    
    # Хорошие паттерны (бизнес-вопросы)
    good_patterns = [
        r'Сколько',
        r'Какие',
        r'Кто',
        r'Когда',
        r'Где',
        r'Покажи',
        r'Найди',
        r'Список',
        r'Отчет',
        r'Статистика'
    ]
    
    return any(re.search(pattern, question, re.IGNORECASE) for pattern in good_patterns)


def calculate_quality_score(question: str, sql: str) -> int:
    """
    Рассчитывает оценку качества пары вопрос-SQL (0-10)
    """
    score = 0
    
    # Базовые проверки
    if not question or not sql:
        return 0
    
    if is_good_question(question):
        score += 5
    
    # Длина вопроса (не слишком короткий, не слишком длинный)
    if 15 <= len(question) <= 100:
        score += 2
    elif 10 <= len(question) <= 150:
        score += 1
    
    # Сложность SQL (наличие WHERE, JOIN, подзапросов)
    if 'WHERE' in sql.upper():
        score += 1
    if 'JOIN' in sql.upper():
        score += 1
    if '(' in sql and ')' in sql:  # подзапросы
        score += 1
    
    return min(score, 10)  # максимум 10


def suggest_business_question(sql: str, table_names: List[str], operation_type: str) -> str:
    """
    Предлагает улучшенный бизнес-вопрос на основе SQL
    """
    if operation_type == 'SELECT':
        if 'COUNT' in sql.upper():
            if 'setup_jobs' in table_names:
                return "Сколько активных настроек станков?"
            elif 'batches' in table_names:
                return "Сколько открытых батчей?"
            elif 'cards' in table_names:
                return "Сколько карточек в работе?"
            else:
                return f"Сколько записей в {table_names[0]}?"
        
        elif 'setup_jobs' in table_names:
            return "Какие настройки станков активны?"
        elif 'batches' in table_names:
            return "Какие батчи в работе?"
        elif 'cards' in table_names:
            return "Какие карточки используются?"
        else:
            return f"Покажи данные из {table_names[0]}"
    
    elif operation_type == 'UPDATE':
        if 'setup_jobs' in table_names:
            return "Обновить настройки станка?"
        elif 'cards' in table_names:
            return "Изменить статус карточки?"
        else:
            return f"Обновить данные в {table_names[0]}?"
    
    elif operation_type == 'INSERT':
        return f"Добавить новую запись в {table_names[0]}?"
    
    elif operation_type == 'DELETE':
        return f"Удалить записи из {table_names[0]}?"
    
    return "Выполнить SQL запрос?"
