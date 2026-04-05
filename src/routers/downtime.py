"""
Downtime logs API — просмотр журнала и отчёты по простоям станков.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.database import get_db_session

router = APIRouter(prefix="/downtime", tags=["downtime"])


@router.get("/logs")
def get_downtime_logs(
    machine_name: Optional[str] = Query(None),
    from_dt: Optional[datetime] = Query(None, description="ISO datetime"),
    to_dt: Optional[datetime] = Query(None, description="ISO datetime"),
    category: Optional[str] = Query(None, description="machine | part | work_and_material"),
    unanswered_only: bool = Query(False),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db_session),
):
    """
    Список событий простоя — по одной строке на событие (machine + resolved_at).

    Несколько алертов одного простоя (follow-up, эскалация) группируются в одну строку.
    Длительность = resolved_at - first_alert_at. NULL если не разрешён.
    """
    filters = ["1=1"]
    params: dict = {"limit": limit, "offset": offset}

    if machine_name:
        filters.append("LOWER(e.machine_name) LIKE LOWER(:machine_name)")
        params["machine_name"] = f"%{machine_name}%"

    if from_dt:
        filters.append("e.first_alert_at >= :from_dt")
        params["from_dt"] = from_dt

    if to_dt:
        filters.append("e.first_alert_at <= :to_dt")
        params["to_dt"] = to_dt

    if category:
        filters.append("sr.category = :category")
        params["category"] = category

    if unanswered_only:
        filters.append("e.reason_code IS NULL")

    where = " AND ".join(filters)

    # events — одна строка на событие (machine + resolved_at)
    # first_alert — берём id первого алерта (для уникальности строки)
    # reporter — кто ответил (берём из строки где есть reason_code)
    base_cte = """
        WITH events AS (
            SELECT
                l.machine_name,
                l.resolved_at,
                MIN(l.alert_sent_at)      AS first_alert_at,
                COUNT(*)                  AS alert_count,
                MAX(l.reason_code)        AS reason_code,
                MAX(l.reason_reported_at) AS reason_reported_at,
                MIN(l.operator_name)      AS operator_name,
                MIN(l.machinist_name)     AS machinist_name
            FROM machine_downtime_logs l
            GROUP BY l.machine_name, l.resolved_at
        ),
        first_alert AS (
            SELECT DISTINCT ON (machine_name, resolved_at)
                machine_name, resolved_at, id
            FROM machine_downtime_logs
            ORDER BY machine_name, resolved_at NULLS LAST, alert_sent_at ASC
        ),
        reporter AS (
            SELECT DISTINCT ON (machine_name, resolved_at)
                machine_name, resolved_at, reporter_name, reporter_role
            FROM machine_downtime_logs
            WHERE reason_code IS NOT NULL
            ORDER BY machine_name, resolved_at NULLS LAST, alert_sent_at ASC
        )
    """

    rows = db.execute(text(f"""
        {base_cte}
        SELECT
            fa.id,
            e.machine_name,
            e.first_alert_at                                          AS alert_sent_at,
            e.alert_count,
            e.operator_name,
            e.machinist_name,
            e.reason_code,
            sr.name_ru                                                AS reason_name,
            sr.category                                               AS reason_category,
            sr.is_long_term                                           AS reason_is_long_term,
            e.reason_reported_at,
            rp.reporter_name,
            rp.reporter_role,
            e.resolved_at,
            CASE WHEN e.resolved_at IS NOT NULL
            THEN EXTRACT(EPOCH FROM (e.resolved_at - e.first_alert_at)) / 60
            END                                                       AS total_downtime_minutes
        FROM events e
        JOIN first_alert fa ON fa.machine_name = e.machine_name
            AND fa.resolved_at IS NOT DISTINCT FROM e.resolved_at
        LEFT JOIN reporter rp ON rp.machine_name = e.machine_name
            AND rp.resolved_at IS NOT DISTINCT FROM e.resolved_at
        LEFT JOIN stoppage_reasons sr ON sr.code = e.reason_code
        WHERE {where}
        ORDER BY e.first_alert_at DESC
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    total = db.execute(text(f"""
        {base_cte}
        SELECT COUNT(*)
        FROM events e
        JOIN first_alert fa ON fa.machine_name = e.machine_name
            AND fa.resolved_at IS NOT DISTINCT FROM e.resolved_at
        LEFT JOIN reporter rp ON rp.machine_name = e.machine_name
            AND rp.resolved_at IS NOT DISTINCT FROM e.resolved_at
        LEFT JOIN stoppage_reasons sr ON sr.code = e.reason_code
        WHERE {where}
    """), {k: v for k, v in params.items() if k not in ("limit", "offset")}).scalar()

    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "machine_name": r.machine_name,
                "alert_sent_at": r.alert_sent_at.isoformat() if r.alert_sent_at else None,
                "alert_count": r.alert_count,
                "operator_name": r.operator_name,
                "machinist_name": r.machinist_name,
                "reason_code": r.reason_code,
                "reason_name": r.reason_name,
                "reason_category": r.reason_category,
                "reason_is_long_term": r.reason_is_long_term,
                "reason_reported_at": r.reason_reported_at.isoformat() if r.reason_reported_at else None,
                "reporter_name": r.reporter_name,
                "reporter_role": r.reporter_role,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
                "total_downtime_minutes": round(r.total_downtime_minutes, 1) if r.total_downtime_minutes is not None else None,
            }
            for r in rows
        ],
    }


