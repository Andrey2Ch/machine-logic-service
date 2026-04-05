"""
Downtime logs API — просмотр журнала простоев станков.
"""

from datetime import datetime, timezone
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
    Список логов простоев с фильтрами.

    Возвращает записи из machine_downtime_logs, обогащённые данными из stoppage_reasons.
    Длительность простоя = resolved_at - alert_sent_at (NULL если не разрешён).
    """
    filters = ["1=1"]
    params: dict = {"limit": limit, "offset": offset}

    if machine_name:
        filters.append("l.machine_name = :machine_name")
        params["machine_name"] = machine_name

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
            r.name_ru        AS reason_name,
            r.category       AS reason_category,
            r.is_long_term   AS reason_is_long_term,
            l.reason_reported_at,
            l.reporter_name,
            l.reporter_role,
            l.resolved_at,
            CASE
                WHEN l.resolved_at IS NOT NULL
                THEN EXTRACT(EPOCH FROM (l.resolved_at - l.alert_sent_at)) / 60
            END              AS total_downtime_minutes
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
                "idle_minutes_at_alert": round(r.idle_minutes or 0, 1),
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
