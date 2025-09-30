from __future__ import annotations

from typing import Any, Dict, List


def build_plan(intent: str, timeframe: str | None, entities: Dict[str, List[int]]) -> Dict[str, Any]:
    """Строит минимальный JSON‑план под ограниченную матрицу интентов.

    Возвращает структуру, совместимую с plan_compiler.compile_plan_to_sql.
    """
    employees = entities.get("employees") or []
    machines = entities.get("machines") or []

    def filt_yesterday(expr: str) -> Dict[str, str]:
        return {"expr": f"({expr} AT TIME ZONE 'Asia/Jerusalem')::date = (now() AT TIME ZONE 'Asia/Jerusalem')::date - interval '1 day'"}

    if intent == "list_machinists":
        filters: List[Dict[str, str]] = []
        # По умолчанию считаем "делал наладку" как момент регистрации
        if timeframe == "yesterday":
            filters.append(filt_yesterday("setup_jobs.created_at"))
        if employees:
            ids = ",".join(str(x) for x in employees)
            filters.append({"expr": f"setup_jobs.employee_id in ({ids})"})
        plan = {
            "tables": ["setup_jobs", "employees"],
            "joins": [{"left": "setup_jobs.employee_id", "right": "employees.id"}],
            "select": [{"table": "employees", "column": "full_name", "distinct": True}],
            "filters": filters,
            "order_by": [{"expr": "employees.full_name"}],
            "limit": 100,
        }
        return plan

    if intent == "count_machines_by_machinists":
        filters: List[Dict[str, str]] = []
        if timeframe == "yesterday":
            filters.append(filt_yesterday("setup_jobs.created_at"))
        if employees:
            ids = ",".join(str(x) for x in employees)
            filters.append({"expr": f"setup_jobs.employee_id in ({ids})"})
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


