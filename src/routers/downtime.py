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
    """Одна строка на алерт. Длительность = resolved_at - alert_sent_at."""
    filters = ["1=1"]
    params: dict = {"limit": limit, "offset": offset}

    if machine_name:
        filters.append("LOWER(l.machine_name) LIKE LOWER(:machine_name)")
        params["machine_name"] = f"%{machine_name}%"
    if from_dt:
        filters.append("l.alert_sent_at >= :from_dt")
        params["from_dt"] = from_dt
    if to_dt:
        filters.append("l.alert_sent_at <= :to_dt")
        params["to_dt"] = to_dt
    if category:
        filters.append("r.category = :category")
        params["category"] = category
    if unanswered_only:
        filters.append("l.reason_code IS NULL")

    where = " AND ".join(filters)

    rows = db.execute(text(f"""
        SELECT
            l.id,
            l.machine_name,
            l.alert_sent_at,
            l.idle_minutes,
            l.operator_name,
            l.machinist_name,
            l.reason_code,
            r.name_ru       AS reason_name,
            r.category      AS reason_category,
            r.is_long_term  AS reason_is_long_term,
            l.reason_reported_at,
            l.reporter_name,
            l.reporter_role,
            l.resolved_at,
            CASE WHEN l.resolved_at IS NOT NULL
            AND l.alert_sent_at = (
                SELECT MIN(l2.alert_sent_at)
                FROM machine_downtime_logs l2
                WHERE l2.machine_name = l.machine_name
                  AND l2.resolved_at = l.resolved_at
            )
            THEN EXTRACT(EPOCH FROM (l.resolved_at - l.alert_sent_at)) / 60
            END             AS total_downtime_minutes
        FROM machine_downtime_logs l
        LEFT JOIN stoppage_reasons r ON r.code = l.reason_code
        WHERE {where}
        ORDER BY l.alert_sent_at DESC
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    total = db.execute(text(f"""
        SELECT COUNT(*)
        FROM machine_downtime_logs l
        LEFT JOIN stoppage_reasons r ON r.code = l.reason_code
        WHERE {where}
    """), {k: v for k, v in params.items() if k not in ("limit", "offset")}).scalar()

    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "machine_name": r.machine_name,
                "alert_sent_at": r.alert_sent_at.isoformat() if r.alert_sent_at else None,
                "idle_minutes_at_alert": round(float(r.idle_minutes or 0), 1),
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
                "total_downtime_minutes": round(float(r.total_downtime_minutes), 1) if r.total_downtime_minutes is not None else None,
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
