# -*- coding: utf-8 -*-
"""
@file: batches.py
@description: FastAPI router for batch management operations
@dependencies: FastAPI, SQLAlchemy, database models, batch schemas
@created: 2024
"""

import asyncio
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db_session
from ..models.models import (
    BatchDB, LotDB, PartDB, EmployeeDB, MachineDB, SetupDB, CardDB
)
from ..schemas.batch import (
    BatchViewItem, StartInspectionPayload, InspectBatchPayload,
    BatchMergePayload, BatchMovePayload, WarehousePendingBatchItem,
    AcceptWarehousePayload, CreateBatchInput, CreateBatchResponse
)
from ..schemas.machine import BatchLabelInfo
from ..services.notification_service import send_batch_discrepancy_alert
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


# === BATCH VIEWING ENDPOINTS ===

@router.get("/lots/{lot_id}/batches", response_model=List[BatchViewItem])
async def get_batches_for_lot(lot_id: int, db: Session = Depends(get_db_session)):
    """Вернуть ВСЕ НЕАРХИВНЫЕ батчи для указанного лота."""
    try:
        # Убираем фильтр по otk_visible_locations, оставляем только != 'archived'
        batches = db.query(BatchDB, PartDB, LotDB, EmployeeDB).select_from(BatchDB) \
            .join(LotDB, BatchDB.lot_id == LotDB.id) \
            .join(PartDB, LotDB.part_id == PartDB.id) \
            .outerjoin(EmployeeDB, BatchDB.operator_id == EmployeeDB.id) \
            .filter(BatchDB.lot_id == lot_id) \
            .filter(BatchDB.current_location != 'archived') \
            .all()

        result = []
        for row in batches:
            batch_obj, part_obj, lot_obj, emp_obj = row
            result.append({
                'id': batch_obj.id,
                'lot_id': lot_id,
                'drawing_number': part_obj.drawing_number if part_obj else None,
                'lot_number': lot_obj.lot_number if lot_obj else None,
                'current_quantity': batch_obj.current_quantity,
                'current_location': batch_obj.current_location,
                'batch_time': batch_obj.batch_time,
                'warehouse_received_at': batch_obj.warehouse_received_at, 
                'operator_name': emp_obj.full_name if emp_obj else None, 
            })
        return result
    except Exception as e:
        logger.error(f"Error fetching batches for lot {lot_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching batches")


# === BATCH INSPECTION ENDPOINTS ===

