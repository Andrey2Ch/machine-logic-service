from __future__ import annotations

from typing import Any, Dict, List, Tuple
import json
import re
import httpx


def _month_filter(expr: str, timeframe: str | None) -> Dict[str, str] | None:
    if timeframe and timeframe.startswith("month:"):
        ym = timeframe.split(":", 1)[1]  # YYYY-MM
        y, m = ym.split("-", 1)
        # последний день месяца — грубо (для надёжности можно считать через календарь)
        last = {"01":"31","02":"29","03":"31","04":"30","05":"31","06":"30","07":"31","08":"31","09":"30","10":"31","11":"30","12":"31"}[m]
        return {"expr": f"({expr} AT TIME ZONE 'Asia/Jerusalem')::date BETWEEN DATE '{y}-{m}-01' AND DATE '{y}-{m}-{last}'"}
    return None


async def build_plan_llm(intent: str, timeframe: str | None, entities: Dict[str, List[int]],
                         allowed_schema: Dict[str, List[str]], api_key: str,
                         examples: List[Tuple[str, str]] = None, original_question: str = "") -> Dict[str, Any]:
    """LLM-based Planner Agent: строит JSON план на основе intent и entities.
    
    Args:
        intent: Intent из Resolver ("list_machinists", "count_machines_by_machinists", "generic")
        timeframe: "yesterday" | "month:YYYY-MM" | null
        entities: {"employees": [ids], "machines": [ids], "parts": [ids]}
        allowed_schema: {"table_name": ["col1", "col2", ...]}
        api_key: Anthropic API key
        examples: Optional few-shot examples
    
    Returns:
        JSON план совместимый с plan_compiler.compile_plan_to_sql
    """
    system_prompt = """You are a Planner Agent. Your task is to build a structured JSON query plan for manufacturing data queries.

You MUST return ONLY valid JSON in this exact format:
{
  "tables": ["table1", "table2"],
  "joins": [{"left": "table1.col", "right": "table2.col"}],
  "select": [{"table": "table1", "column": "col1", "alias": "alias1", "distinct": false}],
  "filters": [{"expr": "table1.col = value"}],
  "group_by": [{"table": "table1", "column": "col1"}],
  "order_by": [{"table": "table1", "column": "col1", "dir": "asc"}],
  "limit": 100
}

Rules:
1. Use ONLY tables and columns from allowed_schema
2. All joins must reference valid table.column pairs
3. Filters use raw SQL expressions (date, timezone-aware)
4. Timezone: Asia/Jerusalem
5. Always include LIMIT (default 100)
6. For "yesterday": use (col AT TIME ZONE 'Asia/Jerusalem')::date = (now() AT TIME ZONE 'Asia/Jerusalem')::date - interval '1 day'
7. For "month:YYYY-MM": use (col AT TIME ZONE 'Asia/Jerusalem')::date BETWEEN 'YYYY-MM-01' AND 'YYYY-MM-31'"""

    # Форматируем allowed_schema
    schema_text = "ALLOWED SCHEMA:\n"
    for tbl, cols in allowed_schema.items():
        schema_text += f"  {tbl}: {', '.join(cols)}\n"
    
    # Форматируем entities
    entities_text = f"""EXTRACTED ENTITIES:
- Intent: {intent}
- Timeframe: {timeframe or "null"}
- Employee IDs: {entities.get("employees") or []}
- Machine IDs: {entities.get("machines") or []}
- Part IDs: {entities.get("parts") or []}
- Lot IDs: {entities.get("lots") or []}"""
    
    user_prompt = f"""{schema_text}

{entities_text}

Original question: {original_question}

Task: Build a JSON plan based on the intent, entities, and original question.

Common patterns:
- list_machinists: SELECT employees.full_name, machines.name, setup_jobs.start_time FROM setup_jobs JOIN employees/machines/parts
- count_machines_by_machinists: SELECT COUNT(DISTINCT setup_jobs.machine_id) FROM setup_jobs
- generic: Determine best approach based on entities

IMPORTANT:
- If question mentions "лот" or "lot": JOIN lots table and include lots.lot_number, lots.initial_planned_quantity
- If question mentions "деталь/part" + specific part number: MUST filter by part_id
- Always include machines.name if machines are mentioned
- Always include parts.drawing_number if parts are mentioned

Apply filters for:
- Timeframe (if provided)
- Employee IDs (setup_jobs.employee_id IN (...))
- Machine IDs (setup_jobs.machine_id IN (...))
- Part IDs (setup_jobs.part_id IN (...)) - CRITICAL if part numbers mentioned!
- Lot IDs (lots.id IN (...)) - CRITICAL if lot numbers mentioned!

Return JSON plan only."""

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 2000,
        "temperature": 0.0,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        
        content = "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")
        
        # Извлекаем JSON
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            plan = json.loads(json_match.group(0))
        else:
            plan = json.loads(content)
        
        return plan
    
    except Exception as e:
        print(f"ERROR: LLM Planner failed: {e}")
        # Fallback to deterministic planner
        return build_plan(intent, timeframe, entities)


