from __future__ import annotations

from typing import Any, Dict, List


def _month_filter(expr: str, timeframe: str | None) -> Dict[str, str] | None:
    if timeframe and timeframe.startswith("month:"):
        ym = timeframe.split(":", 1)[1]  # YYYY-MM
        y, m = ym.split("-", 1)
        # последний день месяца — грубо (для надёжности можно считать через календарь)
        last = {"01":"31","02":"29","03":"31","04":"30","05":"31","06":"30","07":"31","08":"31","09":"30","10":"31","11":"30","12":"31"}[m]
        return {"expr": f"({expr} AT TIME ZONE 'Asia/Jerusalem')::date BETWEEN DATE '{y}-{m}-01' AND DATE '{y}-{m}-{last}'"}
    return None


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
        plan = {
            "tables": ["setup_jobs", "employees", "machines", "parts"],
            "joins": [
                {"left": "setup_jobs.employee_id", "right": "employees.id"},
                {"left": "setup_jobs.machine_id",  "right": "machines.id"},
                {"left": "setup_jobs.part_id",     "right": "parts.id"}
            ],
            "select": [
                {"table": "employees", "column": "full_name",       "alias": "machinist_name", "distinct": True},
                {"table": "parts",     "column": "drawing_number", "alias": "part_drawing"}
            ],
            "filters": filters,
            "order_by": [
                {"table": "employees", "column": "full_name"},
                {"table": "parts",     "column": "drawing_number"}
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


