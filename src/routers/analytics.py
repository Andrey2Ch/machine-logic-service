"""
@file: routers/analytics.py
@description: Роутер для аналитических запросов
@dependencies: FastAPI, SQLAlchemy
@created: 2024-07-31
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import text, func, bindparam, or_
from typing import List, Optional
from datetime import datetime, timezone

from ..database import get_db_session
from ..models.models import LotDB, PartDB, SetupDB, BatchDB, EmployeeDB, MachineDB, AreaDB
from ..models.reports import LotDetailReport
from ..services.metrics import aggregates_for_lots, planned_resolved

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Analytics"])

@router.get("/lots/{lot_id}/analytics", response_model=LotDetailReport)
async def get_lot_analytics(lot_id: int, db: Session = Depends(get_db_session)):
    """
    Получить детальную аналитику по конкретному лоту.
    """
    try:
        # Получаем лот с деталью
        lot = db.query(LotDB).options(selectinload(LotDB.part)).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Лот с ID {lot_id} не найден")
        
        # Получаем все наладки для лота
        setups = db.query(SetupDB).options(
            selectinload(SetupDB.machine),
            selectinload(SetupDB.operator)
        ).filter(SetupDB.lot_id == lot_id).all()
        
        # Получаем все батчи для лота
        batches = db.query(BatchDB).filter(BatchDB.lot_id == lot_id).all()
        
        # 🔧 ИСПРАВЛЕНО: Получаем последние показания для ВСЕХ наладок этого лота
        # Логика: берем последние показания для всех наладок лота, не только активных
        last_reading_result = db.execute(text("""
            SELECT COALESCE(
                (SELECT mr.reading 
                 FROM machine_readings mr
                 JOIN setup_jobs sj ON mr.setup_job_id = sj.id
                 WHERE sj.lot_id = :lot_id 
                   AND mr.setup_job_id IS NOT NULL
                 ORDER BY mr.created_at DESC
                 LIMIT 1), 
                0) as last_reading
        """), {"lot_id": lot_id}).fetchone()
        
        total_produced_quantity = last_reading_result.last_reading if last_reading_result else 0
        
        # 🔧 ИСПРАВЛЕНО 2026-01-18: "Принято" = recounted_quantity (пересчитано кладовщиком)
        # Учитываем ВСЕ батчи с warehouse_received_at, включая archived!
        # Логика: дочерние батчи (после ОТК) НЕ наследуют warehouse_received_at,
        # поэтому родительский archived батч с warehouse_received_at — это единственный
        # источник данных о приёмке. Двойного подсчёта не будет.
        warehouse_batches = [
            batch for batch in batches 
            if batch.warehouse_received_at is not None
            # Убрали '!= archived' — родительские батчи после ОТК теперь учитываются
        ]
        
        # Принято на склад = сумма recounted_quantity (пересчитанное кладовщиком)
        # Если recounted_quantity нет, используем current_quantity как fallback
        total_warehouse_quantity = sum(
            batch.recounted_quantity if batch.recounted_quantity is not None else (batch.current_quantity or 0)
            for batch in warehouse_batches
        )
        
        # Заявлено операторами = сумма current_quantity батчей принятых на склад
        declared_quantity_at_warehouse_recount = sum(
            batch.current_quantity or 0
            for batch in warehouse_batches
        )
        
        total_good_quantity = sum(batch.current_quantity for batch in batches 
                                if batch.current_location == 'good')
        
        total_defect_quantity = sum(batch.current_quantity for batch in batches 
                                  if batch.current_location == 'defect')
        
        total_rework_quantity = sum(batch.current_quantity for batch in batches 
                                  if batch.current_location == 'rework_repair')
        
        # Определение временных меток
        valid_start_times = [s.start_time for s in setups if s.start_time]
        started_at = min(valid_start_times) if valid_start_times else None

        valid_end_times = [s.end_time for s in setups if s.end_time and s.status == 'completed']
        completed_at = max(valid_end_times) if valid_end_times else None
        
        # Расчет времени выполнения
        completion_time_hours = None
        if started_at and completed_at:
            completion_time_hours = (completed_at - started_at).total_seconds() / 3600
        
        # Проверка просрочки
        is_overdue = False
        if lot.due_date and lot.status != 'completed':
            is_overdue = datetime.now(timezone.utc) > lot.due_date.replace(tzinfo=timezone.utc)
        elif lot.due_date and completed_at:
            is_overdue = completed_at.replace(tzinfo=timezone.utc) > lot.due_date.replace(tzinfo=timezone.utc)
        
        # Список станков и операторов
        machines_used = list(set(setup.machine.name for setup in setups if setup.machine))
        operators_involved = list(set(setup.operator.full_name for setup in setups if setup.operator and setup.operator.full_name))
        
        return LotDetailReport(
            lot_id=lot.id,
            lot_number=lot.lot_number,
            drawing_number=lot.part.drawing_number if lot.part else 'N/A',
            material=lot.part.material if lot.part else None,
            status=lot.status or 'unknown',
            initial_planned_quantity=lot.initial_planned_quantity,
            total_produced_quantity=total_produced_quantity,
            total_warehouse_quantity=total_warehouse_quantity,
            declared_quantity_at_warehouse_recount=declared_quantity_at_warehouse_recount,
            total_good_quantity=total_good_quantity,
            total_defect_quantity=total_defect_quantity,
            total_rework_quantity=total_rework_quantity,
            created_at=lot.created_at,
            started_at=started_at,
            completed_at=completed_at,
            due_date=lot.due_date,
            is_overdue=is_overdue,
            completion_time_hours=completion_time_hours,
            setups_count=len(setups),
            batches_count=len(batches),
            machines_used=machines_used,
            operators_involved=operators_involved
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при генерации детального отчета по лоту {lot_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при генерации отчета: {str(e)}") 


@router.get("/lots-overview")
async def lots_overview(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    active_only: bool = Query(True),
    search: Optional[str] = Query(None),
    part_search: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
    area_name: Optional[str] = Query(None),
    machine: Optional[str] = Query(None),
    order_by: Optional[str] = Query(None, description="created_at|lot_number|drawing_number|status|machine_name|area_name"),
    order: Optional[str] = Query('desc', description="asc|desc"),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    db: Session = Depends(get_db_session),
):
    """
    Агрегированный список лотов с метриками (ускоряет фронтенд).
    Возвращает: rows, total, page, per_page, total_pages
    """
    try:
        q = db.query(LotDB).join(PartDB, PartDB.id == LotDB.part_id)

        # Exclude 'new' lots. If status_filter provided, ignore any 'new' passed in.
        if status_filter:
            statuses = [s.strip() for s in status_filter.split(',') if s.strip() and s.strip() != 'new']
            if statuses:
                q = q.filter(LotDB.status.in_(statuses))
            else:
                q = q.filter(LotDB.status != 'new')
        else:
            q = q.filter(LotDB.status != 'new')

        # OM-like OR search across lot_number OR drawing_number
        if search and search.strip():
            like = f"%{search}%"
            q = q.filter(or_(LotDB.lot_number.ilike(like), PartDB.drawing_number.ilike(like)))
        elif part_search and part_search.strip():
            q = q.filter(PartDB.drawing_number.ilike(f"%{part_search}%"))
        # Date filtering by production start (first setup.start_time for the lot)
        prod_start_sq = db.query(
            SetupDB.lot_id.label('lot_id'),
            func.min(SetupDB.start_time).label('prod_start')
        ).group_by(SetupDB.lot_id).subquery()
        q = q.outerjoin(prod_start_sq, prod_start_sq.c.lot_id == LotDB.id)
        if date_from:
            q = q.filter(prod_start_sq.c.prod_start != None).filter(prod_start_sq.c.prod_start >= date_from)
        if date_to:
            q = q.filter(prod_start_sq.c.prod_start != None).filter(prod_start_sq.c.prod_start <= date_to)

        # Подсчитываем total ДО тяжёлых join'ов
        total = q.count()

        # Latest setup per lot (for machine/area filtering and sorting) — подключаем только если нужно
        need_machine_area = bool(area_name and area_name.strip()) or bool(machine and machine.strip())
        if need_machine_area or (order_by in ['machine_name', 'area_name']):
            latest_setup_sq = db.query(
                SetupDB.lot_id.label('lot_id'),
                func.max(SetupDB.id).label('setup_id')
            ).group_by(SetupDB.lot_id).subquery()
            q = q.outerjoin(latest_setup_sq, latest_setup_sq.c.lot_id == LotDB.id)
            q = q.outerjoin(SetupDB, SetupDB.id == latest_setup_sq.c.setup_id)
            q = q.outerjoin(MachineDB, MachineDB.id == SetupDB.machine_id)
            q = q.outerjoin(AreaDB, AreaDB.id == MachineDB.location_id)

            if area_name and area_name.strip():
                q = q.filter(AreaDB.name == area_name.strip())
            if machine and machine.strip():
                q = q.filter(MachineDB.name == machine.strip())

        # Server-side sorting (safe subset)
        col_map = {
            'created_at': LotDB.created_at,
            'lot_number': LotDB.lot_number,
            'drawing_number': PartDB.drawing_number,
            'status': LotDB.status,
            'machine_name': MachineDB.name,
            'area_name': AreaDB.name,
        }
        sort_col = col_map.get((order_by or 'created_at'))
        if sort_col is None:
            sort_col = LotDB.created_at

        offset = (page - 1) * per_page
        if (order or 'desc').lower() == 'asc':
            lots = q.order_by(sort_col.asc(), LotDB.id.asc()).offset(offset).limit(per_page).all()
        else:
            lots = q.order_by(sort_col.desc(), LotDB.id.desc()).offset(offset).limit(per_page).all()
        lot_ids = [l.id for l in lots]

        # planned: вычислить через setups
        setup_totals = dict(
            db.query(SetupDB.lot_id, func.max(SetupDB.planned_quantity + func.coalesce(SetupDB.additional_quantity, 0)))
              .filter(SetupDB.lot_id.in_(lot_ids))
              .group_by(SetupDB.lot_id)
              .all()
        ) if lot_ids else {}

        aggs = aggregates_for_lots(db, lot_ids)

        # 🧮 Быстрый расчёт "Произведено" (как в BatchDetails):
        # берём последнее показание счётчика по каждому лоту одним запросом
        produced_map = {}
        if lot_ids:
            produced_rows = db.execute(
                text(
                    """
                    SELECT sj.lot_id AS lot_id, mr.reading AS reading
                    FROM machine_readings mr
                    JOIN setup_jobs sj ON mr.setup_job_id = sj.id
                    WHERE sj.lot_id IN :lot_ids
                      AND mr.created_at = (
                        SELECT max(mr2.created_at)
                        FROM machine_readings mr2
                        JOIN setup_jobs sj2 ON mr2.setup_job_id = sj2.id
                        WHERE sj2.lot_id = sj.lot_id
                      )
                    """
                ).bindparams(bindparam('lot_ids', expanding=True)),
                { 'lot_ids': lot_ids }
            ).fetchall()
            produced_map = { int(row.lot_id): int(row.reading or 0) for row in produced_rows }

        # Machine/Area by latest setup per lot
        machine_area_map = {}
        if lot_ids:
            latest_setup_sq = db.query(
                SetupDB.lot_id.label('lot_id'),
                func.max(SetupDB.id).label('setup_id')
            ).filter(SetupDB.lot_id.in_(lot_ids)).group_by(SetupDB.lot_id).subquery()

            ma_rows = db.query(
                latest_setup_sq.c.lot_id,
                MachineDB.name.label('machine_name'),
                AreaDB.name.label('area_name')
            ).join(SetupDB, SetupDB.id == latest_setup_sq.c.setup_id)
            ma_rows = ma_rows.join(MachineDB, MachineDB.id == SetupDB.machine_id)
            ma_rows = ma_rows.join(AreaDB, AreaDB.id == MachineDB.location_id)
            for r in ma_rows.all():
                machine_area_map[int(r.lot_id)] = {
                    'machine_name': r.machine_name,
                    'area_name': r.area_name,
                }

        rows = []
        for l in lots:
            agg = aggs.get(l.id, {})
            rows.append({
                'id': l.id,
                'lot_number': l.lot_number,
                'drawing_number': l.part.drawing_number if l.part else None,
                'planned_total': planned_resolved(l.initial_planned_quantity, setup_totals.get(l.id)),
                'status': l.status,
                'machine_name': machine_area_map.get(l.id, {}).get('machine_name') if machine_area_map else None,
                'area_name': machine_area_map.get(l.id, {}).get('area_name') if machine_area_map else None,
                # Используем точное значение "Произведено" (последнее показание счётчика)
                'operators_reported': produced_map.get(l.id, agg.get('operators_reported', 0)),
                'warehouse_received': agg.get('warehouse_received', 0),
                'good_qty': agg.get('good_qty', 0),
                'defect_qty': agg.get('defect_qty', 0),
                'qa_inspector': agg.get('qa_inspector_name'),
            })

        return {
            'rows': rows,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page,
        }
    except Exception as e:
        logger.error(f"lots_overview error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="lots_overview error")