def build_plan(intent: str, timeframe: str | None, entities: Dict[str, List[int]]) -> Dict[str, Any]:
    """Строит минимальный JSON‑план под ограниченную матрицу интентов.

    Возвращает структуру, совместимую с plan_compiler.compile_plan_to_sql.
    """
    employees = entities.get("employees") or []
    machines = entities.get("machines") or []
    parts = entities.get("parts") or []

    def filt_yesterday(expr: str) -> Dict[str, str]:
        return {"expr": f"({expr} AT TIME ZONE 'Asia/Jerusalem')::date = (now() AT TIME ZONE 'Asia/Jerusalem')::date - interval '1 day'"}

    if intent == "list_machinists":
        filters: List[Dict[str, str]] = []
        # По умолчанию считаем "делал наладку" как момент регистрации
        if timeframe == "yesterday":
            filters.append(filt_yesterday("setup_jobs.created_at"))
        else:
            mf = _month_filter("setup_jobs.created_at", timeframe)
            if mf:
                filters.append(mf)
        if employees:
            ids = ",".join(str(x) for x in employees)
            filters.append({"expr": f"setup_jobs.employee_id in ({ids})"})
        if machines:
            ids = ",".join(str(x) for x in machines)
            filters.append({"expr": f"setup_jobs.machine_id in ({ids})"})
        if parts:
            ids = ",".join(str(x) for x in parts)
            filters.append({"expr": f"setup_jobs.part_id in ({ids})"})
        
        # Добавляем машины и start_time/end_time в SELECT, если был фильтр по машинам или деталям
        select_items = [
            {"table": "employees", "column": "full_name", "alias": "machinist_name", "distinct": False}
        ]
        if machines or parts:
            select_items.append({"table": "machines", "column": "name", "alias": "machine_name", "distinct": False})
            select_items.append({"table": "setup_jobs", "column": "start_time", "alias": None, "distinct": False})
            select_items.append({"table": "setup_jobs", "column": "end_time", "alias": None, "distinct": False})
        if parts:
            select_items.append({"table": "parts", "column": "drawing_number", "alias": "part_drawing", "distinct": False})
        
        plan = {
            "tables": ["setup_jobs", "employees", "machines", "parts"],
            "joins": [
                {"left": "setup_jobs.employee_id", "right": "employees.id"},
                {"left": "setup_jobs.machine_id",  "right": "machines.id"},
                {"left": "setup_jobs.part_id",     "right": "parts.id"}
            ],
            "select": select_items,
            "filters": filters,
            "order_by": [
                {"table": "setup_jobs", "column": "start_time", "dir": "desc"}
            ],
            "limit": 100,
        }
        return plan

    if intent == "count_machines_by_machinists":
        filters: List[Dict[str, str]] = []
        if timeframe == "yesterday":
            filters.append(filt_yesterday("setup_jobs.created_at"))
        else:
            mf = _month_filter("setup_jobs.created_at", timeframe)
            if mf:
                filters.append(mf)
        if employees:
            ids = ",".join(str(x) for x in employees)
            filters.append({"expr": f"setup_jobs.employee_id in ({ids})"})
        if machines:
            ids = ",".join(str(x) for x in machines)
            filters.append({"expr": f"setup_jobs.machine_id in ({ids})"})
        if parts:
            ids = ",".join(str(x) for x in parts)
            filters.append({"expr": f"setup_jobs.part_id in ({ids})"})
        plan = {
            "tables": ["setup_jobs"],
            "joins": [],
            "select": [{"table": "setup_jobs", "column": "machine_id", "alias": "machine_count", "agg": "count_distinct"}],
            "filters": filters,
            "limit": 100,
        }
        return plan

    # generic fallback: пустой план, пусть наверху отработает текстовый путь
    return {"tables": [], "joins": [], "select": [], "filters": [], "limit": 100}


