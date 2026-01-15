#!/usr/bin/env python3
"""
Скрипт для загрузки базы знаний AI-ассистента в PostgreSQL с векторными embeddings.

Использование:
    python load_ai_knowledge.py

Требования:
    - OPENAI_API_KEY в environment
    - DATABASE_URL в environment
    - Установлен pgvector extension в PostgreSQL
"""

import os
import json
import hashlib
import asyncio
from pathlib import Path
from typing import Optional

import asyncpg
import httpx

# OpenAI API для embeddings
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

# Database
DATABASE_URL = os.getenv("DATABASE_URL")

# Paths
KNOWLEDGE_BASE_PATH = Path(__file__).parent.parent.parent / "isramat-dashboard" / "src" / "lib" / "ai-assistant" / "knowledge"


def sha256_hash(content: str) -> str:
    """Вычисляет SHA-256 хэш строки."""
    return hashlib.sha256(content.encode()).hexdigest()


async def get_embedding(text: str) -> list[float]:
    """Получает embedding от OpenAI API."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set")
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "input": text[:8000],  # Лимит токенов
                "model": EMBEDDING_MODEL
            },
            timeout=30.0
        )
        response.raise_for_status()
        data = response.json()
        return data["data"][0]["embedding"]


async def load_schema_knowledge(conn: asyncpg.Connection):
    """Загружает знания о схеме БД."""
    schema_path = KNOWLEDGE_BASE_PATH / "schema" / "tables.json"
    
    if not schema_path.exists():
        print(f"[WARN] Schema file not found: {schema_path}")
        return
    
    print("[INFO] Загрузка схемы БД...")
    
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    
    tables = schema.get("tables", {})
    
    for table_name, table_info in tables.items():
        title = f"Таблица: {table_name}"
        
        # Формируем текстовое описание таблицы
        content_parts = [
            f"# {title}",
            f"\n{table_info.get('description', '')}",
            "\n## Колонки:"
        ]
        
        for col_name, col_info in table_info.get("columns", {}).items():
            col_type = col_info.get("type", "unknown")
            col_desc = col_info.get("description", "")
            content_parts.append(f"- **{col_name}** ({col_type}): {col_desc}")
        
        if "relations" in table_info:
            content_parts.append("\n## Связи:")
            for rel_name, rel_target in table_info["relations"].items():
                content_parts.append(f"- {rel_name} → {rel_target}")
        
        if "business_rules" in table_info:
            content_parts.append("\n## Бизнес-правила:")
            for rule in table_info["business_rules"]:
                content_parts.append(f"- {rule}")
        
        if "examples" in table_info:
            content_parts.append("\n## Примеры SQL:")
            for example in table_info["examples"]:
                content_parts.append(f"```sql\n{example}\n```")
        
        content = "\n".join(content_parts)
        content_hash = sha256_hash(content)
        
        # Проверяем, есть ли уже такой документ
        existing = await conn.fetchrow(
            "SELECT id, content_hash FROM ai_knowledge_documents WHERE document_type = 'schema' AND title = $1",
            title
        )
        
        if existing and existing["content_hash"] == content_hash:
            print(f"  [OK] {table_name} - без изменений")
            continue
        
        # Получаем embedding
        print(f"  [GEN] {table_name} - генерация embedding...")
        embedding = await get_embedding(content)
        
        metadata = {
            "table_name": table_name,
            "columns": list(table_info.get("columns", {}).keys()),
            "has_relations": bool(table_info.get("relations")),
        }
        
        if existing:
            # Обновляем
            await conn.execute("""
                UPDATE ai_knowledge_documents 
                SET content = $1, content_hash = $2, embedding = $3, metadata = $4, updated_at = NOW()
                WHERE id = $5
            """, content, content_hash, embedding, json.dumps(metadata), existing["id"])
            print(f"  [UPD] {table_name} - обновлено")
        else:
            # Создаём
            await conn.execute("""
                INSERT INTO ai_knowledge_documents (document_type, title, content, content_hash, embedding, metadata)
                VALUES ('schema', $1, $2, $3, $4, $5)
            """, title, content, content_hash, embedding, json.dumps(metadata))
            print(f"  [NEW] {table_name} - создано")


async def load_markdown_knowledge(conn: asyncpg.Connection, doc_type: str, file_path: Path):
    """Загружает знания из markdown файла."""
    if not file_path.exists():
        print(f"[WARN] File not found: {file_path}")
        return
    
    print(f"[INFO] Загрузка {file_path.name}...")
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Разбиваем на секции по заголовкам ##
    sections = []
    current_section = {"title": file_path.stem, "content": ""}
    
    for line in content.split("\n"):
        if line.startswith("## "):
            if current_section["content"].strip():
                sections.append(current_section)
            current_section = {
                "title": line[3:].strip(),
                "content": line + "\n"
            }
        elif line.startswith("# "):
            current_section["main_title"] = line[2:].strip()
            current_section["content"] += line + "\n"
        else:
            current_section["content"] += line + "\n"
    
    if current_section["content"].strip():
        sections.append(current_section)
    
    for section in sections:
        title = section.get("main_title", "") + " - " + section["title"] if section.get("main_title") else section["title"]
        content = section["content"].strip()
        
        if len(content) < 50:  # Слишком короткие секции пропускаем
            continue
        
        content_hash = sha256_hash(content)
        
        # Проверяем существующий документ
        existing = await conn.fetchrow(
            "SELECT id, content_hash FROM ai_knowledge_documents WHERE document_type = $1 AND title = $2",
            doc_type, title
        )
        
        if existing and existing["content_hash"] == content_hash:
            print(f"  [OK] {title[:50]}... - без изменений")
            continue
        
        # Получаем embedding
        print(f"  [GEN] {title[:50]}... - генерация embedding...")
        embedding = await get_embedding(content)
        
        metadata = {
            "source_file": str(file_path.name),
            "char_count": len(content),
        }
        
        if existing:
            await conn.execute("""
                UPDATE ai_knowledge_documents 
                SET content = $1, content_hash = $2, embedding = $3, metadata = $4, updated_at = NOW()
                WHERE id = $5
            """, content, content_hash, embedding, json.dumps(metadata), existing["id"])
            print(f"  [UPD] {title[:50]}... - обновлено")
        else:
            await conn.execute("""
                INSERT INTO ai_knowledge_documents (document_type, source_path, title, content, content_hash, embedding, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """, doc_type, str(file_path), title, content, content_hash, embedding, json.dumps(metadata))
            print(f"  [NEW] {title[:50]}... - создано")


async def load_sql_examples(conn: asyncpg.Connection):
    """Загружает примеры SQL запросов."""
    print("[INFO] Загрузка SQL примеров...")
    
    examples = [
        {
            "question": "Сколько машино-часов наработал каждый оператор за декабрь?",
            "sql": """SELECT 
    e.full_name,
    COUNT(DISTINCT b.id) as batches,
    SUM(b.recounted_quantity) as parts,
    ROUND(SUM(b.recounted_quantity * sj.cycle_time / 3600.0), 1) as machine_hours
FROM batches b
JOIN setup_jobs sj ON b.setup_job_id = sj.id
JOIN employees e ON b.operator_id = e.id
WHERE b.batch_time >= '2025-12-01' AND b.batch_time < '2026-01-01'
GROUP BY e.full_name
ORDER BY machine_hours DESC""",
            "tables_used": ["batches", "setup_jobs", "employees"],
            "difficulty": "medium",
            "tags": ["машино-часы", "операторы", "отчёт", "производительность"]
        },
        {
            "question": "Какие лоты сейчас в производстве?",
            "sql": """SELECT 
    l.lot_number,
    p.drawing_number,
    p.name as part_name,
    l.total_planned_quantity,
    l.created_at
FROM lots l
JOIN parts p ON l.part_id = p.id
WHERE l.status = 'in_production'
ORDER BY l.created_at DESC""",
            "tables_used": ["lots", "parts"],
            "difficulty": "simple",
            "tags": ["лоты", "производство", "статус"]
        },
        {
            "question": "Покажи топ-5 операторов по количеству деталей",
            "sql": """SELECT 
    e.full_name,
    SUM(b.recounted_quantity) as total_parts,
    COUNT(b.id) as batch_count
FROM batches b
JOIN employees e ON b.operator_id = e.id
WHERE b.batch_time >= NOW() - INTERVAL '30 days'
GROUP BY e.id, e.full_name
ORDER BY total_parts DESC
LIMIT 5""",
            "tables_used": ["batches", "employees"],
            "difficulty": "simple",
            "tags": ["топ", "операторы", "детали", "рейтинг"]
        },
        {
            "question": "Статистика по станку SR-32 за последний месяц",
            "sql": """SELECT 
    m.name as machine,
    COUNT(DISTINCT sj.id) as setups,
    COUNT(DISTINCT sj.lot_id) as lots,
    SUM(b.recounted_quantity) as total_parts,
    ROUND(SUM(b.recounted_quantity * sj.cycle_time / 3600.0), 1) as machine_hours,
    ROUND(AVG(sj.cycle_time), 1) as avg_cycle_time
FROM machines m
LEFT JOIN setup_jobs sj ON sj.machine_id = m.id AND sj.start_time >= NOW() - INTERVAL '30 days'
LEFT JOIN batches b ON b.setup_job_id = sj.id
WHERE m.name = 'SR-32'
GROUP BY m.id, m.name""",
            "tables_used": ["machines", "setup_jobs", "batches"],
            "difficulty": "medium",
            "tags": ["станок", "статистика", "SR-32"]
        },
        {
            "question": "Покажи брак за последнюю неделю",
            "sql": """SELECT 
    d.created_at::date as date,
    m.name as machine,
    l.lot_number,
    p.drawing_number,
    d.defect_quantity,
    d.reason
FROM defects d
JOIN setup_jobs sj ON d.setup_job_id = sj.id
JOIN machines m ON sj.machine_id = m.id
JOIN lots l ON sj.lot_id = l.id
JOIN parts p ON l.part_id = p.id
WHERE d.created_at >= NOW() - INTERVAL '7 days'
ORDER BY d.created_at DESC""",
            "tables_used": ["defects", "setup_jobs", "machines", "lots", "parts"],
            "difficulty": "medium",
            "tags": ["брак", "дефекты", "качество"]
        },
        {
            "question": "Расхождения больше 10% между оператором и кладовщиком",
            "sql": """SELECT 
    b.batch_time::date as date,
    op.full_name as operator,
    wh.full_name as warehouse_employee,
    b.operator_reported_quantity,
    b.recounted_quantity,
    b.discrepancy_absolute,
    ROUND(b.discrepancy_percentage, 1) as discrepancy_pct
FROM batches b
JOIN employees op ON b.operator_id = op.id
LEFT JOIN employees wh ON b.warehouse_employee_id = wh.id
WHERE ABS(b.discrepancy_percentage) > 10
  AND b.batch_time >= NOW() - INTERVAL '30 days'
ORDER BY ABS(b.discrepancy_percentage) DESC""",
            "tables_used": ["batches", "employees"],
            "difficulty": "medium",
            "tags": ["расхождения", "контроль", "склад"]
        },
        {
            "question": "Сколько станков сейчас работает?",
            "sql": """SELECT 
    COUNT(*) FILTER (WHERE sj.status = 'production') as working,
    COUNT(*) FILTER (WHERE sj.status = 'setup') as in_setup,
    COUNT(*) FILTER (WHERE sj.status = 'waiting_approval') as waiting_qc,
    (SELECT COUNT(*) FROM machines WHERE is_active = TRUE) as total_active
FROM setup_jobs sj
WHERE sj.end_time IS NULL""",
            "tables_used": ["setup_jobs", "machines"],
            "difficulty": "simple",
            "tags": ["станки", "статус", "работа"]
        },
        {
            "question": "Выработка по сменам за сегодня",
            "sql": """SELECT 
    CASE 
        WHEN EXTRACT(HOUR FROM b.batch_time) BETWEEN 6 AND 17 THEN 'Смена 1 (день)'
        ELSE 'Смена 2 (ночь)'
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
            "tags": ["смены", "выработка", "сегодня"]
        },
    ]
    
    for ex in examples:
        # Проверяем, есть ли уже такой пример
        existing = await conn.fetchrow(
            "SELECT id FROM ai_sql_examples WHERE question = $1",
            ex["question"]
        )
        
        if existing:
            print(f"  [OK] {ex['question'][:50]}... - уже есть")
            continue
        
        # Получаем embedding для вопроса
        print(f"  [GEN] {ex['question'][:50]}... - генерация embedding...")
        embedding = await get_embedding(ex["question"])
        
        await conn.execute("""
            INSERT INTO ai_sql_examples (question, question_embedding, sql_query, tables_used, difficulty, tags, is_verified)
            VALUES ($1, $2, $3, $4, $5, $6, TRUE)
        """, ex["question"], embedding, ex["sql"], ex["tables_used"], ex["difficulty"], ex["tags"])
        
        print(f"  [NEW] {ex['question'][:50]}... - создано")