@router.post("/batches/{batch_id}/start-inspection")
async def start_batch_inspection(batch_id: int, payload: StartInspectionPayload, db: Session = Depends(get_db_session)):
    """Пометить батч как начатый к инспекции."""
    try:
        batch = db.query(BatchDB).filter(BatchDB.id == batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        if batch.current_location != 'warehouse_counted':
            raise HTTPException(status_code=400, detail="Batch cannot be inspected in its current state")
        batch.current_location = 'inspection'
        db.commit()
        db.refresh(batch)
        return {'success': True, 'batch': batch}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error starting inspection for batch {batch_id}: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error while starting inspection")


@router.post("/batches/{batch_id}/inspect")
async def inspect_batch(batch_id: int, payload: InspectBatchPayload, db: Session = Depends(get_db_session)):
    """Разделить батч на good / defect / rework."""
    try:
        batch = db.query(BatchDB).filter(BatchDB.id == batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        if batch.current_location not in ['inspection', 'warehouse_counted']:
            raise HTTPException(status_code=400, detail="Batch is not in inspection state")

        total_requested = payload.good_quantity + payload.rejected_quantity + payload.rework_quantity
        if total_requested > batch.current_quantity:
            raise HTTPException(status_code=400, detail="Sum of quantities exceeds batch size")

        # Архивируем исходный батч
        batch.current_location = 'archived'

        created_batches = []
        def _create_child(qty: int, location: str):
            if qty <= 0:
                return None
            child = BatchDB(
                setup_job_id=batch.setup_job_id,
                lot_id=batch.lot_id,
                initial_quantity=qty,
                current_quantity=qty,
                recounted_quantity=None,
                current_location=location,
                operator_id=payload.inspector_id,
                parent_batch_id=batch.id,
                batch_time=datetime.now(),
            )
            db.add(child)
            db.flush()
            created_batches.append(child)
            return child

        _create_child(payload.good_quantity, 'good')
        _create_child(payload.rejected_quantity, 'defect')
        _create_child(payload.rework_quantity, 'rework_repair')

        db.commit()

        return {
            'success': True,
            'created_batch_ids': [b.id for b in created_batches]
        }
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error inspecting batch {batch_id}: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error while inspecting batch")


# === BATCH OPERATIONS ENDPOINTS ===

@router.post("/batches/merge")
async def merge_batches(payload: BatchMergePayload, db: Session = Depends(get_db_session)):
    """Слить несколько батчей в один."""
    try:
        if len(payload.batch_ids) < 2:
            raise HTTPException(status_code=400, detail="Need at least two batches to merge")
        batches = db.query(BatchDB).filter(BatchDB.id.in_(payload.batch_ids)).all()
        if len(batches) != len(payload.batch_ids):
            raise HTTPException(status_code=404, detail="Some batches not found")
        lot_ids = set(b.lot_id for b in batches)
        if len(lot_ids) != 1:
            raise HTTPException(status_code=400, detail="Batches belong to different lots")

        total_qty = sum(b.current_quantity for b in batches)
        new_batch = BatchDB(
            setup_job_id=batches[0].setup_job_id,
            lot_id=batches[0].lot_id,
            initial_quantity=total_qty,
            current_quantity=total_qty,
            current_location=payload.target_location,
            operator_id=None,
            parent_batch_id=None,
            batch_time=datetime.now()
        )
        db.add(new_batch)
        db.flush()

        for b in batches:
            b.current_location = 'archived'
        db.commit()
        return {'success': True, 'new_batch_id': new_batch.id}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error merging batches: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error while merging batches")


@router.post("/batches/{batch_id}/move", response_model=BatchViewItem)
async def move_batch(
    batch_id: int, 
    payload: BatchMovePayload, 
    db: Session = Depends(get_db_session)
):
    """Переместить батч в новую локацию."""
    try:
        batch = db.query(BatchDB).filter(BatchDB.id == batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        
        if batch.current_location == 'archived':
            raise HTTPException(status_code=400, detail="Cannot move an archived batch")

        target_location = payload.target_location.strip()
        if not target_location:
            raise HTTPException(status_code=400, detail="Target location cannot be empty")

        # Получить связанные данные для ответа
        related_data = db.query(BatchDB, PartDB, LotDB, EmployeeDB)\
            .select_from(BatchDB)\
            .join(LotDB, BatchDB.lot_id == LotDB.id)\
            .join(PartDB, LotDB.part_id == PartDB.id)\
            .outerjoin(EmployeeDB, BatchDB.operator_id == EmployeeDB.id)\
            .filter(BatchDB.id == batch_id)\
            .first()

        if not related_data:
            raise HTTPException(status_code=404, detail="Related batch data not found")

        batch_obj, part_obj, lot_obj, emp_obj = related_data

        # Выполнить перемещение
        batch.current_location = target_location
        if payload.inspector_id:
            batch.operator_id = payload.inspector_id

        db.commit()
        db.refresh(batch)

        # Вернуть обновленные данные в формате BatchViewItem
        return BatchViewItem(
            id=batch.id,
            lot_id=batch.lot_id,
            drawing_number=part_obj.drawing_number if part_obj else None,
            lot_number=lot_obj.lot_number if lot_obj else None,
            current_quantity=batch.current_quantity,
            current_location=batch.current_location,
            batch_time=batch.batch_time,
            warehouse_received_at=batch.warehouse_received_at,
            operator_name=emp_obj.full_name if emp_obj else None
        )

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error moving batch {batch_id}: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error while moving batch")


# === WAREHOUSE ENDPOINTS ===

@router.get("/warehouse/batches-pending", response_model=List[WarehousePendingBatchItem])
async def get_warehouse_pending_batches(db: Session = Depends(get_db_session)):
    """Получить список батчей, ожидающих приемки на склад (статус 'production' или 'sorting')."""
    try:
        batches = (
            db.query(BatchDB, PartDB, LotDB, EmployeeDB, MachineDB, CardDB)
            .select_from(BatchDB)
            .join(LotDB, BatchDB.lot_id == LotDB.id)
            .join(PartDB, LotDB.part_id == PartDB.id)
            .outerjoin(EmployeeDB, BatchDB.operator_id == EmployeeDB.id)
            .outerjoin(SetupDB, BatchDB.setup_job_id == SetupDB.id)
            .outerjoin(MachineDB, SetupDB.machine_id == MachineDB.id)
            .outerjoin(CardDB, BatchDB.id == CardDB.batch_id)
            .filter(BatchDB.current_location.in_(['production', 'sorting']))
            .order_by(BatchDB.batch_time.asc())
            .all()
        )

        result = []
        for row in batches:
            batch_obj, part_obj, lot_obj, emp_obj, machine_obj, card_obj = row
            item_data = {
                'id': batch_obj.id,
                'lot_id': batch_obj.lot_id,
                'drawing_number': part_obj.drawing_number if part_obj else None,
                'lot_number': lot_obj.lot_number if lot_obj else None,
                'current_quantity': batch_obj.current_quantity,
                'batch_time': batch_obj.batch_time,
                'operator_name': emp_obj.full_name if emp_obj else None,
                'machine_name': machine_obj.name if machine_obj else None,
                'card_number': card_obj.card_number if card_obj else None
            }
            result.append(WarehousePendingBatchItem.model_validate(item_data))
        return result
    except Exception as e:
        logger.error(f"Error fetching warehouse pending batches: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching pending batches")


@router.post("/batches/{batch_id}/accept-warehouse")
async def accept_batch_on_warehouse(batch_id: int, payload: AcceptWarehousePayload, db: Session = Depends(get_db_session)):
    """Принять батч на склад: обновить кол-во и статус."""
    try:
        # Получаем батч и связанные сущности одним запросом для эффективности
        batch_data = db.query(BatchDB, LotDB, PartDB)\
            .join(LotDB, BatchDB.lot_id == LotDB.id)\
            .join(PartDB, LotDB.part_id == PartDB.id)\
            .filter(BatchDB.id == batch_id)\
            .first()

        if not batch_data:
            raise HTTPException(status_code=404, detail="Batch not found or related Lot/Part missing")
        
        batch, lot, part = batch_data

        if batch.current_location not in ['production', 'sorting']:
            raise HTTPException(status_code=400, detail=f"Batch is not in an acceptable state for warehouse acceptance (current: {batch.current_location}). Expected 'production' or 'sorting'.")
        
        warehouse_employee = db.query(EmployeeDB).filter(EmployeeDB.id == payload.warehouse_employee_id).first()
        if not warehouse_employee:
            raise HTTPException(status_code=404, detail="Warehouse employee not found")

        if warehouse_employee.role_id not in [3, 6]: # Предполагаем ID 3=admin, 6=warehouse
             logger.warning(f"Employee {payload.warehouse_employee_id} with role {warehouse_employee.role_id} tried to accept batch.")
             raise HTTPException(status_code=403, detail="Insufficient permissions for warehouse acceptance")

        # Получаем оператора производства ДО перезаписи operator_id
        original_operator_id = batch.operator_id
        original_operator = db.query(EmployeeDB).filter(EmployeeDB.id == original_operator_id).first() if original_operator_id else None

        # Сохраняем кол-во от оператора и вводим кол-во кладовщика
        operator_reported_qty = batch.current_quantity # Это кол-во ДО приемки
        recounted_clerk_qty = payload.recounted_quantity
        
        # Записываем исторические данные
        batch.operator_reported_quantity = operator_reported_qty
        batch.recounted_quantity = recounted_clerk_qty
        
        # Рассчитываем и сохраняем расхождения
        batch.discrepancy_absolute = None
        batch.discrepancy_percentage = None 
        batch.admin_acknowledged_discrepancy = False
        notification_task = None

        if operator_reported_qty is not None: # Если было какое-то кол-во от оператора
            difference = recounted_clerk_qty - operator_reported_qty
            batch.discrepancy_absolute = difference
            
            if operator_reported_qty != 0:
                percentage_diff = abs(difference / operator_reported_qty) * 100
                batch.discrepancy_percentage = round(percentage_diff, 2)

                if percentage_diff > 5.0:
                    logger.warning(
                        f"Critical discrepancy for batch {batch.id}: "
                        f"Operator Qty: {operator_reported_qty}, "
                        f"Clerk Qty: {recounted_clerk_qty}, "
                        f"Diff: {difference} ({percentage_diff:.2f}%)"
                    )
                    
                    discrepancy_details = {
                        "batch_id": batch.id,
                        "drawing_number": part.drawing_number if part else 'N/A',
                        "lot_number": lot.lot_number if lot else 'N/A',
                        "operator_name": original_operator.full_name if original_operator else 'Неизвестный оператор',
                        "warehouse_employee_name": warehouse_employee.full_name,
                        "original_qty": operator_reported_qty,
                        "recounted_qty": recounted_clerk_qty,
                        "discrepancy_abs": difference,
                        "discrepancy_perc": round(percentage_diff, 2)
                    }
                    notification_task = asyncio.create_task(
                        send_batch_discrepancy_alert(db=db, discrepancy_details=discrepancy_details)
                    )

        # Обновляем основные поля батча
        batch.current_quantity = recounted_clerk_qty # Актуальное кол-во теперь = пересчитанному кладовщиком
        batch.current_location = 'warehouse_counted'
        batch.warehouse_employee_id = payload.warehouse_employee_id
        batch.warehouse_received_at = datetime.now()
        batch.operator_id = payload.warehouse_employee_id # Обновляем оператора на кладовщика
        batch.updated_at = datetime.now() 

        db.commit()
        db.refresh(batch)

        logger.info(f"Batch {batch_id} accepted on warehouse by employee {payload.warehouse_employee_id} with quantity {payload.recounted_quantity}")
        
        return {'success': True, 'message': 'Batch accepted successfully'}

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error accepting batch {batch_id} on warehouse: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error while accepting batch")


# === BATCH CREATION ENDPOINTS ===

@router.post("/batches", response_model=CreateBatchResponse)
async def create_batch(payload: CreateBatchInput, db: Session = Depends(get_db_session)):
    """
    Создать новый батч (в том числе для переборки). batch_quantity=None, статус по умолчанию 'sorting'.
    """
    lot = db.query(LotDB).filter(LotDB.id == payload.lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    part = db.query(PartDB).filter(PartDB.drawing_number == payload.drawing_number).first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    machine = db.query(MachineDB).filter(MachineDB.id == payload.machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    operator = db.query(EmployeeDB).filter(EmployeeDB.id == payload.operator_id).first()
    if not operator:
        raise HTTPException(status_code=404, detail="Operator not found")

    now = datetime.now()
    hour = now.hour
    shift = "1" if 6 <= hour < 18 else "2"

    new_batch = BatchDB(
        lot_id=payload.lot_id,
        initial_quantity=0, # ставим 0 по умолчанию для батчей 'sorting'
        current_quantity=0, # ставим 0 по умолчанию для батчей 'sorting'
        recounted_quantity=None,
        current_location=payload.status or 'sorting',
        operator_id=payload.operator_id,
        batch_time=now,
        created_at=now
    )
    db.add(new_batch)
    db.commit()
    db.refresh(new_batch)

    return CreateBatchResponse(
        batch_id=new_batch.id,
        lot_number=lot.lot_number,
        drawing_number=part.drawing_number,
        machine_name=machine.name,
        operator_id=operator.id,
        created_at=now,
        shift=shift
    )


# === DEBUG/UTILITY ENDPOINTS ===

@router.get("/debug/batches-summary")
async def get_batches_summary(db: Session = Depends(get_db_session)):
    """Отладочная информация о батчах."""
    try:
        total_batches = db.query(BatchDB).count()
        batch_by_location = {}
        
        locations = db.query(BatchDB.current_location).distinct().all()
        for location_tuple in locations:
            location = location_tuple[0]
            count = db.query(BatchDB).filter(BatchDB.current_location == location).count()
            batch_by_location[location] = count

        return {
            "total_batches": total_batches,
            "by_location": batch_by_location
        }
    except Exception as e:
        logger.error(f"Error generating batches summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while generating summary")


# === BATCH LABEL INFO ENDPOINT ===

@router.get("/batches/{batch_id}/label-info", response_model=BatchLabelInfo)
async def get_batch_label_info(batch_id: int, db: Session = Depends(get_db_session)):
    """Получить информацию для лейбла батча."""
    try:
        batch_query = (
            db.query(BatchDB, LotDB, PartDB, SetupDB, MachineDB, EmployeeDB)
            .join(LotDB, BatchDB.lot_id == LotDB.id)
            .join(PartDB, LotDB.part_id == PartDB.id)
            .outerjoin(SetupDB, BatchDB.setup_job_id == SetupDB.id)
            .outerjoin(MachineDB, SetupDB.machine_id == MachineDB.id)
            .outerjoin(EmployeeDB, BatchDB.operator_id == EmployeeDB.id)
            .filter(BatchDB.id == batch_id)
            .first()
        )

        if not batch_query:
            raise HTTPException(status_code=404, detail="Batch not found")

        batch, lot, part, setup, machine, operator = batch_query

        return BatchLabelInfo(
            batch_id=batch.id,
            lot_number=lot.lot_number,
            drawing_number=part.drawing_number,
            quantity=batch.current_quantity,
            machine_name=machine.name if machine else "Unknown",
            operator_name=operator.full_name if operator else "Unknown",
            created_at=batch.batch_time or batch.created_at
        )

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error fetching batch label info for batch {batch_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching batch label info") 