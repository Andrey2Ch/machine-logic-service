"""
Роутер для утреннего дашборда - ключевые метрики для быстрого обзора
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text, func
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel

from ..database import get_db_session
from ..models.models import LotDB, BatchDB, SetupDB, MachineDB

import logging
logger = logging.getLogger(__name__)

router = APIRouter(tags=["Morning Dashboard"])


# ==================== MODELS ====================

class AcceptanceDiscrepancy(BaseModel):
    """Расхождение при приемке для одного станка"""
    machine_name: str
    lot_count: int  # Количество активных лотов
    declared_quantity: int  # Заявлено операторами (на момент пересчета склада)
    accepted_quantity: int  # Фактически принял склад
    discrepancy_absolute: int  # Разница (может быть отрицательной)
    discrepancy_percent: float  # Процент расхождения
    status: str  # 'critical', 'warning', 'ok'

class DefectRate(BaseModel):
    """Процент брака по станку"""
    machine_name: str
    lot_count: int
    total_produced: int  # Всего произведено (принято + в производстве)
    total_defect: int  # Брак
    total_good: int  # Годные
    defect_percent: float  # % брака
    status: str  # 'critical', 'warning', 'ok'

class Absence(BaseModel):
    """Отсутствующий сотрудник"""
    employee_name: str
    role: str
    reason: str  # 'vacation', 'sick_leave', 'day_off'
    start_date: datetime
    end_date: datetime
    days_remaining: int

class Deadline(BaseModel):
    """Дедлайн заказа"""
    lot_id: int
    lot_number: str
    drawing_number: str
    machine_name: str
    planned_quantity: int
    produced_quantity: int
    remaining_quantity: int
    due_date: datetime
    hours_until_deadline: float
    status: str  # 'critical', 'warning', 'ok'

class MorningSummary(BaseModel):
    """Полная утренняя сводка"""
    timestamp: datetime
    acceptance_discrepancies: List[AcceptanceDiscrepancy]
    defect_rates: List[DefectRate]
    absences_today: List[Absence]
    absences_tomorrow: List[Absence]
    deadlines_today: List[Deadline]
    deadlines_tomorrow: List[Deadline]
    summary_stats: dict


# ==================== НАСТРОЙКИ ПОРОГОВ ====================

DEFAULT_THRESHOLDS = {
    "acceptance_discrepancy": {
        "critical_percent": 5.0,
        "critical_absolute": 200,
        "warning_percent": 2.0,
        "warning_absolute": 50
    },
    "defect_rate": {
        "critical_percent": 5.0,
        "warning_percent": 2.0
    }
}


# ==================== HELPER FUNCTIONS ====================

def calculate_status(discrepancy_abs: int, discrepancy_percent: float, thresholds: dict) -> str:
    """Определяет статус на основе порогов"""
    # Только недостачи считаем проблемой (отрицательные значения)
    if discrepancy_abs >= 0:
        return 'ok'  # Излишек - не проблема
    
    abs_value = abs(discrepancy_abs)
    abs_percent = abs(discrepancy_percent)
    
    if abs_percent >= thresholds['critical_percent'] or abs_value >= thresholds['critical_absolute']:
        return 'critical'
    elif abs_percent >= thresholds['warning_percent'] or abs_value >= thresholds['warning_absolute']:
        return 'warning'
    else:
        return 'ok'


# ==================== API ENDPOINTS ====================

@router.get("/morning/acceptance-discrepancies", response_model=List[AcceptanceDiscrepancy])
async def get_acceptance_discrepancies(
    days: int = Query(7, ge=1, le=30, description="Период в днях"),
    db: Session = Depends(get_db_session)
):
    """
    Расхождения при приемке склада по станкам
    Показывает разницу между заявленным и принятым количеством
    """
    try:
        # SQL запрос для расчета расхождений
        query = text("""
        WITH lot_stats AS (
            SELECT 
                l.id as lot_id,
                sj.machine_id,
                m.name as machine_name,
                
                -- Заявлено на момент пересчета склада (declared_quantity_at_warehouse_recount)
                COALESCE((
                    SELECT mr.reading 
                    FROM machine_readings mr
                    JOIN setup_jobs sj2 ON mr.setup_job_id = sj2.id
                    WHERE sj2.lot_id = l.id 
                      AND mr.setup_job_id IS NOT NULL
                      AND mr.created_at <= (
                          SELECT MAX(warehouse_received_at) 
                          FROM batches 
                          WHERE lot_id = l.id 
                            AND warehouse_received_at IS NOT NULL
                      )
                    ORDER BY mr.created_at DESC
                    LIMIT 1
                ), 0) as declared,
                
                -- Фактически принято складом (total_warehouse_quantity)
                COALESCE((
                    SELECT SUM(current_quantity)
                    FROM batches
                    WHERE lot_id = l.id
                      AND warehouse_received_at IS NOT NULL
                      AND current_location != 'archived'
                ), 0) as accepted
                
            FROM lots l
            JOIN setup_jobs sj ON sj.lot_id = l.id
            LEFT JOIN machines m ON m.id = sj.machine_id
            WHERE l.created_at >= NOW() - INTERVAL '1 day' * :days
              AND l.status NOT IN ('cancelled', 'closed')
              AND sj.machine_id IS NOT NULL
        )
        SELECT 
            machine_name,
            COUNT(DISTINCT lot_id) as lot_count,
            SUM(declared) as total_declared,
            SUM(accepted) as total_accepted,
            SUM(declared - accepted) as discrepancy_abs,
            CASE 
                WHEN SUM(declared) > 0 
                THEN ROUND(((SUM(declared) - SUM(accepted))::numeric / SUM(declared)) * 100, 2)
                ELSE 0 
            END as discrepancy_percent
        FROM lot_stats
        WHERE machine_name IS NOT NULL
          AND declared > 0  -- Только где был пересчет склада
        GROUP BY machine_name
        ORDER BY ABS(SUM(declared - accepted)) DESC;
        """)
        
        result = db.execute(query, {"days": days}).fetchall()
        
        thresholds = DEFAULT_THRESHOLDS['acceptance_discrepancy']
        
        discrepancies = []
        for row in result:
            status = calculate_status(row.discrepancy_abs, row.discrepancy_percent, thresholds)
            
            discrepancies.append(AcceptanceDiscrepancy(
                machine_name=row.machine_name,
                lot_count=row.lot_count,
                declared_quantity=int(row.total_declared),
                accepted_quantity=int(row.total_accepted),
                discrepancy_absolute=int(row.discrepancy_abs),
                discrepancy_percent=float(row.discrepancy_percent),
                status=status
            ))
        
        return discrepancies
        
    except Exception as e:
        logger.error(f"Error getting acceptance discrepancies: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/morning/defect-rates", response_model=List[DefectRate])
async def get_defect_rates(
    days: int = Query(7, ge=1, le=30, description="Период в днях"),
    db: Session = Depends(get_db_session)
):
    """
    Процент брака по станкам за период
    """
    try:
        query = text("""
        WITH lot_stats AS (
            SELECT 
                l.id as lot_id,
                sj.machine_id,
                m.name as machine_name,
                
                -- Всего принято (без archived)
                COALESCE((
                    SELECT SUM(current_quantity)
                    FROM batches
                    WHERE lot_id = l.id
                      AND warehouse_received_at IS NOT NULL
                      AND current_location != 'archived'
                ), 0) as total_accepted,
                
                -- Годные
                COALESCE((
                    SELECT SUM(current_quantity)
                    FROM batches
                    WHERE lot_id = l.id
                      AND current_location = 'good'
                ), 0) as good,
                
                -- Брак
                COALESCE((
                    SELECT SUM(current_quantity)
                    FROM batches
                    WHERE lot_id = l.id
                      AND current_location = 'defect'
                ), 0) as defect
                
            FROM lots l
            JOIN setup_jobs sj ON sj.lot_id = l.id
            LEFT JOIN machines m ON m.id = sj.machine_id
            WHERE l.created_at >= NOW() - INTERVAL '1 day' * :days
              AND l.status NOT IN ('cancelled')
              AND sj.machine_id IS NOT NULL
        )
        SELECT 
            machine_name,
            COUNT(DISTINCT lot_id) as lot_count,
            SUM(total_accepted) as total_produced,
            SUM(good) as total_good,
            SUM(defect) as total_defect,
            CASE 
                WHEN SUM(total_accepted) > 0 
                THEN ROUND((SUM(defect)::numeric / SUM(total_accepted)) * 100, 2)
                ELSE 0 
            END as defect_percent
        FROM lot_stats
        WHERE machine_name IS NOT NULL
          AND total_accepted > 0
        GROUP BY machine_name
        ORDER BY defect_percent DESC;
        """)
        
        result = db.execute(query, {"days": days}).fetchall()
        
        thresholds = DEFAULT_THRESHOLDS['defect_rate']
        
        defect_rates = []
        for row in result:
            defect_pct = float(row.defect_percent)
            
            if defect_pct >= thresholds['critical_percent']:
                status = 'critical'
            elif defect_pct >= thresholds['warning_percent']:
                status = 'warning'
            else:
                status = 'ok'
            
            defect_rates.append(DefectRate(
                machine_name=row.machine_name,
                lot_count=row.lot_count,
                total_produced=int(row.total_produced),
                total_good=int(row.total_good),
                total_defect=int(row.total_defect),
                defect_percent=defect_pct,
                status=status
            ))
        
        return defect_rates
        
    except Exception as e:
        logger.error(f"Error getting defect rates: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/morning/deadlines", response_model=List[Deadline])
async def get_deadlines(
    days_ahead: int = Query(1, ge=0, le=7, description="Смотреть вперед на N дней"),
    db: Session = Depends(get_db_session)
):
    """
    Дедлайны на сегодня или ближайшие дни
    """
    try:
        now = datetime.now(timezone.utc)
        target_date = now + timedelta(days=days_ahead)
        
        query = text("""
        SELECT 
            l.id as lot_id,
            l.lot_number,
            p.drawing_number,
            m.name as machine_name,
            l.initial_planned_quantity as planned_quantity,
            l.due_date,
            
            -- Последнее показание счетчика (произведено)
            COALESCE((
                SELECT mr.reading 
                FROM machine_readings mr
                JOIN setup_jobs sj ON mr.setup_job_id = sj.id
                WHERE sj.lot_id = l.id 
                ORDER BY mr.created_at DESC
                LIMIT 1
            ), 0) as produced_quantity
            
        FROM lots l
        JOIN parts p ON p.id = l.part_id
        JOIN setup_jobs sj ON sj.lot_id = l.id
        LEFT JOIN machines m ON m.id = sj.machine_id
        WHERE l.due_date IS NOT NULL
          AND l.due_date::date = :target_date::date
          AND l.status NOT IN ('completed', 'cancelled', 'closed')
        ORDER BY l.due_date ASC;
        """)
        
        result = db.execute(query, {
            "target_date": target_date.strftime('%Y-%m-%d')
        }).fetchall()
        
        deadlines = []
        for row in result:
            remaining = row.planned_quantity - row.produced_quantity
            hours_until = (row.due_date.replace(tzinfo=timezone.utc) - now).total_seconds() / 3600
            
            # Статус по критичности
            if hours_until < 4:  # Меньше 4 часов
                status = 'critical'
            elif hours_until < 12:  # Меньше 12 часов
                status = 'warning'
            else:
                status = 'ok'
            
            # Если уже просрочено
            if hours_until < 0:
                status = 'overdue'
            
            deadlines.append(Deadline(
                lot_id=row.lot_id,
                lot_number=row.lot_number,
                drawing_number=row.drawing_number,
                machine_name=row.machine_name or 'N/A',
                planned_quantity=row.planned_quantity,
                produced_quantity=row.produced_quantity,
                remaining_quantity=remaining,
                due_date=row.due_date,
                hours_until_deadline=round(hours_until, 1),
                status=status
            ))
        
        return deadlines
        
    except Exception as e:
        logger.error(f"Error getting deadlines: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/morning/summary")
async def get_morning_summary(
    period_days: int = Query(7, ge=1, le=30, description="Период для расхождений и брака"),
    db: Session = Depends(get_db_session)
):
    """
    Полная утренняя сводка - все ключевые метрики
    """
    try:
        # Получаем все данные параллельно
        discrepancies = await get_acceptance_discrepancies(period_days, db)
        defect_rates = await get_defect_rates(period_days, db)
        deadlines_today = await get_deadlines(0, db)
        deadlines_tomorrow = await get_deadlines(1, db)
        
        # TODO: Добавить отпуска из calendar API
        
        # Сводная статистика
        total_discrepancy = sum(d.discrepancy_absolute for d in discrepancies)
        critical_count = sum(1 for d in discrepancies if d.status == 'critical')
        avg_defect_rate = sum(d.defect_percent for d in defect_rates) / len(defect_rates) if defect_rates else 0
        
        return {
            "timestamp": datetime.now(timezone.utc),
            "acceptance_discrepancies": discrepancies,
            "defect_rates": defect_rates,
            "absences_today": [],  # TODO: Интеграция с calendar
            "absences_tomorrow": [],  # TODO: Интеграция с calendar
            "deadlines_today": deadlines_today,
            "deadlines_tomorrow": deadlines_tomorrow,
            "summary_stats": {
                "total_discrepancy_parts": total_discrepancy,
                "critical_discrepancies_count": critical_count,
                "average_defect_rate": round(avg_defect_rate, 2),
                "deadlines_today_count": len(deadlines_today),
                "deadlines_tomorrow_count": len(deadlines_tomorrow)
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting morning summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