@router.get("/report")
def get_downtime_report(
    from_dt: datetime = Query(..., description="Начало периода (ISO datetime)"),
    to_dt: datetime = Query(..., description="Конец периода (ISO datetime)"),
    db: Session = Depends(get_db_session),
):
    """
    Агрегированный отчёт по простоям за период.

    Возвращает сводку + разбивку по операторам, станкам и причинам.
    """
    p = {"from_dt": from_dt, "to_dt": to_dt}

    # ── Сводка ────────────────────────────────────────────────────────────────
    summary_row = db.execute(text("""
        SELECT
            COUNT(*)                                                        AS total_count,
            COUNT(*) FILTER (
                WHERE EXTRACT(EPOCH FROM (resolved_at - alert_sent_at))/60 > 15
                   OR (resolved_at IS NULL AND idle_minutes > 15)
            )                                                               AS significant_count,
            COUNT(*) FILTER (WHERE reason_code IS NULL)                     AS unanswered_count,
            COALESCE(SUM(
                EXTRACT(EPOCH FROM (resolved_at - alert_sent_at))/60
            ), 0)                                                           AS total_minutes
        FROM machine_downtime_logs
        WHERE alert_sent_at >= :from_dt
          AND alert_sent_at <= :to_dt
    """), p).fetchone()

    # ── По операторам ─────────────────────────────────────────────────────────
    operator_rows = db.execute(text("""
        SELECT
            COALESCE(operator_name, '(не указан)')  AS operator_name,
            COUNT(*)                                AS stop_count,
            COALESCE(SUM(
                EXTRACT(EPOCH FROM (resolved_at - alert_sent_at))/60
            ), 0)                                   AS total_minutes
        FROM machine_downtime_logs
        WHERE alert_sent_at >= :from_dt
          AND alert_sent_at <= :to_dt
        GROUP BY COALESCE(operator_name, '(не указан)')
        ORDER BY total_minutes DESC
    """), p).fetchall()

    # ── По станкам + топ-причина ──────────────────────────────────────────────
    machine_rows = db.execute(text("""
        WITH per_machine AS (
            SELECT
                machine_name,
                COUNT(*)                                          AS stop_count,
                COALESCE(SUM(
                    EXTRACT(EPOCH FROM (resolved_at - alert_sent_at))/60
                ), 0)                                            AS total_minutes
            FROM machine_downtime_logs
            WHERE alert_sent_at >= :from_dt
              AND alert_sent_at <= :to_dt
            GROUP BY machine_name
        ),
        top_reason AS (
            SELECT DISTINCT ON (l.machine_name)
                l.machine_name,
                r.name_ru   AS top_reason_name,
                r.category  AS top_reason_category
            FROM machine_downtime_logs l
            JOIN stoppage_reasons r ON r.code = l.reason_code
            WHERE l.alert_sent_at >= :from_dt
              AND l.alert_sent_at <= :to_dt
            GROUP BY l.machine_name, r.name_ru, r.category
            ORDER BY l.machine_name, COUNT(*) DESC
        )
        SELECT
            pm.machine_name,
            pm.stop_count,
            pm.total_minutes,
            tr.top_reason_name,
            tr.top_reason_category
        FROM per_machine pm
        LEFT JOIN top_reason tr USING (machine_name)
        ORDER BY pm.total_minutes DESC
    """), p).fetchall()

    # ── По причинам ───────────────────────────────────────────────────────────
    reason_rows = db.execute(text("""
        SELECT
            r.code      AS reason_code,
            r.name_ru   AS reason_name,
            r.category  AS reason_category,
            COUNT(*)    AS stop_count,
            COALESCE(SUM(
                EXTRACT(EPOCH FROM (l.resolved_at - l.alert_sent_at))/60
            ), 0)       AS total_minutes
        FROM machine_downtime_logs l
        JOIN stoppage_reasons r ON r.code = l.reason_code
        WHERE l.alert_sent_at >= :from_dt
          AND l.alert_sent_at <= :to_dt
        GROUP BY r.code, r.name_ru, r.category
        ORDER BY total_minutes DESC
    """), p).fetchall()

    return {
        "period": {
            "from_dt": from_dt.isoformat(),
            "to_dt": to_dt.isoformat(),
        },
        "summary": {
            "total_count": summary_row.total_count,
            "significant_count": summary_row.significant_count,
            "unanswered_count": summary_row.unanswered_count,
            "total_minutes": round(float(summary_row.total_minutes or 0), 1),
        },
        "by_operator": [
            {
                "operator_name": r.operator_name,
                "stop_count": r.stop_count,
                "total_minutes": round(float(r.total_minutes or 0), 1),
            }
            for r in operator_rows
        ],
        "by_machine": [
            {
                "machine_name": r.machine_name,
                "stop_count": r.stop_count,
                "total_minutes": round(float(r.total_minutes or 0), 1),
                "top_reason_name": r.top_reason_name,
                "top_reason_category": r.top_reason_category,
            }
            for r in machine_rows
        ],
        "by_reason": [
            {
                "reason_code": r.reason_code,
                "reason_name": r.reason_name,
                "reason_category": r.reason_category,
                "stop_count": r.stop_count,
                "total_minutes": round(float(r.total_minutes or 0), 1),
            }
            for r in reason_rows
        ],
    }
