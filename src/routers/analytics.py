"""
@file: routers/analytics.py
@description: Роутер для аналитических запросов
@dependencies: FastAPI, SQLAlchemy
@created: 2024-07-31
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import text
from typing import List
from datetime import datetime, timezone

from ..database import get_db_session
from ..models.models import LotDB, PartDB, SetupDB, BatchDB, EmployeeDB, MachineDB
from ..models.reports import LotDetailReport

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
        
        # Подсчет пересчитанного количества на складе (все батчи)
        total_warehouse_quantity = sum(batch.current_quantity for batch in batches)
        
        # Определение заявленного количества на момент пересчета склада
        # Ищем последнее время изменения батчей для этого лота
        declared_quantity_at_warehouse_recount = 0
        if batches:
            last_batch_update = max(batch.updated_at for batch in batches if batch.updated_at)
            
            # Получаем показания операторов на момент последнего пересчета склада
            declared_reading_result = db.execute(text("""
                SELECT COALESCE(
                    (SELECT mr.reading 
                     FROM machine_readings mr
                     JOIN setup_jobs sj ON mr.setup_job_id = sj.id
                     WHERE sj.lot_id = :lot_id 
                       AND mr.setup_job_id IS NOT NULL
                       AND mr.created_at <= :warehouse_recount_time
                     ORDER BY mr.created_at DESC
                     LIMIT 1), 
                    0) as declared_reading
            """), {"lot_id": lot_id, "warehouse_recount_time": last_batch_update}).fetchone()
            
            declared_quantity_at_warehouse_recount = declared_reading_result.declared_reading if declared_reading_result else 0
        
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