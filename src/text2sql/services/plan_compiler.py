from __future__ import annotations

from typing import Dict, List, Optional, Any


class PlanCompileError(Exception):
    pass


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise PlanCompileError(msg)


def compile_plan_to_sql(plan: Dict[str, Any], allowed_schema: Dict[str, List[str]]) -> str:
    """
    Компилирует строгий JSON-план в безопасный SQL SELECT.

    Ожидаемый формат `plan`:
    {
      "tables": ["setup_jobs", "employees"],
      "joins": [{"left":"setup_jobs.employee_id","right":"employees.id"}],
      "select": [{"table":"employees","column":"full_name","alias":null,"distinct":true}],
      "filters": [{"expr":"date(setup_jobs.start_time) = current_date - interval '1 day'"}],
      "group_by": [{"table":"setup_jobs","column":"status"}],
      "order_by": [{"table":"employees","column":"full_name","dir":"asc"}],
      "limit": 100
    }

    Все table/column обязаны существовать в allowed_schema.
    Разрешается только SELECT (+ WITH/CTE отсутствуют).
    """
    tables: List[str] = plan.get("tables", []) or []
    _assert(len(tables) > 0, "Plan must specify at least one table")

    # валидация tables
    for t in tables:
        _assert(t in allowed_schema, f"Unknown table in plan: {t}")

    # SELECT
    select_items = plan.get("select", []) or []
    _assert(len(select_items) > 0, "Plan must specify select items")

    # DISTINCT флаг на уровне SELECT
    distinct_global = False
    sql_select_parts: List[str] = []
    for item in select_items:
        t = item.get("table")
        c = item.get("column")
        alias = item.get("alias")
        distinct = item.get("distinct", False)
        _assert(isinstance(t, str) and isinstance(c, str), "select item requires table/column")
        _assert(t in allowed_schema, f"Unknown table in select: {t}")
        _assert(c in allowed_schema[t], f"Unknown column in select: {t}.{c}")
        part = f"{t}.{c}"
        if alias and isinstance(alias, str) and alias.strip():
            part += f" AS {alias}"
        sql_select_parts.append(part)
        distinct_global = distinct_global or bool(distinct)

    select_clause = ", ".join(sql_select_parts)
    if distinct_global:
        select_clause = f"DISTINCT {select_clause}"

    # FROM и JOIN
    from_table = tables[0]
    sql_from = f"FROM {from_table}"
    joins = plan.get("joins", []) or []
    sql_join_parts: List[str] = []
    for j in joins:
        left = j.get("left")
        right = j.get("right")
        _assert(isinstance(left, str) and isinstance(right, str), "join requires left/right")
        # формат table.column
        def parse_tc(s: str) -> tuple[str, str]:
            parts = s.split(".")
            _assert(len(parts) == 2, f"join endpoint must be table.column: {s}")
            return parts[0], parts[1]

        lt, lc = parse_tc(left)
        rt, rc = parse_tc(right)
        for t, c in [(lt, lc), (rt, rc)]:
            _assert(t in allowed_schema, f"Unknown table in join: {t}")
            _assert(c in allowed_schema[t], f"Unknown column in join: {t}.{c}")
        # используем INNER JOIN по умолчанию
        sql_join_parts.append(f"JOIN {rt} ON {lt}.{lc} = {rt}.{rc}")

    # WHERE
    filters = plan.get("filters", []) or []
    sql_where_parts: List[str] = []
    for f in filters:
        expr = f.get("expr")
        _assert(isinstance(expr, str) and expr.strip(), "filter requires expr string")
        # лёгкая проверка на DML/опасные ключевые слова
        bad = ["insert ", "update ", "delete ", "drop ", "alter ", ";", "--", "/*"]
        low = expr.lower()
        _assert(not any(b in low for b in bad), f"forbidden in filter expr: {expr}")
        sql_where_parts.append(expr)

    # GROUP BY
    group_by = plan.get("group_by", []) or []
    sql_group_parts: List[str] = []
    for g in group_by:
        t = g.get("table")
        c = g.get("column")
        _assert(isinstance(t, str) and isinstance(c, str), "group_by requires table/column")
        _assert(t in allowed_schema and c in allowed_schema[t], f"Unknown group_by: {t}.{c}")
        sql_group_parts.append(f"{t}.{c}")

    # ORDER BY
    order_by = plan.get("order_by", []) or []
    sql_order_parts: List[str] = []
    for o in order_by:
        t = o.get("table")
        c = o.get("column")
        d = (o.get("dir") or "asc").lower()
        _assert(isinstance(t, str) and isinstance(c, str), "order_by requires table/column")
        _assert(t in allowed_schema and c in allowed_schema[t], f"Unknown order_by: {t}.{c}")
        _assert(d in {"asc", "desc"}, "order_by.dir must be asc|desc")
        sql_order_parts.append(f"{t}.{c} {d}")

    # LIMIT (обязателен, дефолт 100)
    limit = plan.get("limit")
    try:
        limit_val = int(limit) if limit is not None else 100
    except Exception as ex:
        raise PlanCompileError(f"Invalid limit value: {limit}")
    if limit_val <= 0:
        limit_val = 100

    sql_parts: List[str] = [f"SELECT {select_clause}", sql_from]
    if sql_join_parts:
        sql_parts.extend(sql_join_parts)
    if sql_where_parts:
        sql_parts.append("WHERE " + " AND ".join(sql_where_parts))
    if sql_group_parts:
        sql_parts.append("GROUP BY " + ", ".join(sql_group_parts))
    if sql_order_parts:
        sql_parts.append("ORDER BY " + ", ".join(sql_order_parts))
    sql_parts.append(f"LIMIT {limit_val}")

    return "\n".join(sql_parts)


