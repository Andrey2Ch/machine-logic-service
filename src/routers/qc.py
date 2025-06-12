from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from src.database import get_db_session
from src.models.models import EmployeeDB
from pydantic import BaseModel

router = APIRouter(prefix="/lots", tags=["Quality Control"])

class LotInfoItem(BaseModel):
    id: int
    drawing_number: str
    lot_number: str
    inspector_name: Optional[str] = None
    planned_quantity: Optional[int] = None
    machine_name: Optional[str] = None

    class Config:
        from_attributes = True

@router.get("/pending-qc", response_model=List[LotInfoItem])
async def get_lots_pending_qc(
    db: Session = Depends(get_db_session),
    current_user_qa_id: Optional[int] = Query(None, alias="qaId"),
    hideCompleted: Optional[bool] = Query(False, description="Скрыть завершённые лоты"),
    dateFilter: Optional[str] = Query("all", description="all | 1month | 2months | 6months"),
):
    """Новая версия выборки лотов для ОТК с корректным machine_name"""

    try:
        # динамически собираем WHERE для фильтров (кроме QA)
        where_lot_filters: List[str] = []
        params = {}

        if dateFilter != "all":
            where_lot_filters.append("l.created_at >= :date_from")
            from datetime import datetime, timedelta
            days = {"1month": 30, "2months": 60, "6months": 180}.get(dateFilter, 0)
            params["date_from"] = datetime.utcnow() - timedelta(days=days)

        if hideCompleted:
            where_lot_filters.append("l.status != 'completed'")

        base_filters = where_lot_filters.copy()
        # далее мы добавим динамические условия open/empty/setup после сборки SQL
        where_clause_main = " AND ".join(base_filters)
        if where_clause_main:
            where_clause_main = "WHERE " + where_clause_main

        # чистый SQL с CTE ranked_setups
        sql = f"""
            WITH ranked_setups AS (
                SELECT
                    s.lot_id,
                    s.machine_id,
                    s.qa_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY s.lot_id
                        ORDER BY
                            CASE WHEN s.status IN ('created','started','pending_qa_approval') THEN 0 ELSE 1 END,
                            s.created_at DESC
                    ) AS rn
                FROM setup_jobs s
            ),
            open_batches AS (
                SELECT DISTINCT b.lot_id
                FROM batches b
                WHERE b.current_location NOT IN ('good', 'defect', 'archived')  -- warehouse_counted считается открытым
            )
            SELECT
                l.id,
                l.lot_number,
                p.drawing_number,
                m.name                AS machine_name,
                rs.qa_id              AS qa_id
            FROM   lots l
            JOIN   parts p           ON p.id = l.part_id
            LEFT   JOIN ranked_setups rs ON rs.lot_id = l.id AND rs.rn = 1
            LEFT   JOIN machines m   ON m.id = rs.machine_id
            {where_clause_main}
            {'AND' if where_clause_main else 'WHERE'} (
                    l.id IN (SELECT lot_id FROM open_batches)          -- есть хотя бы один "живой" батч
                 OR NOT EXISTS (SELECT 1 FROM batches b WHERE b.lot_id = l.id) -- ещё нет батчей
                 OR rs.qa_id IS NOT NULL                                -- активная наладка
                )
        """

        rows = db.execute(text(sql), params).fetchall()
        if not rows:
            return []

        result: List[LotInfoItem] = []
        qa_cache = {}
        if current_user_qa_id:
            qa_cache[current_user_qa_id] = db.query(EmployeeDB.full_name).filter(EmployeeDB.id == current_user_qa_id).scalar()

        for row in rows:
            qa_id = row.qa_id
            # фильтр "только мои"
            if current_user_qa_id is not None and qa_id != current_user_qa_id:
                continue

            inspector_name = None
            if qa_id:
                if qa_id not in qa_cache:
                    qa_cache[qa_id] = db.query(EmployeeDB.full_name).filter(EmployeeDB.id == qa_id).scalar()
                inspector_name = qa_cache[qa_id]

            result.append(
                LotInfoItem(
                    id=row.id,
                    drawing_number=row.drawing_number,
                    lot_number=row.lot_number,
                    planned_quantity=None,
                    inspector_name=inspector_name,
                    machine_name=row.machine_name,
                )
            )
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) 