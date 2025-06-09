from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
import logging

from src.database import get_db_session
from src.models.models import MachineDB, ReadingDB, SetupDB, BatchDB, LotDB, PartDB, EmployeeDB
from src.schemas.machine import MachineItem, OperatorMachineViewItem, BatchLabelInfo, BatchAvailabilityInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/machines", tags=["Machines"])

@router.get("/", response_model=List[MachineItem])
async def get_machines(db: Session = Depends(get_db_session)):
    """
    Получить список всех станков.
    """
    try:
        machines = db.query(MachineDB).all()
        logger.info(f"Запрос списка станков. Возвращено {len(machines)} станков.")
        return machines
    except Exception as e:
        logger.error(f"Ошибка при получении списка станков: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при получении списка станков: {str(e)}")

@router.get("/operator-view", response_model=List[OperatorMachineViewItem])
async def get_operator_machines_view(db: Session = Depends(get_db_session)):
    """
    Получить представление станков для оператора с последними показаниями и статусом наладки.
    """
    try:
        # Получаем все станки
        machines = db.query(MachineDB).all()
        result = []
        
        for machine in machines:
            # Получаем последнее показание
            last_reading = db.query(ReadingDB)\
                .filter(ReadingDB.machine_id == machine.id)\
                .order_by(ReadingDB.timestamp.desc())\
                .first()
            
            # Получаем текущую наладку
            current_setup = db.query(SetupDB)\
                .filter(SetupDB.machine_id == machine.id)\
                .filter(SetupDB.status.in_(['pending', 'in_progress']))\
                .order_by(SetupDB.id.desc())\
                .first()
            
            machine_view = OperatorMachineViewItem(
                id=machine.id,
                name=machine.name,
                reading='',
                lastReading=last_reading.value if last_reading else None,
                lastReadingTime=last_reading.timestamp if last_reading else None,
                setupId=current_setup.id if current_setup else None,
                drawingNumber=current_setup.drawing_number if current_setup else None,
                plannedQuantity=current_setup.planned_quantity if current_setup else None,
                additionalQuantity=current_setup.additional_quantity if current_setup else None,
                status=current_setup.status if current_setup else None
            )
            result.append(machine_view)
        
        logger.info(f"Запрос представления станков для оператора. Возвращено {len(result)} станков.")
        return result
    except Exception as e:
        logger.error(f"Ошибка при получении представления станков для оператора: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")

@router.get("/{machine_id}/active-batch-label", response_model=BatchLabelInfo)
async def get_active_batch_label(machine_id: int, db: Session = Depends(get_db_session)):
    """
    Получить информацию для печати этикетки активного батча на станке.
    """
    try:
        # Получаем активный батч в статусе 'production'
        batch = db.query(BatchDB)\
            .join(SetupDB)\
            .join(LotDB)\
            .join(PartDB)\
            .join(EmployeeDB, BatchDB.operator_id == EmployeeDB.id)\
            .join(MachineDB, SetupDB.machine_id == MachineDB.id)\
            .filter(SetupDB.machine_id == machine_id)\
            .filter(BatchDB.status == 'production')\
            .order_by(BatchDB.id.desc())\
            .first()
        
        if not batch:
            raise HTTPException(status_code=404, detail=f"Активный батч для станка {machine_id} не найден")
        
        # Получаем связанные данные
        setup = batch.setup
        lot = setup.lot
        part = lot.part
        operator = batch.operator
        machine = setup.machine
        
        # Определяем смену
        hour = batch.batch_time.hour if batch.batch_time else datetime.now().hour
        shift = "1" if 8 <= hour < 20 else "2"
        
        batch_label = BatchLabelInfo(
            id=batch.id,
            lot_id=lot.id,
            drawing_number=part.drawing_number,
            lot_number=lot.lot_number,
            machine_name=machine.name,
            operator_name=operator.full_name,
            operator_id=operator.id,
            batch_time=batch.batch_time,
            shift=shift,
            start_time=setup.start_time.strftime("%H:%M") if setup.start_time else None,
            end_time=setup.end_time.strftime("%H:%M") if setup.end_time else None,
            initial_quantity=setup.planned_quantity + (setup.additional_quantity or 0),
            current_quantity=batch.current_quantity,
            batch_quantity=batch.initial_quantity,
            warehouse_received_at=batch.warehouse_received_at,
            warehouse_employee_name=batch.warehouse_employee.full_name if batch.warehouse_employee else None,
            recounted_quantity=batch.recounted_quantity
        )
        
        logger.info(f"Получена информация для этикетки батча {batch.id} на станке {machine_id}")
        return batch_label
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при получении информации для этикетки батча на станке {machine_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")

@router.get("/{machine_id}/batch-availability", response_model=BatchAvailabilityInfo)
async def get_batch_availability(machine_id: int, db: Session = Depends(get_db_session)):
    """
    Проверить доступность печати этикеток для станка.
    """
    try:
        # Получаем станок
        machine = db.query(MachineDB).filter(MachineDB.id == machine_id).first()
        if not machine:
            raise HTTPException(status_code=404, detail=f"Станок с ID {machine_id} не найден")
        
        # Проверяем активный батч в production
        active_batch = db.query(BatchDB)\
            .join(SetupDB)\
            .filter(SetupDB.machine_id == machine_id)\
            .filter(BatchDB.status == 'production')\
            .order_by(BatchDB.id.desc())\
            .first()
        
        # Проверяем любой батч (для переборки)
        any_batch = db.query(BatchDB)\
            .join(SetupDB)\
            .filter(SetupDB.machine_id == machine_id)\
            .order_by(BatchDB.id.desc())\
            .first()
        
        # Получаем данные последнего батча если есть
        last_batch_data = None
        if any_batch:
            try:
                # Используем тот же код что и в get_active_batch_label
                setup = any_batch.setup
                lot = setup.lot
                part = lot.part
                operator = any_batch.operator
                
                hour = any_batch.batch_time.hour if any_batch.batch_time else datetime.now().hour
                shift = "1" if 8 <= hour < 20 else "2"
                
                last_batch_data = BatchLabelInfo(
                    id=any_batch.id,
                    lot_id=lot.id,
                    drawing_number=part.drawing_number,
                    lot_number=lot.lot_number,
                    machine_name=machine.name,
                    operator_name=operator.full_name,
                    operator_id=operator.id,
                    batch_time=any_batch.batch_time,
                    shift=shift,
                    start_time=setup.start_time.strftime("%H:%M") if setup.start_time else None,
                    end_time=setup.end_time.strftime("%H:%M") if setup.end_time else None,
                    initial_quantity=setup.planned_quantity + (setup.additional_quantity or 0),
                    current_quantity=any_batch.current_quantity,
                    batch_quantity=any_batch.initial_quantity,
                    warehouse_received_at=any_batch.warehouse_received_at,
                    warehouse_employee_name=any_batch.warehouse_employee.full_name if any_batch.warehouse_employee else None,
                    recounted_quantity=any_batch.recounted_quantity
                )
            except Exception as e:
                logger.warning(f"Ошибка при формировании данных последнего батча для станка {machine_id}: {e}")
                last_batch_data = None
        
        availability = BatchAvailabilityInfo(
            machine_id=machine_id,
            machine_name=machine.name,
            has_active_batch=active_batch is not None,
            has_any_batch=any_batch is not None,
            last_batch_data=last_batch_data
        )
        
        logger.info(f"Проверена доступность печати этикеток для станка {machine_id}: активный={availability.has_active_batch}, любой={availability.has_any_batch}")
        return availability
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при проверке доступности печати этикеток для станка {machine_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}") 