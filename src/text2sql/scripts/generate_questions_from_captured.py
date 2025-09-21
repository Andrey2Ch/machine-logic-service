#!/usr/bin/env python3
"""
Скрипт для автоматической генерации вопросов из captured SQL queries.
Читает записи из text2sql_captured, генерирует вопросы и обновляет БД.
"""

import os
import sys
import logging
import psycopg2
import psycopg2.extras
from typing import Optional
from dotenv import load_dotenv

# Добавляем путь к корню проекта для импорта модулей
project_root = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.insert(0, project_root)

# Импортируем функцию генерации вопросов напрямую
import re

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
        m_table = re.search(r"insert\s+into\s+(\w+)", sql)
        table = m_table.group(1) if m_table else "таблицу"
        return (f"Добавить записи в {table}?", hints)
    
    elif kind == "update":
        m_table = re.search(r"update\s+(\w+)", sql)
        table = m_table.group(1) if m_table else "таблицу"
        
        m_set = re.search(r"set\s+(.+?)(\s+where|$)", sql)
        if m_set:
            set_expr = m_set.group(1).strip()
            cols = re.findall(r"(\w+)\s*=", set_expr)
            if cols:
                cols_text = ", ".join(cols[:3]) + (" и др." if len(cols) > 3 else "")
                hints.append(f"SET: {cols_text}")
        
        m_where = re.search(r"where\s+(.+)$", sql)
        if m_where:
            where_expr = m_where.group(1).strip()
            conds = []
            for match in re.finditer(r"(\w+)\s*=\s*%\([^)]+\)s", where_expr):
                conds.append(f"{match.group(1)} = <значение>")
            if conds:
                hints.append(f"WHERE: {'; '.join(conds[:2])}")
        
        return (f"Обновить {table}?", hints)
    
    elif kind == "delete":
        m_table = re.search(r"delete\s+from\s+(\w+)", sql)
        table = m_table.group(1) if m_table else "таблицы"
        return (f"Удалить записи из {table}?", hints)
    
    else:
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
        for match in re.finditer(r"(\b[a-z_][a-z0-9_]*\b)\s*=\s*%\([^)]+\)s", filters):
            col = match.group(1)
            conds.append(f"{col} = <значение>")
        for match in re.finditer(r"(\b[a-z_][a-z0-9_]*\b)\s+is\s+(not\s+)?null", filters):
            col = match.group(1)
            not_kw = match.group(2)
            conds.append(f"{col} {'не ' if not_kw else ''}пусто")
        for col in re.findall(r"(\b[a-z_][a-z0-9_]*\b)\s+in\s*\(", filters):
            conds.append(f"{col} в списке")
        if not conds:
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

# Загружаем переменные окружения
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_db_connection():
    """Подключение к PostgreSQL."""
    try:
        # Пробуем получить DATABASE_URL из переменных окружения
        database_url = os.getenv('DATABASE_URL')
        if database_url:
            return psycopg2.connect(database_url)
        
        # Railway URL pattern
        railway_url = "postgresql://postgres:XWQcfSsWzPCJYUJKGOFSgBxOKyEYMXYI@junction.proxy.rlwy.net:24086/railway"
        logger.info("📡 Используем Railway PostgreSQL")
        return psycopg2.connect(railway_url)
        
    except Exception as e:
        logger.error(f"Ошибка подключения к БД: {e}")
        raise


def fetch_queries_without_questions(conn, limit: int = 100):
    """Получить SQL-запросы без сгенерированных вопросов."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, sql, route, role, duration_ms, captured_at
            FROM text2sql_captured 
            WHERE question_ru IS NULL 
               OR question_ru = ''
            ORDER BY captured_at DESC
            LIMIT %s
        """, (limit,))
        return cur.fetchall()


def update_question(conn, record_id: int, question_ru: str, hints: list[str]):
    """Обновить запись с сгенерированным вопросом."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE text2sql_captured 
            SET question_ru = %s, 
                question_hints = %s,
                question_generated_at = NOW()
            WHERE id = %s
        """, (question_ru, psycopg2.extras.Json(hints), record_id))


def main():
    """Основная функция скрипта."""
    logger.info("🚀 Запуск генерации вопросов из captured SQL queries")
    
    try:
        conn = get_db_connection()
        logger.info("✅ Подключение к БД установлено")
        
        # Проверяем, есть ли колонки для вопросов
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'text2sql_captured' 
                  AND column_name IN ('question_ru', 'question_hints', 'question_generated_at')
            """)
            existing_cols = [row[0] for row in cur.fetchall()]
            
            # Добавляем недостающие колонки
            if 'question_ru' not in existing_cols:
                logger.info("➕ Добавляем колонку question_ru")
                cur.execute("ALTER TABLE text2sql_captured ADD COLUMN question_ru TEXT")
            
            if 'question_hints' not in existing_cols:
                logger.info("➕ Добавляем колонку question_hints")
                cur.execute("ALTER TABLE text2sql_captured ADD COLUMN question_hints JSONB")
                
            if 'question_generated_at' not in existing_cols:
                logger.info("➕ Добавляем колонку question_generated_at")
                cur.execute("ALTER TABLE text2sql_captured ADD COLUMN question_generated_at TIMESTAMP")
            
            conn.commit()
        
        # Получаем общее количество записей без вопросов
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) 
                FROM text2sql_captured 
                WHERE question_ru IS NULL OR question_ru = ''
            """)
            total_count = cur.fetchone()[0]
            logger.info(f"📊 Всего записей без вопросов: {total_count}")
        
        if total_count == 0:
            logger.info("✨ Все записи уже имеют сгенерированные вопросы!")
            return
        
        processed = 0
        errors = 0
        batch_size = 100
        
        while processed < total_count:
            # Получаем очередную порцию записей
            records = fetch_queries_without_questions(conn, batch_size)
            if not records:
                break
                
            logger.info(f"🔄 Обрабатываем порцию: {len(records)} записей")
            
            for record in records:
                try:
                    # Генерируем вопрос
                    question_ru, hints = _ru_from_sql(record['sql'])
                    
                    # Обновляем запись в БД
                    update_question(conn, record['id'], question_ru, hints)
                    processed += 1
                    
                    if processed % 50 == 0:
                        logger.info(f"✅ Обработано: {processed}/{total_count}")
                        
                except Exception as e:
                    logger.error(f"❌ Ошибка обработки записи {record['id']}: {e}")
                    errors += 1
                    continue
            
            # Сохраняем изменения после каждой порции
            conn.commit()
            logger.info(f"💾 Сохранена порция. Всего обработано: {processed}")
        
        logger.info(f"🎉 Генерация завершена!")
        logger.info(f"✅ Успешно обработано: {processed}")
        logger.info(f"❌ Ошибок: {errors}")
        
        # Показываем статистику
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(question_ru) as with_questions,
                    COUNT(*) - COUNT(question_ru) as without_questions
                FROM text2sql_captured
            """)
            stats = cur.fetchone()
            logger.info(f"📈 Итоговая статистика:")
            logger.info(f"   Всего записей: {stats[0]}")
            logger.info(f"   С вопросами: {stats[1]}")
            logger.info(f"   Без вопросов: {stats[2]}")
        
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
        raise
    finally:
        if 'conn' in locals():
            conn.close()
            logger.info("🔌 Соединение с БД закрыто")


if __name__ == "__main__":
    main()
