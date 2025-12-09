"""
Роутер для утреннего дашборда - ключевые метрики для быстрого обзора
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text, func
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone, date
from pydantic import BaseModel
import os
import httpx

from ..database import get_db_session
from ..models.models import LotDB, BatchDB, SetupDB, MachineDB

import logging
logger = logging.getLogger(__name__)

router = APIRouter(tags=["Morning Dashboard"])

# URL израматского дашборда для вызова API morning-report
_dashboard_url = os.getenv("ISRAMAT_DASHBOARD_URL", "http://localhost:3000")
# Автоматически добавляем https:// если отсутствует протокол
if not _dashboard_url.startswith(('http://', 'https://')):
    ISRAMAT_DASHBOARD_URL = f"https://{_dashboard_url}"
else:
    ISRAMAT_DASHBOARD_URL = _dashboard_url


# ==================== MODELS ====================

class AcceptanceDiscrepancy(BaseModel):
    """Расхождение при приемке для одного станка"""
    machine_name: str
    drawing_numbers: str  # Номера чертежей (через запятую если несколько)
    declared_quantity: int  # Заявлено операторами (на момент пересчета склада)
    accepted_quantity: int  # Фактически принял склад
    discrepancy_absolute: int  # Разница (может быть отрицательной)
    discrepancy_percent: float  # Процент расхождения
    status: str  # 'critical', 'warning', 'ok'

class LotDefectInfo(BaseModel):
    """Информация о браке по одному лоту"""
    lot_id: int
    drawing_number: str
    lot_status: str  # 'in_production', 'post_production', 'closed'
    total_produced: int
    total_defect: int
    defect_percent: float
    defect_recent: Optional[int] = None
    period_days: Optional[int] = None

class MachineDefectRate(BaseModel):
    """Процент брака по станку (с группировкой лотов)"""
    machine_name: str
    total_produced: int  # Сумма по всем лотам
    total_defect: int
    defect_percent: float
    status: str  # 'critical', 'warning', 'ok'
    lots: List[LotDefectInfo]  # Список лотов этого станка

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
    defect_rates: List[MachineDefectRate]
    absences_today: List[Absence]
    absences_tomorrow: List[Absence]
    deadlines_today: List[Deadline]
    deadlines_tomorrow: List[Deadline]
    summary_stats: dict

class MachineInfo(BaseModel):
    """Информация о станке для плана работы"""
    machine_name: str
    lot_number: str
    drawing_number: str
    status: str
    progress: str  # "180/200"
    hours_remaining: float
    estimated_completion: str
    priority: str  # Приоритет наладки

class MachinePlan(BaseModel):
    """План работы станков на день"""
    setup_now: List[MachineInfo]  # Требуют наладки СЕЙЧАС
    setup_today: List[MachineInfo]  # Закончат сегодня (< 9ч)
    setup_next_shift: List[MachineInfo]  # Следующая смена
    setup_tomorrow: List[MachineInfo]  # Завтра


# ==================== НАСТРОЙКИ ПОРОГОВ ====================

DEFAULT_THRESHOLDS = {
    "acceptance_discrepancy": {
        "critical_percent": 5.0,  # Только % (убрали абсолютные значения)
        "warning_percent": 2.0
    },
    "defect_rate": {
        "critical_percent": 5.0,
        "warning_percent": 2.0
    }
}


# ==================== HELPER FUNCTIONS ====================

def calculate_status(discrepancy_abs: int, discrepancy_percent: float, thresholds: dict) -> str:
    """Определяет статус ТОЛЬКО на основе процентов (без абсолютных значений)"""
    # Только недостачи считаем проблемой (отрицательные значения)
    if discrepancy_abs >= 0:
        return 'ok'  # Излишек - не проблема
    
    abs_percent = abs(discrepancy_percent)
    
    # Только проценты! Убрали критерии по абсолютным значениям
    if abs_percent >= thresholds['critical_percent']:
        return 'critical'
    elif abs_percent >= thresholds['warning_percent']:
        return 'warning'
    else:
        return 'ok'


# ==================== API ENDPOINTS ====================

@router.get("/morning/acceptance-discrepancies", response_model=List[AcceptanceDiscrepancy])
async def get_acceptance_discrepancies(
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
                p.drawing_number,
                
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
            JOIN parts p ON p.id = l.part_id
            LEFT JOIN machines m ON m.id = sj.machine_id
            WHERE (
                l.status = 'in_production'  -- Все в производстве
                
                OR
                
                (l.status = 'post_production' AND l.id = (
                    SELECT l2.id 
                    FROM lots l2
                    WHERE l2.status = 'post_production'
                    ORDER BY (
                        SELECT MAX(sj2.end_time)
                        FROM setup_jobs sj2
                        WHERE sj2.lot_id = l2.id AND sj2.status = 'completed'
                    ) DESC NULLS LAST
                    LIMIT 1
                ))
            )
            AND sj.machine_id IS NOT NULL
        )
        SELECT 
            machine_name,
            STRING_AGG(DISTINCT drawing_number, ', ' ORDER BY drawing_number) as drawing_numbers,
            SUM(declared) as total_declared,
            SUM(accepted) as total_accepted,
            SUM(accepted - declared) as discrepancy_abs,
            CASE 
                WHEN SUM(declared) > 0 
                THEN ROUND(((SUM(accepted) - SUM(declared))::numeric / SUM(declared)) * 100, 2)
                ELSE 0 
            END as discrepancy_percent
        FROM lot_stats
        WHERE machine_name IS NOT NULL
          AND declared > 0  -- Только где был пересчет склада
        GROUP BY machine_name
        ORDER BY ABS(SUM(declared - accepted)) DESC;
        """)
        
        result = db.execute(query).fetchall()
        
        thresholds = DEFAULT_THRESHOLDS['acceptance_discrepancy']
        
        discrepancies = []
        for row in result:
            status = calculate_status(row.discrepancy_abs, row.discrepancy_percent, thresholds)
            
            discrepancies.append(AcceptanceDiscrepancy(
                machine_name=row.machine_name,
                drawing_numbers=row.drawing_numbers or '-',
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


@router.get("/morning/defect-rates", response_model=List[MachineDefectRate])
async def get_defect_rates(
    db: Session = Depends(get_db_session)
):
    """
    Процент брака по станкам (с группировкой лотов):
    - in_production: все лоты без ограничений
    - post_production/closed: последний завершённый лот для каждого станка за 7 дней
    - Группировка по станку, сортировка по общему % брака DESC
    - post_production с 0% брака НЕ показываются
    """
    try:
        query = text("""
        WITH latest_completed AS (
            -- Последний завершённый лот для каждого станка за последние 7 дней
            SELECT DISTINCT ON (sj.machine_id)
                l.id as lot_id,
                sj.machine_id
            FROM lots l
            JOIN setup_jobs sj ON sj.lot_id = l.id AND sj.status = 'completed'
            WHERE l.status IN ('post_production', 'closed')
              AND sj.machine_id IS NOT NULL
              AND sj.end_time > NOW() - INTERVAL '7 days'
            ORDER BY sj.machine_id, sj.end_time DESC NULLS LAST
        ),
        lot_last_activity AS (
            -- Последняя активность (батч) для каждого лота
            SELECT 
                b.lot_id,
                MAX(b.warehouse_received_at) as last_batch_time,
                (CURRENT_DATE - MAX(b.warehouse_received_at)::date) as days_ago
            FROM batches b
            WHERE b.warehouse_received_at IS NOT NULL
            GROUP BY b.lot_id
        ),
        lot_data AS (
            SELECT DISTINCT ON (l.id)
                l.id as lot_id,
                l.status as lot_status,
                p.drawing_number,
                m.name as machine_name,
                sj.machine_id,
                
                -- Всего принято (без archived)
                COALESCE((
                    SELECT SUM(current_quantity)
                    FROM batches
                    WHERE lot_id = l.id
                      AND warehouse_received_at IS NOT NULL
                      AND current_location != 'archived'
                ), 0) as total_accepted,
                
                -- Брак (всего)
                COALESCE((
                    SELECT SUM(current_quantity)
                    FROM batches
                    WHERE lot_id = l.id
                      AND current_location = 'defect'
                ), 0) as total_defect,
                
                -- Брак с последней активности лота (с начала того дня)
                COALESCE((
                    SELECT SUM(b2.current_quantity)
                    FROM batches b2
                    WHERE b2.lot_id = l.id
                      AND b2.current_location = 'defect'
                      AND b2.warehouse_received_at >= (
                          SELECT DATE_TRUNC('day', lla.last_batch_time)
                          FROM lot_last_activity lla
                          WHERE lla.lot_id = l.id
                      )
                ), 0) as defect_recent,
                
                -- Дней назад последняя активность
                (SELECT lla.days_ago FROM lot_last_activity lla WHERE lla.lot_id = l.id) as period_days
                
            FROM lots l
            JOIN parts p ON p.id = l.part_id
            JOIN setup_jobs sj ON sj.lot_id = l.id
            LEFT JOIN machines m ON m.id = sj.machine_id
            WHERE sj.machine_id IS NOT NULL
              AND (
                l.status = 'in_production'
                OR
                EXISTS (
                    SELECT 1 FROM latest_completed lc 
                    WHERE lc.lot_id = l.id AND lc.machine_id = sj.machine_id
                )
              )
            ORDER BY l.id, sj.end_time DESC NULLS LAST
        )
        SELECT 
            lot_id,
            lot_status,
            drawing_number,
            machine_name,
            total_accepted,
            total_defect,
            defect_recent,
            period_days,
            CASE 
                WHEN total_accepted > 0 
                THEN ROUND((total_defect::numeric / total_accepted) * 100, 2)
                ELSE 0 
            END as defect_percent
        FROM lot_data
        WHERE machine_name IS NOT NULL
        ORDER BY machine_name, 
            CASE lot_status WHEN 'in_production' THEN 0 ELSE 1 END;
        """)
        
        result = db.execute(query).fetchall()
        
        thresholds = DEFAULT_THRESHOLDS['defect_rate']
        
        # Группируем по станкам
        machines_dict: dict = {}
        for row in result:
            machine = row.machine_name
            defect_pct = float(row.defect_percent)
            lot_status = row.lot_status
            
            # Пропускаем post_production с 0% брака
            if lot_status != 'in_production' and defect_pct == 0:
                continue
            
            if machine not in machines_dict:
                machines_dict[machine] = {
                    'total_produced': 0,
                    'total_defect': 0,
                    'lots': []
                }
            
            machines_dict[machine]['total_produced'] += int(row.total_accepted)
            machines_dict[machine]['total_defect'] += int(row.total_defect)
            machines_dict[machine]['lots'].append(LotDefectInfo(
                lot_id=row.lot_id,
                drawing_number=row.drawing_number or '-',
                lot_status=lot_status,
                total_produced=int(row.total_accepted),
                total_defect=int(row.total_defect),
                defect_percent=defect_pct,
                defect_recent=int(row.defect_recent) if row.defect_recent else None,
                period_days=int(row.period_days) if row.period_days is not None else None
            ))
        
        # Формируем результат с сортировкой по % брака
        defect_rates = []
        for machine, data in machines_dict.items():
            total_pct = round((data['total_defect'] / data['total_produced'] * 100), 2) if data['total_produced'] > 0 else 0
            
            if total_pct >= thresholds['critical_percent']:
                status = 'critical'
            elif total_pct >= thresholds['warning_percent']:
                status = 'warning'
            else:
                status = 'ok'
            
            defect_rates.append(MachineDefectRate(
                machine_name=machine,
                total_produced=data['total_produced'],
                total_defect=data['total_defect'],
                defect_percent=total_pct,
                status=status,
                lots=data['lots']
            ))
        
        # Сортируем по % брака DESC
        defect_rates.sort(key=lambda x: x.defect_percent, reverse=True)
        
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
          AND DATE(l.due_date) = DATE(:target_date)
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


@router.get("/morning/machine-plan", response_model=MachinePlan)
async def get_machine_plan():
    """
    План работы станков на день - группировка по приоритетам наладки
    
    Переиспользует данные из /api/morning-report (израмат-дашборд),
    фильтрует и группирует по приоритетам наладки:
    - SETUP NOW: требуют наладки СЕЙЧАС
    - SETUP: закончат в эту смену (< 9ч)
    - SETUP next shift: следующая смена
    - SETUP tomorrow: завтра
    """
    try:
        # Вызываем существующий API morning-report
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{ISRAMAT_DASHBOARD_URL}/api/morning-report")
            response.raise_for_status()
            data = response.json()
        
        machines = data.get('data', [])
        
        # Группируем по приоритетам наладки
        def create_machine_info(m: Dict[str, Any]) -> MachineInfo:
            """Конвертируем данные из morning-report в MachineInfo"""
            # Прогресс
            current = m.get("Текущее количество", 0)
            planned = m.get("План", 0)
            additional = m.get("Доп. кол.", 0)
            total_plan = planned + additional
            
            return MachineInfo(
                machine_name=m.get("Станок", ""),
                lot_number=m.get("Номер лота", ""),
                drawing_number=m.get("Чертёж", ""),
                status=m.get("Статус", ""),
                progress=f"{current}/{total_plan}" if total_plan > 0 else "-",
                hours_remaining=float(m.get("Осталось часов", 0)),
                estimated_completion=m.get("Дата и время окончания", ""),
                priority=m.get("Приоритет наладки", "")
            )
        
        # Фильтруем по приоритетам
        setup_now = [
            create_machine_info(m) for m in machines 
            if m.get("Приоритет наладки") == "SETUP NOW"
        ]
        
        setup_today = [
            create_machine_info(m) for m in machines 
            if m.get("Приоритет наладки") == "SETUP"
        ]
        
        setup_next_shift = [
            create_machine_info(m) for m in machines 
            if m.get("Приоритет наладки") == "SETUP next shift"
        ]
        
        setup_tomorrow = [
            create_machine_info(m) for m in machines 
            if m.get("Приоритет наладки") == "SETUP tomorrow"
        ]
        
        return MachinePlan(
            setup_now=setup_now,
            setup_today=setup_today,
            setup_next_shift=setup_next_shift,
            setup_tomorrow=setup_tomorrow
        )
        
    except httpx.HTTPError as e:
        logger.error(f"Error calling morning-report API: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"Ошибка вызова morning-report API: {str(e)}")
    except Exception as e:
        logger.error(f"Error getting machine plan: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def get_operator_rework_stats(target_date: date, db: Session) -> dict:
    """
    Статистика батчей на переборку от операторов за последний рабочий день (СМЕНУ)
    
    ВАЖНО: Смена начинается в 06:00 и заканчивается в 06:00 следующего дня.
    Батч созданный 05.12 в 03:35 относится к СМЕНЕ 04.12 (которая длится с 04.12 06:00 до 05.12 06:00).
    
    Берет батчи с current_location IN ('sorting', 'sorting_warehouse'):
    - 'sorting' - батчи от операторов, еще не принятые на складе
    - 'sorting_warehouse' - батчи на переборку, принятые на складе, но еще не взятые ОТК
    
    Returns:
        {
            'total_batches': int,
            'total_parts': int,
            'by_machine': [{'machine': str, 'batches': [...], ...}],
            'by_operator': [{'operator': str, 'batches': [...], ...}],
            'shift_date': date
        }
    """
    try:
        # Получаем последний рабочий день (пропускаем пятницу и субботу)
        shift_date = get_previous_workday(target_date)
        
        # Смена: с 06:00 shift_date до 06:00 следующего дня
        shift_start = datetime.combine(shift_date, datetime.min.time().replace(hour=6))
        shift_end = shift_start + timedelta(days=1)
        
        # Все батчи на переборку за эту смену (детальный список)
        all_batches_query = text("""
        SELECT 
            b.id,
            b.batch_time,
            b.current_quantity,
            b.current_location,
            m.name as machine_name,
            e.full_name as operator_name,
            p.drawing_number,
            l.lot_number
        FROM batches b
        JOIN setup_jobs sj ON b.setup_job_id = sj.id
        JOIN machines m ON sj.machine_id = m.id
        JOIN employees e ON b.operator_id = e.id
        LEFT JOIN lots l ON b.lot_id = l.id
        LEFT JOIN parts p ON l.part_id = p.id
        WHERE b.parent_batch_id IS NULL
          AND b.current_location IN ('sorting', 'sorting_warehouse')
          AND b.batch_time >= :shift_start
          AND b.batch_time < :shift_end
        ORDER BY b.batch_time
        """)
        
        all_batches = db.execute(all_batches_query, {'shift_start': shift_start, 'shift_end': shift_end}).fetchall()
        
        total_batches = len(all_batches)
        total_parts = sum(b.current_quantity for b in all_batches)
        
        # Группируем по станкам
        by_machine_dict = {}
        for b in all_batches:
            if b.machine_name not in by_machine_dict:
                by_machine_dict[b.machine_name] = {
                    'machine': b.machine_name,
                    'batches_list': [],
                    'total_parts': 0
                }
            by_machine_dict[b.machine_name]['batches_list'].append({
                'id': b.id,
                'batch_time': b.batch_time.isoformat() if b.batch_time else None,
                'quantity': b.current_quantity,
                'location': b.current_location,
                'operator': b.operator_name,
                'drawing_number': b.drawing_number or '-',
                'lot_number': b.lot_number or '-'
            })
            by_machine_dict[b.machine_name]['total_parts'] += b.current_quantity
        
        by_machine = [
            {
                'machine': data['machine'],
                'batches_count': len(data['batches_list']),
                'parts': data['total_parts'],
                'percent': round((data['total_parts'] / total_parts * 100) if total_parts > 0 else 0, 1),
                'batches': data['batches_list']
            }
            for data in sorted(by_machine_dict.values(), key=lambda x: x['total_parts'], reverse=True)
        ]
        
        # Группируем по операторам
        by_operator_dict = {}
        for b in all_batches:
            if b.operator_name not in by_operator_dict:
                by_operator_dict[b.operator_name] = {
                    'operator': b.operator_name,
                    'batches_list': [],
                    'total_parts': 0
                }
            by_operator_dict[b.operator_name]['batches_list'].append({
                'id': b.id,
                'batch_time': b.batch_time.isoformat() if b.batch_time else None,
                'quantity': b.current_quantity,
                'location': b.current_location,
                'machine': b.machine_name,
                'drawing_number': b.drawing_number or '-',
                'lot_number': b.lot_number or '-'
            })
            by_operator_dict[b.operator_name]['total_parts'] += b.current_quantity
        
        by_operator = [
            {
                'operator': data['operator'],
                'batches_count': len(data['batches_list']),
                'parts': data['total_parts'],
                'batches': data['batches_list']
            }
            for data in sorted(by_operator_dict.values(), key=lambda x: x['total_parts'], reverse=True)
        ]
        
        return {
            'total_batches': total_batches,
            'total_parts': total_parts,
            'by_machine': by_machine,
            'by_operator': by_operator,
            'shift_date': shift_date.isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error getting operator rework stats: {e}", exc_info=True)
        return {'total_batches': 0, 'total_parts': 0, 'by_machine': [], 'by_operator': []}


def get_previous_workday(from_date: date) -> date:
    """
    Возвращает предыдущий рабочий день с учетом израильских выходных (пятница, суббота)
    
    Python weekday():
    - Monday = 0, Tuesday = 1, Wednesday = 2, Thursday = 3
    - Friday = 4 (выходной в Израиле)
    - Saturday = 5 (выходной в Израиле)
    - Sunday = 6 (рабочий день в Израиле!)
    
    Рабочие дни: понедельник (0), вторник (1), среда (2), четверг (3), воскресенье (6)
    Выходные: пятница (4), суббота (5)
    """
    prev_day = from_date - timedelta(days=1)
    
    # Пропускаем выходные: пятница (4) и суббота (5)
    # НЕ пропускаем воскресенье (6) - это рабочий день в Израиле!
    while prev_day.weekday() in (4, 5):  # Friday (4) or Saturday (5)
        prev_day = prev_day - timedelta(days=1)
    
    return prev_day


def get_next_workday(from_date: date) -> date:
    """
    Возвращает следующий рабочий день с учетом израильских выходных (пятница, суббота)
    """
    next_day = from_date + timedelta(days=1)
    # Пятница (4) → воскресенье (+2 дня)
    if next_day.weekday() == 4:  # Friday
        return next_day + timedelta(days=2)
    # Суббота (5) → воскресенье (+1 день)
    elif next_day.weekday() == 5:  # Saturday
        return next_day + timedelta(days=1)
    return next_day


async def get_absences_for_date(target_date: date, db: Session) -> list:
    """
    Получает отпуска и отсутствия на указанную дату НАПРЯМУЮ из БД
    
    Returns:
        [
            {
                'employee_name': str,
                'role': str,
                'reason': str,
                'start_date': str,
                'end_date': str,
                'days_remaining': int
            }
        ]
    """
    try:
        # Прямой SQL запрос к таблице calendar_requests
        query = text("""
            SELECT 
                cr.id,
                e.full_name as employee_name,
                'Сотрудник' as role_name,
                crt.name as request_type_name,
                cr.start_date,
                cr.end_date,
                cr.status
            FROM calendar_requests cr
            JOIN employees e ON e.id = cr.employee_id
            JOIN calendar_request_types crt ON crt.id = cr.request_type_id
            WHERE cr.status = 'approved'
              AND cr.start_date <= :target_date
              AND cr.end_date >= :target_date
              AND e.is_active = true
            ORDER BY e.full_name
        """)
        
        result = db.execute(query, {'target_date': target_date}).fetchall()
        
        absences = []
        for row in result:
            # Вычисляем оставшиеся дни
            end_date = row.end_date
            days_remaining = (end_date - target_date).days
            
            absences.append({
                'employee_name': row.employee_name,
                'role': row.role_name,
                'reason': row.request_type_name,
                'start_date': row.start_date.isoformat(),
                'end_date': row.end_date.isoformat(),
                'days_remaining': max(0, days_remaining)
            })
        
        return absences
        
    except Exception as e:
        logger.error(f"Error getting absences from DB: {e}", exc_info=True)
        return []


@router.get("/morning/summary")
async def get_morning_summary(
    target_date: str = None,  # Формат: YYYY-MM-DD
    db: Session = Depends(get_db_session)
):
    """
    Полная утренняя сводка - все ключевые метрики
    
    Параметры:
    - target_date: дата для отчета (по умолчанию: сегодня)
    """
    try:
        # Определяем целевую дату
        if target_date:
            report_date = datetime.strptime(target_date, '%Y-%m-%d').date()
        else:
            report_date = date.today()
        
        # Следующий рабочий день с учетом израильских выходных
        next_workday = get_next_workday(report_date)
        
        # Получаем все данные
        discrepancies = await get_acceptance_discrepancies(db)
        defect_rates = await get_defect_rates(db)
        operator_rework = await get_operator_rework_stats(report_date, db)
        
        # Получаем отпуска и отсутствия НАПРЯМУЮ из БД
        absences_today = await get_absences_for_date(report_date, db)
        absences_tomorrow = await get_absences_for_date(next_workday, db)
        
        # Сводная статистика
        total_discrepancy = sum(d.discrepancy_absolute for d in discrepancies)
        critical_count = sum(1 for d in discrepancies if d.status == 'critical')
        avg_defect_rate = sum(d.defect_percent for d in defect_rates) / len(defect_rates) if defect_rates else 0
        
        return {
            "timestamp": datetime.now(timezone.utc),
            "report_date": report_date.isoformat(),
            "next_workday": next_workday.isoformat(),
            "acceptance_discrepancies": discrepancies,
            "defect_rates": defect_rates,
            "operator_rework_stats": operator_rework,
            "absences_today": absences_today,
            "absences_tomorrow": absences_tomorrow,
            "summary_stats": {
                "total_discrepancy_parts": total_discrepancy,
                "critical_discrepancies_count": critical_count,
                "average_defect_rate": round(avg_defect_rate, 2),
                "operator_rework_batches": operator_rework['total_batches'],
                "operator_rework_parts": operator_rework['total_parts']
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting morning summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