async def main():
    """Основная функция загрузки знаний."""
    print("[AI] Загрузка базы знаний AI-ассистента\n")
    
    if not DATABASE_URL:
        print("[ERROR] DATABASE_URL not set!")
        return
    
    if not OPENAI_API_KEY:
        print("[ERROR] OPENAI_API_KEY not set!")
        return
    
    # Подключаемся к БД
    conn = await asyncpg.connect(DATABASE_URL)
    
    try:
        # 1. Загружаем схему БД
        await load_schema_knowledge(conn)
        print()
        
        # 2. Загружаем glossary
        await load_markdown_knowledge(
            conn, "glossary", 
            KNOWLEDGE_BASE_PATH / "domain" / "glossary.md"
        )
        print()
        
        # 3. Загружаем workflows
        await load_markdown_knowledge(
            conn, "workflow", 
            KNOWLEDGE_BASE_PATH / "domain" / "workflows.md"
        )
        print()
        
        # 4. Загружаем SQL примеры
        await load_sql_examples(conn)
        print()
        
        # Статистика
        stats = await conn.fetchrow("""
            SELECT 
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE document_type = 'schema') as schema_count,
                COUNT(*) FILTER (WHERE document_type = 'glossary') as glossary_count,
                COUNT(*) FILTER (WHERE document_type = 'workflow') as workflow_count
            FROM ai_knowledge_documents
            WHERE is_active = TRUE
        """)
        
        sql_count = await conn.fetchval("SELECT COUNT(*) FROM ai_sql_examples")
        
        print("[DONE] Загрузка завершена!")
        print(f"   Документов в базе знаний: {stats['total']}")
        print(f"      - Схема БД: {stats['schema_count']}")
        print(f"      - Глоссарий: {stats['glossary_count']}")
        print(f"      - Workflows: {stats['workflow_count']}")
        print(f"   SQL примеров: {sql_count}")
        
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
